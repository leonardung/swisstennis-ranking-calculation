"""Recompute a published ranking list from scraped matches and compare with the published values."""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import ranking

# DCL Art. 8: periods run 1 April – 30 September and 1 October – 31 March;
# a list uses the results of the two periods (one year) before it.
PERIOD_START_MONTHS = (4, 10)

CATEGORIES = ["N1", "N2", "N3", "N4", "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9"]

# playerWinnerCode: S/N won/lost, W/Z won/lost by retirement (count, Art. 8.5).
# 0/1 are walkovers without a point played and D (not fetched in practice) do not count.
WIN_CODES = ["S", "W"]
LOSS_CODES = ["N", "Z"]

# Minimum start value for players with matches (old KR: "Mindestausgangswert 1"); new players
# start here too. Players without matches are not raised to it: they keep w0 (floor 0.75).
MIN_START_VALUE = 1.0


@dataclass
class Period:
    publication: pd.Timestamp
    start: pd.Timestamp
    end: pd.Timestamp
    players: pd.DataFrame  # one row per player, index = player number used by ranking.compute
    matches: pd.DataFrame  # player, opp, opp_fixed, win


def publications(history: pd.DataFrame, min_players: int = 1000) -> list[pd.Timestamp]:
    """Dates of full ranking lists (ignores individual corrections published on other dates)."""
    counts = history.groupby(history["date"].dt.normalize())["personId"].nunique()
    return list(counts[counts >= min_players].index)


def window_end(publication: pd.Timestamp) -> pd.Timestamp:
    """Latest period boundary (1 April or 1 October) on or before the publication date."""
    year = publication.year
    bounds = [pd.Timestamp(y, m, 1) for y in (year - 1, year) for m in PERIOD_START_MONTHS]
    return max(b for b in bounds if b <= publication.normalize())


def build_period(
    history: pd.DataFrame, matches: pd.DataFrame, players: pd.DataFrame, publication: pd.Timestamp
) -> Period:
    end = window_end(publication)
    start = end - pd.DateOffset(years=1)
    day = history["date"].dt.normalize()

    target = history[day == publication].drop_duplicates("personId", keep="last")
    prev = history[day < end].sort_values("date").drop_duplicates("personId", keep="last")

    m = matches[(matches["date"] >= start) & (matches["date"] < end)]
    m = m[m["playerWinnerCode"].isin(WIN_CODES + LOSS_CODES)]
    # Tournament results have no encounterId (NaN) and pending ones 0: only dedupe real ids.
    m = m[m["encounterId"].fillna(0).eq(0) | ~m.duplicated(["personId", "encounterId"])]

    ids = pd.Index(sorted(set(target["personId"]) | set(prev["personId"]) | set(m["personId"])))
    p = pd.DataFrame({"personId": ids})
    p = p.merge(prev[["personId", "W"]].rename(columns={"W": "W5_prev"}), how="left")
    p = p.merge(
        target[["personId", "firstname", "lastname", "classification", "rank", "W", "C", "games"]].rename(
            columns={"W": "W_pub", "C": "C_pub", "classification": "class_pub", "rank": "rank_pub"}
        ),
        how="left",
    )
    p["gender"] = p["personId"].map(genders(players, matches))

    rows = pd.DataFrame(
        {
            "player": ids.get_indexer(m["personId"]),
            "opp": ids.get_indexer(m["adversaryPersonId"].fillna(-1).astype(int)),
            "opp_fixed": m["adversaryValue"].to_numpy(dtype=float),
            "win": m["playerWinnerCode"].isin(WIN_CODES).to_numpy(),
        }
    )
    # Opponents outside the calculation need the value stored with the result.
    rows = rows[(rows["opp"] >= 0) | (rows["opp_fixed"].fillna(0) != 0)].reset_index(drop=True)

    p["n_matches"] = np.bincount(rows["player"], minlength=len(p))
    return Period(publication, start, end, p, rows)


def genders(players: pd.DataFrame, matches: pd.DataFrame) -> pd.Series:
    """Gender per personId: from the licence list, else from their opponents over all matches
    (singles are played within a gender; unlicensed players are not in the licence list)."""
    known = players.set_index("personId")["gender"]
    m = matches[["personId", "adversaryPersonId"]].dropna()
    m = m.assign(gender=m["personId"].map(known)).dropna()
    inferred = (
        m.groupby(["adversaryPersonId", "gender"]).size().reset_index().sort_values(0)
        .drop_duplicates("adversaryPersonId", keep="last")
        .set_index("adversaryPersonId")["gender"]
    )
    inferred.index = inferred.index.astype(int)
    return known.combine_first(inferred)


def assign_categories(p: pd.DataFrame) -> pd.Series:
    """Category our C would get on the published list: the best category whose lowest
    published C (per gender) we reach.

    Ranking by the Art. 3 quotas does not reproduce the list: foreign players get a rank but no
    quota slot (Art. 9.2), and Swiss Tennis adapts the quotas; the published thresholds include both.
    """
    out = pd.Series(pd.NA, index=p.index, dtype="object")
    listed = p[p["C_pub"].notna()]
    for gender, g in listed.groupby("gender"):
        lowest = g.groupby("class_pub")["C_pub"].min().reindex(CATEGORIES).dropna()
        thresholds, names = lowest.to_numpy()[:-1], lowest.index[:-1]  # everyone else: last category
        best = (g["C"].to_numpy()[:, None] >= thresholds[None, :]).argmax(axis=1)
        reached = (g["C"].to_numpy()[:, None] >= thresholds[None, :]).any(axis=1)
        out.loc[g.index] = np.where(reached, names.to_numpy()[best], lowest.index[-1])
    return out


