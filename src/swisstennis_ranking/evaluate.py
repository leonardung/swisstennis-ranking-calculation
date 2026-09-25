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

# DCL Art. 5.8: a player who more than 3 times within two periods (the list's one-year window)
# does not show up after the draw (result 0-0, code 0 on their side; 1 = won by walkover) loses
# 0.3 of C (W and so their value for opponents is unchanged). Only Swiss tournaments count
# (sources 4xx; not interclub, club championships (700), foreign results), each tournament once
# (round-robin: one w.o. per tournament), and not the club-internal Club Champion Trophy /
# Next Gen Trophy events.
NO_SHOW_CODE = "0"
NO_SHOW_SOURCES = (400, 499)
NO_SHOW_EXCLUDED = r"Club Champion Trophy|Next Gen Trophy"
NO_SHOW_LIMIT = 3
NO_SHOW_DEDUCTION = 0.3

# Minimum start value for players with matches (old KR: "Mindestausgangswert 1"); new players
# start here too. Players without matches are not raised to it: they keep w0 (floor 0.75).
MIN_START_VALUE = 1.0
# Start value of a player whose W5 is below the lowest interpolation point (R8 mean).
FLOOR_START_VALUE = 0.75

# Foreign players (outside the quota, RankingHistory.lzh_kontingent = 0) from R1 up have an
# assigned value (Art. 9.2): their W stays at the previous list's value, both for their opponents
# and on the list, and they keep their previous rank and category; their C is the C of the Swiss
# player at that rank on the new list.
ASSIGNED_MIN_W5 = 9.0

# Results against foreign opponents (source 610) store a category mean (3 decimals) as the
# opponent value, taken from the latest list that used the result; such values are replaced by
# the mean of the same category for the list being computed when within this distance of it.
CATEGORY_MEAN_SNAP = 0.2

# Categories in which players classified by evaluation (Art. 6.1) are recognised.
EVALUATED_CATEGORIES = CATEGORIES[:-2]


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
    p = p.merge(
        prev[["personId", "W", "classification", "rank"]].rename(
            columns={"W": "W5_prev", "classification": "class_prev", "rank": "rank_prev"}
        ),
        how="left",
    )
    p = p.merge(
        target[["personId", "firstname", "lastname", "classification", "rank", "W", "C", "games"]].rename(
            columns={"W": "W_pub", "C": "C_pub", "classification": "class_pub", "rank": "rank_pub"}
        ),
        how="left",
    )
    p["gender"] = p["personId"].map(genders(players, matches))
    p["licensed"] = p["personId"].isin(players["personId"])
    p["foreign"] = foreign(history, p["personId"], end, publication, ~p["licensed"])

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
    p["no_shows"] = p["personId"].map(no_shows(matches, start, end)).fillna(0).astype(int)
    return Period(publication, start, end, p, rows)


