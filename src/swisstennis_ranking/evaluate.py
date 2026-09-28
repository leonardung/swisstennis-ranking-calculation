"""Recompute a ranking list from scraped matches and compare it with the published list.

Two modes:
- reproduce (default): inputs Swiss Tennis sets by hand or does not publish are read from the
  published list itself (w0 interpolation points fitted on it, the values of new players and of
  players classified by evaluation). Tells how well the calculation is understood.
- predict: only what is known before the list is published. Works for future lists and,
  on past lists, tells how well a list can be forecast.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import ranking

# DCL Art. 8: periods run 1 April – 30 September and 1 October – 31 March;
# a list uses the results of the two periods (one year) before it.
PERIOD_START_MONTHS = (4, 10)

CATEGORIES = ["N1", "N2", "N3", "N4", "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9"]

# DCL Art. 3: last rank of each category N1 … R8 among players inside the quota; the rest is R9.
# Players with equal C and W share a rank (Art. 5.10). Foreigners get no quota slot (Art. 9.2).
QUOTAS = {
    "M": [10, 30, 70, 150, 310, 630, 1270, 2550, 5110, 10230, 20470, 30770],
    "F": [10, 24, 45, 75, 144, 284, 554, 1074, 2074, 4024, 7824, 11824],
}

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
# start here too. Players without matches are not raised to it.
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

# Category of new players in predict mode: Swiss Tennis places ~94% of them in R9 (the rest by an
# evaluation no data predicts).
NEW_PLAYER_CATEGORY = "R9"

Knots = dict[str, tuple[np.ndarray, np.ndarray]]  # gender -> sorted (W5, w0) interpolation points


@dataclass
class Period:
    publication: pd.Timestamp
    start: pd.Timestamp
    end: pd.Timestamp
    players: pd.DataFrame  # one row per player, index = player number used by ranking.compute
    matches: pd.DataFrame  # player, opp, opp_fixed, win, match (row in the matches table)
    published: bool  # the list exists in the history (else only prediction is possible)
    # (W, C) of new players per (gender, category) on the list a year earlier (predict mode)
    new_pairs: pd.DataFrame | None = None


def publications(history: pd.DataFrame, min_players: int = 1000) -> list[pd.Timestamp]:
    """Dates of full ranking lists (ignores individual corrections published on other dates)."""
    counts = history.groupby(history["date"].dt.normalize())["personId"].nunique()
    return list(counts[counts >= min_players].index)


def next_list(date: pd.Timestamp) -> pd.Timestamp:
    """The official list a (monthly) list at `date` anticipates: the first period boundary
    (1 April or 1 October) on or after it."""
    date = date.normalize()
    bounds = [pd.Timestamp(y, m, 1) for y in (date.year, date.year + 1) for m in PERIOD_START_MONTHS]
    return min(b for b in bounds if b >= date)


def build_period(
    history: pd.DataFrame, matches: pd.DataFrame, players: pd.DataFrame, publication: pd.Timestamp
) -> Period:
    """Inputs of the list at `publication`.

    An official list (1 April / 1 October) uses the year of results before it and starts from
    the previous official list's W5. A monthly list is the next official list computed early:
    same start value and window start, with the results played until its date.
    """
    official = next_list(publication)
    start, end = official - pd.DateOffset(years=1), publication.normalize()
    day = history["date"].dt.normalize()

    target = history[day == publication].drop_duplicates("personId", keep="last")
    prev = history[day < end].sort_values("date").drop_duplicates("personId", keep="last")

    m = matches[(matches["date"] >= start) & (matches["date"] < end)]
    m = dedupe(m[m["playerWinnerCode"].isin(WIN_CODES + LOSS_CODES)])

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
    gender = genders(players, matches)
    p["gender"] = p["personId"].map(gender)
    p["licensed"] = p["personId"].isin(players["personId"])
    # on the previous official list (or a later correction), dated 6 months before the next one
    recent = prev.loc[prev["date"].dt.normalize() >= official - pd.DateOffset(months=6), "personId"]
    p["on_prev_list"] = p["personId"].isin(recent)
    p["foreign"] = foreign(history, p["personId"], end, ~p["licensed"])

    rows = pd.DataFrame(
        {
            "player": ids.get_indexer(m["personId"]),
            "opp": ids.get_indexer(m["adversaryPersonId"].fillna(-1).astype(int)),
            "opp_fixed": m["adversaryValue"].to_numpy(dtype=float),
            "win": m["playerWinnerCode"].isin(WIN_CODES).to_numpy(),
            "match": m.index.to_numpy(),  # row of the result in `matches`
        }
    )
    # Opponents outside the calculation need the value stored with the result.
    rows = rows[(rows["opp"] >= 0) | (rows["opp_fixed"].fillna(0) != 0)].reset_index(drop=True)

    p["n_matches"] = np.bincount(rows["player"], minlength=len(p))
    p["no_shows"] = p["personId"].map(no_shows(matches, start, end)).fillna(0).astype(int)
    pairs = new_player_pairs(history, gender, official - pd.DateOffset(years=1))
    return Period(publication, start, end, p, rows, published=not target.empty, new_pairs=pairs)


def dedupe(m: pd.DataFrame) -> pd.DataFrame:
    """One row per match: interclub results can be stored twice under the same encounterId.
    Tournament results have no encounterId (NaN) and pending ones 0: only dedupe real ids."""
    return m[m["encounterId"].fillna(0).eq(0) | ~m.duplicated(["personId", "encounterId"])]


def new_player_pairs(history: pd.DataFrame, gender: pd.Series, date: pd.Timestamp) -> pd.DataFrame:
    """(W, C) per (gender, category) given to new players (Art. 6.2) on the list at `date`: the
    most common pair among the players whose first list it is. Empty when the list or players
    first appearing on it are missing."""
    day = history["date"].dt.normalize()
    first = history.loc[day == day.groupby(history["personId"]).transform("min")]
    new = first[(first["date"].dt.normalize() == date) & (day.min() < date)]
    new = new.assign(gender=new["personId"].map(gender)).dropna(subset=["gender", "W", "C"])
    counts = new.groupby(["gender", "classification", "W", "C"]).size().reset_index(name="n")
    top = counts.sort_values("n").drop_duplicates(["gender", "classification"], keep="last")
    return top.set_index(["gender", "classification"])[["W", "C"]].sort_index()


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


def foreign(history: pd.DataFrame, ids: pd.Series, end: pd.Timestamp, fallback: pd.Series) -> pd.Series:
    """True for players outside the quota (kontingent 0: foreigners, Art. 9.2) on the latest
    flagged list before the window end or on the list itself (nationality, not a result of the
    calculation). Either flag counts: the current list's comes from the licence table, which
    holds today's flag, not the one of its date. Players never flagged (history without the
    kontingent column) fall back to not holding a current licence."""
    if "kontingent" not in history:
        return fallback
    h = history[history["kontingent"].notna()].sort_values("date")
    before = h[h["date"] < end].drop_duplicates("personId", keep="last").set_index("personId")["kontingent"]
    on_list = h[h["date"] >= end].drop_duplicates("personId").set_index("personId")["kontingent"]
    prev, now = ids.map(before), ids.map(on_list)
    return (prev.eq(0) | now.eq(0)).where(prev.notna() | now.notna(), fallback).astype(bool)


def genders(players: pd.DataFrame, matches: pd.DataFrame) -> pd.Series:
    """Gender per personId: from the licence list, else from their opponents over all matches
    (singles are played within a gender; unlicensed players are not in the licence list).
    Repeated so it reaches unlicensed players who only played other unlicensed players."""
    known = players.set_index("personId")["gender"]
    pairs = matches[["personId", "adversaryPersonId"]].dropna().astype(int)
    while True:
        m = pairs.assign(gender=pairs["personId"].map(known)).dropna()
        m = m[~m["adversaryPersonId"].isin(known.index)]
        if m.empty:
            return known
        inferred = (
            m.groupby(["adversaryPersonId", "gender"]).size().reset_index().sort_values(0)
            .drop_duplicates("adversaryPersonId", keep="last")
            .set_index("adversaryPersonId")["gender"]
        )
        known = pd.concat([known, inferred])


def licensed_then(p: pd.DataFrame) -> pd.Series:
    """Predict mode on a past list: the players on it who probably held a licence on its date.

    The history carries every scraped player on every list, licensed then or not, and the licence
    table of a past date is not in the data; keep the players licensed now or with results in the
    list's window (a result needs a licence). Exact for the current list; on older lists it misses
    players who have since dropped their licence.
    """
    return p["on_list"] & (p["licensed"] | (p["n_matches"] > 0))


# --- w0 from W5 --------------------------------------------------------------------------------


def category_means(p: pd.DataFrame) -> Knots:
    """Interpolation points from the previous list: per gender, the mean W5 of the players inside
    the quota in each category, at the knot constant R8=1 … R1=8, N4=9 … N1=12
    (reference/Interpolieren.xlsx), averaged over the players on the new list who hold a licence
    (`licensed_then`, default `on_list`). This matches the exact (fitted) points to about 0.001
    when the licensed players are known (the current list).

    The means are sensitive to who is included: over all players a past list carries in the
    history (including those without a licence then) they are off by up to 0.02.
    """
    swiss = p[p.get("licensed_then", p["on_list"]) & ~p["foreign"] & p["W5_prev"].notna() & p["class_prev"].isin(CATEGORIES[:-1])]
    out = {}
    for gender, g in swiss.groupby("gender"):
        means = g.groupby("class_prev")["W5_prev"].mean()
        ks = np.array([len(CATEGORIES) - 1 - CATEGORIES.index(c) for c in means.index], dtype=float)
        order = np.argsort(ks)
        out[gender] = (means.to_numpy()[order], ks[order])
    return out


def _fit_line(x: np.ndarray, y: np.ndarray, tol: float = 0.0011, tries: int = 300):
    """Robust line fit (RANSAC with a fixed seed): the line through most points within tol,
    refitted by least squares on its inliers. Returns (intercept, slope) or None."""
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
    return a, b


def fitted_knots(p: pd.DataFrame, max_knot: int = 9) -> Knots:
    """Interpolation points read off the published list (reproduce mode).

    Players without matches have W = w0, so those whose w0 lies in [k, k+1) are on one line
    segment, and its crossings with w0 = k and k+1 are the exact points. A robust fit ignores
    players whose published W is not their w0 (assigned values, missing results). Points that
    cannot be fitted (too few Swiss players without matches, above R1) come from category_means.
    """
    inactive = p[
        (p["n_matches"] == 0) & p["W5_prev"].notna() & (p["W_pub"] > FLOOR_START_VALUE + 5e-4) & ~p["foreign"]
    ]
    out = {}
    for gender, (mx, mk) in category_means(p).items():
        g = inactive[inactive["gender"] == gender]
        crossings: dict[float, list[float]] = {}
        for k in range(1, max_knot):
            seg = g[(g["W_pub"] >= k) & (g["W_pub"] < k + 1)]
            fit = _fit_line(seg["W5_prev"].to_numpy(), seg["W_pub"].to_numpy())
            if fit is None or fit[1] < 0.3:
                continue
            a, b = fit
            crossings.setdefault(k, []).append((k - a) / b)
            crossings.setdefault(k + 1, []).append((k + 1 - a) / b)
        points = {k: np.mean(v) for k, v in crossings.items()}
        for k, x in zip(mk, mx):
            if k > max(points, default=0):
                points[k] = x
        ks = np.array(sorted(points), dtype=float)
        x = np.array([points[k] for k in ks])
        out[gender] = (x, ks) if len(ks) >= 2 and np.all(np.diff(x) > 0) else (mx, mk)
    return out


def start_values(p: pd.DataFrame, knots: Knots) -> pd.Series:
    """w0 per player: W5 interpolated linearly between the knots, rounded to 3 decimals.

    Below the first knot w0 is FLOOR_START_VALUE; above the last one the last segment is
    extrapolated. Players with matches start at MIN_START_VALUE or higher; new players at
    MIN_START_VALUE.
    """
    w0 = pd.Series(MIN_START_VALUE, index=p.index)
    for gender, (x, y) in knots.items():
        sel = (p["gender"] == gender) & p["W5_prev"].notna()
        w5 = p.loc[sel, "W5_prev"].to_numpy()
        if len(x) >= 2:
            above = y[-1] + (w5 - x[-1]) * (y[-1] - y[-2]) / (x[-1] - x[-2])
        else:
            above = w5 * y[-1] / x[-1]
        v = np.where(w5 > x[-1], above, np.interp(w5, x, y))
        w0[sel] = np.where(w5 < x[0], FLOOR_START_VALUE, v).round(3)
    return w0.where(p["n_matches"] == 0, w0.clip(lower=MIN_START_VALUE))


def snap_category_means(p: pd.DataFrame, r: pd.DataFrame, knots: Knots) -> np.ndarray:
    """Opponent values of results against opponents outside the calculation, with stored
    category means (3 decimals) replaced by this list's mean of the nearest category."""
    v = r["opp_fixed"].to_numpy(dtype=float).copy()
    gender = p["gender"].to_numpy()[r["player"].to_numpy()]
    three_dec = np.abs(v * 1000 - np.round(v * 1000)) < 0.01
    cand = (r["opp"].to_numpy() < 0) & ~np.isnan(v) & three_dec
    for g, (x, _) in knots.items():
        sel = cand & (gender == g)
        if not sel.any():
            continue
        nearest = x[np.abs(v[sel, None] - x[None, :]).argmin(axis=1)]
        close = np.abs(v[sel] - nearest) < CATEGORY_MEAN_SNAP
        v[np.flatnonzero(sel)[close]] = nearest[close].round(3)
    return v


