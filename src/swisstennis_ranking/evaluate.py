"""Recompute a published ranking list from scraped matches and compare with the published values."""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import ranking

# DCL Art. 8: periods run 1 April – 30 September and 1 October – 31 March;
# a list uses the results of the two periods (one year) before it.
PERIOD_START_MONTHS = (4, 10)

# DCL 2025 Art. 3: last rank of each category (N1 … R8; everyone after that is R9).
CATEGORIES = ["N1", "N2", "N3", "N4", "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"]
CONTINGENTS = {
    "M": [10, 30, 70, 150, 310, 630, 1270, 2550, 5110, 10230, 20470, 30770],
    "F": [10, 24, 45, 75, 144, 284, 554, 1074, 2074, 4024, 7824, 11824],
}

# Start value for players without a previous-period value (old KR: "Mindestausgangswert 1").
NEW_PLAYER_W0 = 1.0


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
    m = m[m["playerWinnerCode"].isin(["S", "N"]) & (m["matchNotConsidered"] != True)]  # noqa: E712
    m = m.drop_duplicates(["personId", "encounterId"])

    ids = pd.Index(sorted(set(target["personId"]) | set(prev["personId"]) | set(m["personId"])))
    p = pd.DataFrame({"personId": ids})
    p = p.merge(prev[["personId", "W"]].rename(columns={"W": "W5_prev"}), how="left")
    p = p.merge(
        target[["personId", "firstname", "lastname", "classification", "rank", "W", "C"]].rename(
            columns={"W": "W_pub", "C": "C_pub", "classification": "class_pub", "rank": "rank_pub"}
        ),
        how="left",
    )
    p = p.merge(players[["personId", "gender"]], how="left")

    rows = pd.DataFrame(
        {
            "player": ids.get_indexer(m["personId"]),
            "opp": ids.get_indexer(m["adversaryPersonId"].fillna(-1).astype(int)),
            "opp_fixed": m["adversaryValue"].to_numpy(dtype=float),
            "win": (m["playerWinnerCode"] == "S").to_numpy(),
        }
    )
    # Opponents outside the calculation need the value stored with the result.
    rows = rows[(rows["opp"] >= 0) | (rows["opp_fixed"].fillna(0) != 0)].reset_index(drop=True)

    p["n_matches"] = np.bincount(rows["player"], minlength=len(p))
    p["gender"] = p["gender"].fillna(_gender_from_opponents(p, rows))
    return Period(publication, start, end, p, rows)


def _gender_from_opponents(p: pd.DataFrame, rows: pd.DataFrame) -> pd.Series:
    """Unlicensed players have no gender in the player list; singles are played within a gender."""
    known = rows[rows["opp"] >= 0]
    opp_gender = p["gender"].to_numpy()[known["opp"]]
    s = pd.Series(opp_gender, index=known["player"].to_numpy()).dropna()
    return s.groupby(level=0).agg(lambda g: g.mode().iat[0]).reindex(p.index)


def assign_categories(p: pd.DataFrame, C: str, W: str) -> pd.Series:
    """Rank by C (ties: higher W) within each gender and map the rank to a category (Art. 3, 5.10)."""
    out = pd.Series(pd.NA, index=p.index, dtype="object")
    for gender, bounds in CONTINGENTS.items():
        g = p[(p["gender"] == gender) & p[C].notna()].sort_values([C, W], ascending=False)
        rank = np.arange(1, len(g) + 1)
        out.loc[g.index] = [(CATEGORIES + ["R9"])[i] for i in np.searchsorted(bounds, rank)]
    return out


def evaluate(period: Period, w0_slope: float = 1.0, w0_intercept: float = 0.0) -> pd.DataFrame:
    p = period.players.copy()
    p["w0"] = (w0_slope * p["W5_prev"] + w0_intercept).fillna(NEW_PLAYER_W0)
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
    # Only players on the published list are ranked, as in the real list.
    on_list = p["C_pub"].notna()
    p["class"] = assign_categories(p.where(on_list), "C", "W")
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
    by_class = q.groupby("class_pub").apply(
        lambda g: pd.Series(
            {"n": len(g), "MAE_C": np.abs(g.C - g.C_pub).mean(), "same_cat": np.mean(g["class"] == g["class_pub"])}
        ),
        include_groups=False,
    )
    order = [c for c in CATEGORIES + ["R9"] if c in by_class.index]
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