def w0_table(p: pd.DataFrame) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Per gender, the W5 -> w0 conversion used for this list, as sorted (W5, w0) points.

    Swiss Tennis does not publish how w0 is derived from W5. Players without matches in the
    window have W = w0, so their (W5, published W) pairs reveal the conversion exactly; it is
    a monotone per-gender function (piecewise, ratio ~0.83-0.92, floored at 0.75).
    """
    inactive = p[(p["n_matches"] == 0) & p["W5_prev"].notna() & p["W_pub"].notna()]
    table = {}
    for gender, g in inactive.groupby("gender"):
        pts = g.groupby("W5_prev")["W_pub"].median()
        table[gender] = (pts.index.to_numpy(), pts.to_numpy())
    return table


def start_values(p: pd.DataFrame, table: dict[str, tuple[np.ndarray, np.ndarray]]) -> pd.Series:
    """w0 per player: interpolate W5 in the list's conversion table.

    Above the highest inactive player the last ratio w0/W5 is extrapolated.
    Players with matches start at MIN_START_VALUE or higher; new players at MIN_START_VALUE.
    """
    w0 = pd.Series(MIN_START_VALUE, index=p.index)
    for gender, (x, y) in table.items():
        sel = (p["gender"] == gender) & p["W5_prev"].notna()
        w5 = p.loc[sel, "W5_prev"].to_numpy()
        w0[sel] = np.where(w5 > x[-1], w5 * y[-1] / x[-1], np.interp(w5, x, y))
    return w0.where(p["n_matches"] == 0, w0.clip(lower=MIN_START_VALUE))


def evaluate(period: Period) -> pd.DataFrame:
    p = period.players.copy()
    p["w0"] = start_values(p, w0_table(p))
    r = period.matches
    W, R = ranking.compute(
        p["w0"].to_numpy(),
        r["player"].to_numpy(),
        r["opp"].to_numpy(),
        r["opp_fixed"].to_numpy(),
        r["win"].to_numpy(),
    )
    p["W"], p["R"] = W.round(3), R.round(3)
    p["C"] = (p["W"] + p["R"]).round(3)
    p["R_pub"] = p["C_pub"] - p["W_pub"]
    p["class"] = assign_categories(p)
    return p


def report(p: pd.DataFrame) -> str:
    q = p[p["C_pub"].notna()]
    lines = [f"players on published list: {len(q)}"]
    for label, sub in [
        ("all", q),
        ("with previous value", q[q["W5_prev"].notna()]),
        ("new players", q[q["W5_prev"].isna()]),
        ("no matches", q[q["n_matches"] == 0]),
        ("with matches", q[q["n_matches"] > 0]),
    ]:
        if sub.empty:
            continue
        lines.append(
            f"  {label:<20} n={len(sub):>6}  MAE W={np.abs(sub.W - sub.W_pub).mean():.4f}  "
            f"R={np.abs(sub.R - sub.R_pub).mean():.4f}  C={np.abs(sub.C - sub.C_pub).mean():.4f}  "
            f"exact C={np.mean(np.abs(sub.C - sub.C_pub) < 0.0015):.1%}  "
            f"same category={np.mean(sub['class'] == sub['class_pub']):.1%}"
        )
    by_class = (
        q.assign(err=np.abs(q.C - q.C_pub), same=q["class"] == q["class_pub"])
        .groupby("class_pub")
        .agg(n=("err", "size"), MAE_C=("err", "mean"), same_cat=("same", "mean"))
    )
    order = [c for c in CATEGORIES if c in by_class.index]
    lines.append(by_class.reindex(order).round(4).to_string())
    return "\n".join(lines)


def calibrate(period: Period) -> pd.DataFrame:
    """Recover each player's w0 from the published values.

    - No matches in the window: W = w0, so the published W *is* w0.
    - Otherwise: invert the formula with opponents valued at their published W
      (the published W is W5, which is what opponents converge to).
    Returns personId, W5_prev, n_matches and the recovered w0 for players with a previous value.
    """
    p = period.players
    r = period.matches
    known = r["opp"].to_numpy() >= 0
    W_pub = p["W_pub"].to_numpy()
    opp_w = np.where(known, W_pub[np.where(known, r["opp"], 0)], r["opp_fixed"])
    ok = ~np.isnan(opp_w)
    w0 = ranking.invert_w0(
        np.nan_to_num(W_pub), r["player"].to_numpy()[ok], opp_w[ok], r["win"].to_numpy()[ok]
    )
    out = p[["personId", "gender", "W5_prev", "W_pub", "n_matches"]].assign(w0=w0)
    return out[out["W5_prev"].notna() & out["W_pub"].notna()]