# --- values set by Swiss Tennis (reproduce mode only) ---------------------------------------


def new_player_values(p: pd.DataFrame) -> pd.DataFrame:
    """(W, C) per gender and category for players without a previous value (DCL Art. 6.2).

    They are not computed: Swiss Tennis classifies them and every new player of a gender and
    category is published with the same (W, C) pair, whatever their results. The pair is close
    to (not exactly) the category's mean published W and C of the players with a previous value.
    """
    listed = p[p["C_pub"].notna() & p["W5_prev"].notna()]
    return listed.groupby(["gender", "class_pub"])[["W_pub", "C_pub"]].mean()


def new_player_prediction(p: pd.DataFrame, last_year: pd.DataFrame | None) -> pd.DataFrame:
    """Predict mode: (W, C) per gender and category for new players, who are placed in
    NEW_PLAYER_CATEGORY.

    The pair is close to the category's mean on the list itself, which is seasonal (in R9 the
    mean C is ~0.745 in April and ~0.757 in October) and stable from one year to the next:
    the pair of the same list a year earlier (`last_year`, see new_player_pairs) is within 0.001
    of it for both genders since 2024 (women earlier: up to 0.005). Falls back to the mean of
    our computed values.
    """
    listed = p[p["on_list"] & p["W5_prev"].notna()]
    means = listed.groupby(["gender", "class"])[["W", "C"]].mean().rename_axis(["gender", "classification"])
    return means if last_year is None else last_year.combine_first(means)