def no_shows(matches: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    """Per personId, the Swiss tournaments in [start, end) where they did not show up (Art. 5.8)."""
    if not {"source", "tournamentName"} <= set(matches.columns):
        return pd.Series(dtype=int)
    w = matches[
        (matches["date"] >= start)
        & (matches["date"] < end)
        & (matches["playerWinnerCode"] == NO_SHOW_CODE)
        & matches["source"].between(*NO_SHOW_SOURCES)
    ]
    w = w[~w["tournamentName"].fillna("").str.contains(NO_SHOW_EXCLUDED)]
    return w.groupby("personId")["tournamentName"].nunique()


def foreign(
    history: pd.DataFrame, ids: pd.Series, end: pd.Timestamp, publication: pd.Timestamp, fallback: pd.Series
) -> pd.Series:
    """True for players outside the quota (kontingent 0: foreigners, Art. 9.2), from the latest
    list before the window end, else from the list being computed. Without the kontingent column
    (older scrapes) not holding a current licence is used as a proxy."""
    if "kontingent" not in history:
        return fallback
    h = history[history["kontingent"].notna()]
    day = h["date"].dt.normalize()
    before = h[day < end].sort_values("date").drop_duplicates("personId", keep="last")
    k = pd.concat([before, h[day == publication]]).drop_duplicates("personId", keep="first")
    flag = ids.map(k.set_index("personId")["kontingent"].eq(0))
    return flag.fillna(fallback).astype(bool)


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


def _best_cut(c: np.ndarray, upper: np.ndarray) -> float:
    """C threshold that best separates the `upper` players from the others (fewest misplaced)."""
    order = np.argsort(c)
    c, upper = c[order], upper[order]
    # cut before position i: players [i:] are upper. misplaced = upper below + lower above.
    misplaced = np.r_[0, np.cumsum(upper)] + np.r_[np.cumsum((~upper)[::-1])[::-1], 0]
    i = int(np.argmin(misplaced))
    return c[i] if i < len(c) else np.inf


def category_thresholds(listed: pd.DataFrame) -> pd.Series:
    """Per category (except the last), the published C from which a player of this gender
    reaches it. Each cut is the one that best separates the category and better ones from the
    rest, so a few manually placed players (Art. 6) cannot move it."""
    classes = [c for c in CATEGORIES if c in set(listed["class_pub"])]
    rank = listed["class_pub"].map({c: i for i, c in enumerate(classes)}).to_numpy()
    c = listed["C_pub"].to_numpy()
    return pd.Series([_best_cut(c, rank <= k) for k in range(len(classes) - 1)], index=classes[:-1])


def assign_categories(p: pd.DataFrame) -> pd.Series:
    """Category our C would get on the published list: the best category whose published
    C threshold (per gender) we reach.

    Ranking by the Art. 3 quotas does not reproduce the list: foreign players get a rank but no
    quota slot (Art. 9.2), and Swiss Tennis adapts the quotas; the published thresholds include both.
    """
    out = pd.Series(pd.NA, index=p.index, dtype="object")
    listed = p[p["C_pub"].notna()]
    for gender, g in listed.groupby("gender"):
        thresholds = category_thresholds(g)
        last = [c for c in CATEGORIES if c in set(g["class_pub"])][-1]
        reached = g["C"].to_numpy()[:, None] >= thresholds.to_numpy()[None, :]
        out.loc[g.index] = np.where(reached.any(axis=1), thresholds.index.to_numpy()[reached.argmax(axis=1)], last)
    return out


def _fit_line(x: np.ndarray, y: np.ndarray, tol: float = 0.0011, tries: int = 300):
    """Robust line fit (RANSAC with a fixed seed): the line through most points within tol,
    refitted by least squares on its inliers. Returns (intercept, slope, n_inliers) or None."""
    if len(x) < 2:
        return None
    rng = np.random.default_rng(0)
    best = None
    for _ in range(tries):
        i, j = rng.choice(len(x), 2, replace=False)
        if abs(x[i] - x[j]) < 0.02:
            continue
        b = (y[j] - y[i]) / (x[j] - x[i])
        inliers = np.abs(y - (y[i] + b * (x - x[i]))) < tol
        if best is None or inliers.sum() > best.sum():
            best = inliers
    if best is None or best.sum() < 3:
        return None
    b, a = np.polyfit(x[best], y[best], 1)
    return a, b, int(best.sum())


def knot_table(p: pd.DataFrame, max_knot: int = 9) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Per gender, the interpolation points (W5, w0) of the W5 -> w0 conversion.

    Swiss Tennis interpolates W5 linearly between the mean W5 of the categories, which map to
    the constants 1=R8, 2=R7, ..., 8=R1, 9=N4, ... (reference/Interpolieren.xlsx). The means are
    not published: players without matches (W = w0) whose w0 lies in [k, k+1) are on one line
    segment, and its crossings with w0 = k and k+1 are the category means. A robust fit ignores
    the players whose published W is not their w0 (foreign players, missing results).
    """
    inactive = p[
        (p["n_matches"] == 0) & p["W5_prev"].notna() & (p["W_pub"] > FLOOR_START_VALUE + 5e-4) & ~p["foreign"]
    ]
    table = {}
    for gender, g in inactive.groupby("gender"):
        crossings: dict[int, list[float]] = {}
        for k in range(1, max_knot):
            seg = g[(g["W_pub"] >= k) & (g["W_pub"] < k + 1)]
            fit = _fit_line(seg["W5_prev"].to_numpy(), seg["W_pub"].to_numpy())
            if fit is None or fit[1] < 0.3:
                continue
            a, b, _ = fit
            crossings.setdefault(k, []).append((k - a) / b)
            crossings.setdefault(k + 1, []).append((k + 1 - a) / b)
        points = {k: np.mean(v) for k, v in crossings.items()}
        # Too few Swiss players without matches above R1: use the category means directly.
        for k, mean in category_means(p).get(gender, {}).items():
            if k > max(points, default=0):
                points[k] = mean
        ks = sorted(points)
        x = np.array([points[k] for k in ks])
        if len(ks) >= 2 and np.all(np.diff(x) > 0):
            table[gender] = (x, np.array(ks, dtype=float))
    return table


def category_means(p: pd.DataFrame) -> dict[str, dict[int, float]]:
    """Per gender, the knot constant k (R8=1 ... R1=8, N4=9 ... N1=12) -> mean W5 of the players
    inside the quota who were in that category on the previous list."""
    swiss = p[~p["foreign"] & p["W5_prev"].notna() & p["class_prev"].isin(CATEGORIES[:-1])]
    means = swiss.groupby(["gender", "class_prev"])["W5_prev"].mean()
    out: dict[str, dict[int, float]] = {}
    for (gender, cat), mean in means.items():
        out.setdefault(gender, {})[len(CATEGORIES) - 1 - CATEGORIES.index(cat)] = float(mean)
    return out


def w0_table(
    p: pd.DataFrame, knots: dict[str, tuple[np.ndarray, np.ndarray]] | None = None
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Per gender, the W5 -> w0 conversion used for this list, as sorted (W5, w0) points.

    The category-mean interpolation points (knot_table) where they can be fitted; otherwise
    the (W5, published W) medians of players without matches, who have W = w0.
    """
    knots = knot_table(p) if knots is None else knots
    inactive = p[(p["n_matches"] == 0) & p["W5_prev"].notna() & p["W_pub"].notna() & ~p["foreign"]]
    table = {}
    for gender, g in inactive.groupby("gender"):
        if gender in knots:
            table[gender] = knots[gender]
        else:
            pts = g.groupby("W5_prev")["W_pub"].median()
            table[gender] = (pts.index.to_numpy(), pts.to_numpy())
    return table


def start_values(p: pd.DataFrame, table: dict[str, tuple[np.ndarray, np.ndarray]]) -> pd.Series:
    """w0 per player: interpolate W5 in the list's conversion table, rounded to 3 decimals.

    Below the first point w0 is FLOOR_START_VALUE; above the last one the last segment is
    extrapolated. Players with matches start at MIN_START_VALUE or higher; new players at
    MIN_START_VALUE.
    """
    w0 = pd.Series(MIN_START_VALUE, index=p.index)
    for gender, (x, y) in table.items():
        sel = (p["gender"] == gender) & p["W5_prev"].notna()
        w5 = p.loc[sel, "W5_prev"].to_numpy()
        if len(x) >= 2:
            above = y[-1] + (w5 - x[-1]) * (y[-1] - y[-2]) / (x[-1] - x[-2])
        else:
            above = w5 * y[-1] / x[-1]
        v = np.where(w5 > x[-1], above, np.interp(w5, x, y))
        w0[sel] = np.where(w5 < x[0], FLOOR_START_VALUE, v).round(3)
    return w0.where(p["n_matches"] == 0, w0.clip(lower=MIN_START_VALUE))


def snap_category_means(
    p: pd.DataFrame, r: pd.DataFrame, knots: dict[str, tuple[np.ndarray, np.ndarray]]
) -> np.ndarray:
    """Opponent values of results against opponents outside the calculation, with stored
    category means (3 decimals) replaced by this list's mean of the nearest category."""
    v = r["opp_fixed"].to_numpy(dtype=float).copy()
    gender = p["gender"].to_numpy()[r["player"].to_numpy()]
    three_dec = np.abs(v * 1000 - np.round(v * 1000)) < 0.01
    cand = (r["opp"].to_numpy() < 0) & ~np.isnan(v) & three_dec
    for g, (x, _) in knots.items():
        sel = cand & (gender == g)
        nearest = x[np.abs(v[sel, None] - x[None, :]).argmin(axis=1)] if sel.any() else np.array([])
        close = np.abs(v[sel] - nearest) < CATEGORY_MEAN_SNAP
        v[np.flatnonzero(sel)[close]] = nearest[close].round(3)
    return v


def new_player_values(p: pd.DataFrame) -> pd.DataFrame:
    """(W, C) per gender and category for players without a previous value (DCL Art. 6.2).

    They are not computed: Swiss Tennis classifies them and every new player of a gender and
    category is published with the same (W, C) pair, whatever their results. The pair is close
    to (not exactly) the category's mean published W and C of the players with a previous value.
    This reads the published category, so it is an external input, not a prediction.
    """
    listed = p[p["C_pub"].notna() & p["W5_prev"].notna()]
    return listed.groupby(["gender", "class_pub"])[["W_pub", "C_pub"]].mean()


def evaluated_players(p: pd.DataFrame) -> pd.Series:
    """Players with a previous value that Swiss Tennis classified by evaluation (DCL Art. 6.1).

    They are published with the (W, C) pair of their gender and category that new players get
    (Art. 6.2), whatever their W5 and results; far more of them on April lists (players returning
    for the summer season) than in October. A pair is a (W, C) published for at least two players
    of a category, one of them new or a Swiss player without matches but with R != 0
    (impossible when computed; foreign players' assigned values are, see ASSIGNED_MIN_W5).
    Not in R8/R9, where many computed values coincide. Like the new players' category this is
    read from the published list: an external input, not a prediction.
    """
    key = ["gender", "class_pub", "W_pub", "C_pub"]
    listed = p[p["C_pub"].notna() & p["class_pub"].isin(EVALUATED_CATEGORIES)]
    inactive_with_R = (listed["n_matches"] == 0) & (listed["W_pub"] != listed["C_pub"]) & ~listed["foreign"]
    manual = listed["W5_prev"].isna() | inactive_with_R
    counts = listed.assign(manual=manual, n=1).groupby(key)[["manual", "n"]].sum()
    pairs = counts[(counts["manual"] >= 1) & (counts["n"] >= 2)]
    hit = pd.MultiIndex.from_frame(p[key]).isin(pairs.index)
    return pd.Series(hit, index=p.index) & p["W5_prev"].notna()


def evaluate(period: Period) -> pd.DataFrame:
    p = period.players.copy()
    knots = knot_table(p)
    p["w0"] = start_values(p, w0_table(p, knots))
    r = period.matches
    assigned = p["foreign"] & (p["W5_prev"] >= ASSIGNED_MIN_W5)
    W, R = ranking.compute(
        p["w0"].to_numpy(),
        r["player"].to_numpy(),
        r["opp"].to_numpy(),
        snap_category_means(p, r, knots),
        r["win"].to_numpy(),
        fixed=p["W5_prev"].where(assigned).to_numpy(dtype=float),
    )
    # The Art. 5.8 deduction is shown in R (published R = C - W).
    p["W"] = W.round(3)
    p["R"] = (R - NO_SHOW_DEDUCTION * (p["no_shows"] > NO_SHOW_LIMIT)).round(3)
    p["C"] = (p["W"] + p["R"]).round(3)
    values = p[["gender", "class_pub"]].join(new_player_values(p), on=["gender", "class_pub"])
    new = p["W5_prev"].isna() & values["C_pub"].notna()
    p.loc[new, "W"] = values.loc[new, "W_pub"].round(3)
    p.loc[new, "C"] = values.loc[new, "C_pub"].round(3)
    p.loc[new, "R"] = (p.loc[new, "C"] - p.loc[new, "W"]).round(3)
    p.loc[assigned, "W"] = p.loc[assigned, "W5_prev"]
    p.loc[assigned, "C"] = C_at_rank(p, assigned)
    p.loc[assigned, "R"] = (p.loc[assigned, "C"] - p.loc[assigned, "W"]).round(3)
    evaluated = evaluated_players(p)
    p.loc[evaluated, ["W", "C"]] = p.loc[evaluated, ["W_pub", "C_pub"]].to_numpy()
    p.loc[evaluated, "R"] = (p.loc[evaluated, "C"] - p.loc[evaluated, "W"]).round(3)
    p["evaluated"] = evaluated
    p["R_pub"] = p["C_pub"] - p["W_pub"]
    p["class"] = assign_categories(p)
    keep = assigned & ~evaluated & p["class_prev"].isin(CATEGORIES)
    p.loc[keep, "class"] = p.loc[keep, "class_prev"]
    return p


def C_at_rank(p: pd.DataFrame, assigned: pd.Series) -> pd.Series:
    """C of players with an assigned value: the computed C of the Swiss player (per gender) at
    their previous rank. Ranks count only players inside the quota on the list (Art. 9.2)."""
    out = pd.Series(np.nan, index=p.index)
    ranked = p["C_pub"].notna() & ~p["foreign"]
    for gender, g in p[ranked].groupby("gender"):
        cs = np.sort(g["C"].to_numpy())[::-1]
        sel = assigned & (p["gender"] == gender) & p["rank_prev"].notna()
        pos = p.loc[sel, "rank_prev"].to_numpy(dtype=int) - 1
        out[sel] = cs[np.clip(pos, 0, len(cs) - 1)]
    return out[assigned].fillna(p.loc[assigned, "C"])


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