def evaluated_players(p: pd.DataFrame) -> pd.Series:
    """Players with a previous value that Swiss Tennis classified by evaluation (DCL Art. 6.1).

    They are published with the (W, C) pair of their gender and category that new players get
    (Art. 6.2), whatever their W5 and results; far more of them on April lists (players returning
    for the summer season) than in October. A pair is a (W, C) published for at least two players
    of a category, one of them a new Swiss player or a Swiss player without matches but with
    R != 0 (impossible when computed). Not in R8/R9, where many computed values coincide.
    Foreigners' values are fixed (Art. 9.2), not a pair.
    """
    key = ["gender", "class_pub", "W_pub", "C_pub"]
    listed = p[p["C_pub"].notna() & p["class_pub"].isin(EVALUATED_CATEGORIES)]
    inactive_with_R = (listed["n_matches"] == 0) & (listed["W_pub"] != listed["C_pub"])
    manual = (listed["W5_prev"].isna() | inactive_with_R) & ~listed["foreign"]
    counts = listed.assign(manual=manual, n=1).groupby(key)[["manual", "n"]].sum()
    pairs = counts[(counts["manual"] >= 1) & (counts["n"] >= 2)]
    hit = pd.MultiIndex.from_frame(p[key]).isin(pairs.index)
    return pd.Series(hit, index=p.index) & p["W5_prev"].notna()


# --- the list ------------------------------------------------------------------------------


def _key(g: pd.DataFrame, C: str = "C", W: str = "W") -> np.ndarray:
    """Ascending key = descending (C, W); values have 3 decimals."""
    return -(np.round(g[C].to_numpy() * 1000) * 100_000 + np.round(g[W].to_numpy() * 1000))


def published_cuts(p: pd.DataFrame) -> dict[str, np.ndarray]:
    """Category bounds of the published list, as the worst key of each category (reproduce mode).

    The quota pool (Art. 3) depends on who held a licence on the list date, which is only known
    for the current list; the published categories give the bounds directly. Each bound is the
    cut that misclassifies the fewest Swiss players of the two categories around it.
    """
    cuts = {}
    for gender in QUOTAS:
        g = p[p["on_list"] & (p["gender"] == gender) & ~p["foreign"] & ~p["classified"]]
        g = g[g["class_pub"].isin(CATEGORIES)]
        if g.empty:
            continue
        order = np.argsort(_key(g, "C_pub", "W_pub"), kind="stable")
        key = _key(g, "C_pub", "W_pub")[order]
        level = g["class_pub"].map({c: i for i, c in enumerate(CATEGORIES)}).to_numpy()[order]
        bounds = []
        for i in range(len(CATEGORIES) - 1):
            # cutting after position j: errors = better-category players below + worse ones above
            above_worse = np.cumsum(level > i)
            below_better = (level <= i).sum() - np.cumsum(level <= i)
            # cut only between distinct keys, or before everyone (-inf)
            j = np.append(np.flatnonzero(key[:-1] != key[1:]), len(key) - 1)
            errors = np.append((level <= i).sum(), (above_worse + below_better)[j])
            bounds.append(np.append(-np.inf, key[j])[np.argmin(errors)])
        cuts[gender] = np.maximum.accumulate(bounds)
    return cuts


def ranked(p: pd.DataFrame) -> pd.Series:
    """Players who take a rank (and quota) slot: Swiss players on the list with a previous value,
    not classified by Swiss Tennis. New players, even with results, take none on the published
    lists."""
    return p["on_list"] & ~p["foreign"] & ~p["classified"] & p["W5_prev"].notna()


def categories(p: pd.DataFrame, cuts: dict[str, np.ndarray] | None = None) -> tuple[pd.Series, pd.Series]:
    """(rank, category) per player on the list, from our C (Art. 3, 5.10, 9.2).

    The ranked players (see ranked()) are ranked per gender by C, then W; equal (C, W) share a
    rank, and the quota bounds give the category (or `cuts`, the published bounds). The other
    players get the rank their (C, W) would have among them, without taking a slot.
    """
    rank = pd.Series(np.nan, index=p.index)
    cat = pd.Series(pd.NA, index=p.index, dtype="object")
    for gender, bounds in QUOTAS.items():
        g = p[p["on_list"] & (p["gender"] == gender)]
        swiss = np.sort(_key(g[ranked(g)]))
        pos = np.searchsorted(swiss, _key(g), side="left") + 1  # 1 + number of ranked strictly better
        rank[g.index] = pos
        if cuts is None or gender not in cuts:
            level = np.searchsorted(bounds, pos)
        else:
            level = np.searchsorted(cuts[gender], _key(g))
        cat[g.index] = np.array(CATEGORIES)[level]
    return rank, cat


def placed_ranks(p: pd.DataFrame) -> pd.Series:
    """Rank of the players Swiss Tennis places in a category (new players, classified by
    evaluation): the middle of the category's quota range, the last range ending at the last
    ranked player (see ranked()). Other players keep their rank."""
    rank = p["rank"].copy()
    placed = p["on_list"] & ~p["foreign"] & (p["classified"] | p["W5_prev"].isna())
    for gender, quota in QUOTAS.items():
        ends = np.array([0, *quota, (ranked(p) & (p["gender"] == gender)).sum()])
        sel = placed & (p["gender"] == gender) & p["class"].isin(CATEGORIES)
        level = p.loc[sel, "class"].map(CATEGORIES.index).to_numpy(dtype=int)
        rank[sel] = (ends[level] + ends[level + 1]) // 2 + 1
    return rank


def C_at_rank(p: pd.DataFrame, assigned: pd.Series) -> pd.Series:
    """C of players with an assigned value: the computed C of the Swiss player (per gender) at
    their previous rank. Ranks count only the ranked players (Art. 9.2)."""
    out = pd.Series(np.nan, index=p.index)
    for gender, g in p[ranked(p)].groupby("gender"):
        cs = np.sort(g["C"].to_numpy())[::-1]
        sel = assigned & (p["gender"] == gender) & p["rank_prev"].notna()
        pos = p.loc[sel, "rank_prev"].to_numpy(dtype=int) - 1
        out[sel] = cs[np.clip(pos, 0, len(cs) - 1)]
    return out.fillna(p["C"])


def evaluate(period: Period, predict: bool = False) -> pd.DataFrame:
    """Compute W, R, C, rank and category of every player on the list.

    predict=True uses nothing from the list being computed except who is on it. For a list not
    published (future or monthly), that is the players on the previous official list or with a
    result in the window.
    """
    return evaluate_detailed(period, predict)[0]


def evaluate_detailed(period: Period, predict: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """evaluate(), plus the match rows with the opponent value of the last pass (opp_w) and
    whether a loss counts (counted; False for the discarded ones, True for wins)."""
    if not predict and not period.published:
        raise ValueError(f"{period.publication.date()} is not published; use predict mode")
    p = period.players.copy()
    p["on_list"] = p["C_pub"].notna() if period.published else p["on_prev_list"] | (p["n_matches"] > 0)
    p["licensed_then"] = licensed_then(p) if predict and period.published else p["on_list"]
    knots = category_means(p) if predict else fitted_knots(p)
    p["w0"] = start_values(p, knots)
    r = period.matches
    assigned = p["foreign"] & (p["W5_prev"] >= ASSIGNED_MIN_W5)
    W, R, opp_w = ranking.compute(
        p["w0"].to_numpy(),
        r["player"].to_numpy(),
        r["opp"].to_numpy(),
        snap_category_means(p, r, knots),
        r["win"].to_numpy(),
        fixed=p["W5_prev"].where(assigned).to_numpy(dtype=float),
        with_opp_w=True,
    )
    loss_counts = ranking.counted_losses(r["player"].to_numpy(), opp_w, r["win"].to_numpy(), len(p))
    rows = r.assign(opp_w=opp_w, counted=r["win"].to_numpy() | loss_counts)
    # The Art. 5.8 deduction is shown in R (published R = C - W).
    p["W"] = W.round(3)
    p["R"] = (R - NO_SHOW_DEDUCTION * (p["no_shows"] > NO_SHOW_LIMIT)).round(3)
    p["C"] = (p["W"] + p["R"]).round(3)

    def set_values(sel: pd.Series, W: pd.Series, C: pd.Series) -> None:
        p.loc[sel, "W"] = W[sel].round(3)
        p.loc[sel, "C"] = C[sel].round(3)
        p.loc[sel, "R"] = (p.loc[sel, "C"] - p.loc[sel, "W"]).round(3)

    # Values set by Swiss Tennis (Art. 6): classified players get their published pair and
    # category (predict: new players only, see new_player_prediction) and, like foreigners,
    # take no quota slot.
    p["evaluated"] = False
    p["classified"] = False
    if not predict:
        values = p[["gender", "class_pub"]].join(new_player_values(p), on=["gender", "class_pub"])
        new = p["W5_prev"].isna() & values["C_pub"].notna()
        set_values(new, values["W_pub"], values["C_pub"])
        p["evaluated"] = evaluated_players(p)
        set_values(p["evaluated"], p["W_pub"], p["C_pub"])
        p["classified"] = new | p["evaluated"]
    else:
        p["classified"] = p["on_list"] & p["W5_prev"].isna() & (p["n_matches"] == 0)
    assigned &= ~p["classified"]
    set_values(assigned, p["W5_prev"], C_at_rank(p, assigned))

    p["rank"], p["class"] = categories(p, None if predict else published_cuts(p))
    if predict:
        p.loc[p["classified"], "class"] = NEW_PLAYER_CATEGORY
        values = p[["gender", "class"]].join(new_player_prediction(p, period.new_pairs), on=["gender", "class"])
        set_values(p["classified"] & values["C"].notna(), values["W"], values["C"])
    else:
        p.loc[p["classified"], "class"] = p["class_pub"]
    keep = assigned & p["class_prev"].isin(CATEGORIES)
    p.loc[keep, "class"] = p.loc[keep, "class_prev"]
    p["rank"] = placed_ranks(p)
    p["R_pub"] = p["C_pub"] - p["W_pub"]
    return p, rows


def rank_err(p: pd.DataFrame) -> pd.Series:
    """|our rank - published rank|."""
    return (p["rank"] - p["rank_pub"]).abs()


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
            f"same category={np.mean(sub['class'] == sub['class_pub']):.1%}  "
            f"rank MAE={rank_err(sub).mean():.1f} within 10={np.mean(rank_err(sub) <= 10):.1%}"
        )
    by_class = (
        q.assign(err=np.abs(q.C - q.C_pub), same=q["class"] == q["class_pub"])
        .groupby("class_pub")
        .agg(n=("err", "size"), MAE_C=("err", "mean"), same_cat=("same", "mean"))
    )
    order = [c for c in CATEGORIES if c in by_class.index]
    lines.append(by_class.reindex(order).round(4).to_string())
    return "\n".join(lines)

