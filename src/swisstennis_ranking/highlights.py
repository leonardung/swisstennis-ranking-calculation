"""Home page highlights: leaderboards, upsets of the week and biggest movers.

Computed once per data load (see web.load_state): each is a small list of rows.
"""

from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd

from . import evaluate
from .evaluate import CATEGORIES, FLOOR_START_VALUE, LOSS_CODES, WIN_CODES, dedupe
from .stats import MATCH_TIEBREAK_MIN, _sets, describe

LEADERBOARD_YEARS = 5
# rows kept per list (and per gender and group): the UI shows 5 and the rest on demand
TOP = 25
# ratio leaderboards: (columns of leaderboards(): successes, others; minimum of both together)
RATIOS = {
    "tiebreaks": ("tb_won", "tb_lost", 10),  # tiebreak sets (7-6)
    "super_tiebreaks": ("mtb_won", "mtb_lost", 5),  # match tiebreaks
    "three_sets": ("three_won", "three_lost", 15),  # three-set matches
    "comebacks": ("back_won", "back_lost", 25),  # matches after losing the first set
    "bagels": ("bagels", "other_sets", 50),  # sets won 6-0 among the sets played
}
# upsets and movers are filtered by gender and by group: the winner's / the starting category
GROUPS = {"N1-R3": CATEGORIES[:7], "R4-R9": CATEGORIES[7:]}
LEVEL = {c: i for i, c in enumerate(CATEGORIES)}


def list_at(history: pd.DataFrame, date: pd.Timestamp) -> pd.DataFrame:
    """The published list of `date`, index personId: classification, rank, W, C."""
    h = history[history["date"].dt.normalize() == date].drop_duplicates("personId", keep="last")
    return h.set_index("personId")[["classification", "rank", "W", "C"]]


def _board_rows(matches: pd.DataFrame, today: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The results counted by the leaderboards (last LEADERBOARD_YEARS, walkovers left out: no
    score and no streak), by player and date, and per result: tiebreak sets (7-6), match
    tiebreak, three-set match and match after losing the first set, won and lost; sets won 6-0
    and the other sets (a match tiebreak is no set); the points of a match tiebreak (mtb_mine,
    mtb_theirs, 0 without one); the run of equal results it belongs to."""
    m = matches[(matches["date"] >= today - pd.DateOffset(years=LEADERBOARD_YEARS)) & (matches["date"] < today + pd.Timedelta(days=1))]
    m = dedupe(m[m["playerWinnerCode"].isin(WIN_CODES + LOSS_CODES)])
    m = m.sort_values(["personId", "date"], kind="stable")
    win = m["playerWinnerCode"].isin(WIN_CODES).to_numpy()
    normal = m["playerWinnerCode"].isin(["S", "N"]).to_numpy()
    mine, theirs = _sets(m)
    done = (mine >= 0) & (theirs >= 0)
    tb = done & (np.minimum(mine, theirs) == 6) & (np.maximum(mine, theirs) == 7)
    mtb = done[:, 2] & (np.maximum(mine[:, 2], theirs[:, 2]) >= MATCH_TIEBREAK_MIN)
    sets = done.copy()
    sets[:, 2] &= ~mtb
    bagels = (sets & (mine == 6) & (theirs == 0)).sum(axis=1)
    three = normal & (done.sum(axis=1) == 3)
    first_lost = normal & done[:, 0] & (mine[:, 0] < theirs[:, 0])
    pid = m["personId"].to_numpy()
    d = pd.DataFrame({
        "personId": pid,
        "date": m["date"].to_numpy(),
        "win": win,
        # a new run starts at each change of player or of result
        "run": np.cumsum(np.r_[True, (pid[1:] != pid[:-1]) | (win[1:] != win[:-1])])[: len(m)],
        "tb_won": (tb & (mine > theirs)).sum(axis=1),
        "tb_lost": (tb & (mine < theirs)).sum(axis=1),
        "three_won": three & win,
        "three_lost": three & ~win,
        "mtb_won": mtb & (mine[:, 2] > theirs[:, 2]),
        "mtb_lost": mtb & (mine[:, 2] < theirs[:, 2]),
        "back_won": first_lost & win,
        "back_lost": first_lost & ~win,
        "bagels": bagels,
        "other_sets": sets.sum(axis=1) - bagels,
        "mtb_mine": np.where(mtb, mine[:, 2], 0),
        "mtb_theirs": np.where(mtb, theirs[:, 2], 0),
    })
    return m, d


def _best_runs(d: pd.DataFrame) -> pd.DataFrame:
    """Per player (index) the longest run of wins of _board_rows(), the latest of equal ones:
    its run, length (streak), first and last date and whether it is still running."""
    runs = (
        d[d["win"]]
        .groupby("run")
        .agg(personId=("personId", "first"), streak=("date", "size"), start=("date", "min"), end=("date", "max"))
    )
    best = runs.reset_index().sort_values(["streak", "end"], ascending=False).drop_duplicates("personId")
    last_run = d.groupby("personId")["run"].max()
    best["ongoing"] = best["run"].to_numpy() == last_run.reindex(best["personId"]).to_numpy()
    return best.set_index("personId")


def _longest_mtb(d: pd.DataFrame) -> pd.DataFrame:
    """Per player (index) the match tiebreak of _board_rows() with the most points, the latest of
    equal ones: its row, points, the player's and the opponent's points and date."""
    t = d[d["mtb_mine"] + d["mtb_theirs"] > 0].assign(mtb_points=lambda t: t["mtb_mine"] + t["mtb_theirs"])
    t = t.rename_axis("row").reset_index().sort_values(["mtb_points", "date"], kind="stable")
    return t.drop_duplicates("personId", keep="last").set_index("personId")[["row", "mtb_points", "mtb_mine", "mtb_theirs", "date"]]


def leaderboards(matches: pd.DataFrame, today: pd.Timestamp) -> pd.DataFrame:
    """Per player, the counts of _board_rows() summed, the longest run of wins (streak, start,
    end, ongoing) and the longest match tiebreak (mtb_points, mtb_mine, mtb_theirs, mtb_date)."""
    _, d = _board_rows(matches, today)
    out = d.drop(columns=["date", "win", "run", "mtb_mine", "mtb_theirs"]).groupby("personId").sum()
    longest = _longest_mtb(d).drop(columns="row").rename(columns={"date": "mtb_date"})
    return out.join(_best_runs(d)[["streak", "start", "end", "ongoing"]]).join(longest)


def board_matches(matches: pd.DataFrame, today: pd.Timestamp, key: str) -> pd.DataFrame:
    """The results behind a player's row on the `key` leaderboard (`matches`: the player's results)."""
    m, d = _board_rows(matches, today)
    if key == "streak":
        keep = d["run"].isin(_best_runs(d)["run"])
    elif key == "longest_super_tiebreak":
        keep = pd.Series(d.index.isin(_longest_mtb(d)["row"]), index=d.index)
    else:
        # bagels: the matches with a 6-0 set (the other sets are every match)
        cols = list(RATIOS[key][:1] if key == "bagels" else RATIOS[key][:2])
        keep = (d[cols] > 0).any(axis=1)
    return m[keep.to_numpy()]


def upsets(matches: pd.DataFrame, history: pd.DataFrame, start: pd.Timestamp, today: pd.Timestamp) -> pd.DataFrame:
    """Wins (not walkovers or retirements) from `start` to today against a player of a higher
    category, both on the list in force at the match; biggest category gap first, then W gap.
    `row`: the result's row in `matches`."""
    m = matches[(matches["date"] >= start) & (matches["date"] < today + pd.Timedelta(days=1))]
    m = describe(dedupe(m[(m["playerWinnerCode"] == "S") & m["adversaryPersonId"].notna()])).sort_values("date")
    m = m.assign(adversaryPersonId=m["adversaryPersonId"].astype("int64"), row=m.index)
    ids = set(m["personId"]) | set(m["adversaryPersonId"])
    h = history.loc[history["personId"].isin(ids), ["personId", "date", "classification", "W"]].dropna()
    h = h[h["classification"].isin(CATEGORIES)].sort_values("date")
    m = pd.merge_asof(m, h.rename(columns={"classification": "own_class", "W": "own_W"}), on="date", by="personId")
    opp = h.rename(columns={"personId": "adversaryPersonId", "classification": "opp_class", "W": "opp_W"})
    m = pd.merge_asof(m, opp, on="date", by="adversaryPersonId").dropna(subset=["own_class", "opp_class"])
    m = m.assign(
        levels=m["own_class"].map(LEVEL) - m["opp_class"].map(LEVEL),
        W_gap=m["opp_W"] - m["own_W"],
    )
    return m[m["levels"] > 0].sort_values(["levels", "W_gap"], ascending=False)


def placed_players(
    history: pd.DataFrame, matches: pd.DataFrame, players: pd.DataFrame, date: pd.Timestamp, cache: Path
) -> pd.Index:
    """Players whose category on the official list of `date` Swiss Tennis set instead of the
    formula: new players, players classified by evaluation (Art. 6.1) and foreigners, as the
    list's reproduction finds them. Saved to `cache` (the reproduction takes seconds and a
    published list does not change)."""
    if cache.exists():
        return pd.Index(pd.read_parquet(cache)["personId"])
    p = evaluate.evaluate(evaluate.build_period(history, matches, players, date), predict=False)
    ids = p.loc[p["classified"] | p["foreign"], ["personId"]]
    tmp = cache.with_suffix(".tmp")
    ids.to_parquet(tmp)
    tmp.rename(cache)
    return pd.Index(ids["personId"])


def movers(before: pd.DataFrame, after: pd.DataFrame) -> pd.DataFrame:
    """Players who went up from `before` to `after` (both index personId: class, rank, C):
    categories gained first, then C gained. Only the players on both lists, rated on the first
    (a C at the floor is the placeholder of players without results: their first win is no move)."""
    b, a = before[before["class"].isin(CATEGORIES)], after[after["class"].isin(CATEGORIES)]
    d = b.join(a, lsuffix="_from", rsuffix="_to", how="inner").dropna(subset=["C_from", "C_to"])
    d = d[d["C_from"] > FLOOR_START_VALUE]
    d = d.assign(levels=d["class_from"].map(LEVEL) - d["class_to"].map(LEVEL), gain=d["C_to"] - d["C_from"])
    d = d[(d["levels"] > 0) | ((d["levels"] == 0) & (d["gain"] > 0))]
    return d.sort_values(["levels", "gain"], ascending=False)


def record(matches: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Matches won and lost per player (index) from `start` to the day before `end`, walkovers
    left out."""
    m = matches[(matches["date"] >= start) & (matches["date"] < end)]
    m = dedupe(m[m["playerWinnerCode"].isin(WIN_CODES + LOSS_CODES)])
    win = m["playerWinnerCode"].isin(WIN_CODES)
    return pd.DataFrame({"personId": m["personId"], "won": win, "lost": ~win}).groupby("personId").sum()


def _num(v, digits: int = 3):
    return None if v is None or pd.isna(v) else round(float(v), digits)


def _int(v):
    return None if v is None or pd.isna(v) else int(v)


def group_of(cls: str) -> str:
    return next(g for g, cats in GROUPS.items() if cls in cats)


def _top(d: pd.DataFrame, n: int = TOP) -> pd.DataFrame:
    """The first n rows (in the current order) per gender and group."""
    return d.groupby(["gender", "group"], sort=False).head(n)


def _person(people: pd.DataFrame, pid) -> dict:
    return {"id": int(pid), "name": people.at[pid, "name"]}


def build(
    history: pd.DataFrame,
    matches: pd.DataFrame,
    today_list: pd.DataFrame,
    people: pd.DataFrame,
    official_date: pd.Timestamp,
    previous_date: pd.Timestamp | None,
    placed: pd.Index,
    today: pd.Timestamp,
    match_gain: Callable[[int, int], float | None],
) -> dict:
    """The highlights as JSON. `today_list` is today's calculated list (index personId: class,
    rank, C); `people` has name and gender (index personId); `placed` are the players whose
    category on the official list Swiss Tennis set (left out of last season's movers);
    `match_gain(personId, row)` is the C a result (its row in `matches`) brings today."""
    genders = [g for g in ("M", "F") if (people["gender"] == g).any()]

    official = list_at(history, official_date).rename(columns={"classification": "class"})
    # category shown next to the name: the official list in force, else today's calculated one
    category = official["class"].combine_first(today_list["class"])

    board = leaderboards(matches, today)
    board = board[board.index.isin(people.index)].join(people[["name", "gender"]])

    def ranked_person(pid) -> dict:
        cls = category.get(pid)
        return {**_person(people, pid), "class": None if pd.isna(cls) else str(cls)}

    def ratio_board(g: pd.DataFrame, won: str, lost: str, min_played: int) -> list[dict]:
        g = g.assign(played=g[won] + g[lost])
        g = g[g["played"] >= min_played]
        g = g.assign(ratio=g[won] / g["played"]).sort_values(["ratio", "played"], ascending=False).head(TOP)
        return [
            {"player": ranked_person(pid), "value": round(float(r["ratio"]), 4), "won": int(r[won]), "lost": int(r[lost])}
            for pid, r in g.iterrows()
        ]

    def streak_board(g: pd.DataFrame) -> list[dict]:
        g = g.dropna(subset=["streak"]).sort_values(["streak", "end"], ascending=False).head(TOP)
        return [
            {
                "player": ranked_person(pid),
                "value": int(r["streak"]),
                "start": r["start"].date().isoformat(),
                "end": r["end"].date().isoformat(),
                "ongoing": bool(r["ongoing"]),
            }
            for pid, r in g.iterrows()
        ]

    def mtb_board(g: pd.DataFrame) -> list[dict]:
        g = g.dropna(subset=["mtb_points"]).sort_values(["mtb_points", "mtb_date"], ascending=False).head(TOP)
        return [
            {
                "player": ranked_person(pid),
                "value": int(r["mtb_points"]),
                "score": f"{int(r['mtb_mine'])}-{int(r['mtb_theirs'])}",
                "date": r["mtb_date"].date().isoformat(),
            }
            for pid, r in g.iterrows()
        ]

    boards = {
        g: {
            **{key: ratio_board(board[board["gender"] == g], *cols) for key, cols in RATIOS.items()},
            "streak": streak_board(board[board["gender"] == g]),
            "longest_super_tiebreak": mtb_board(board[board["gender"] == g]),
        }
        for g in genders
    }

    week = today - pd.Timedelta(days=today.weekday())  # this Monday
    start = week - pd.Timedelta(days=7)
    up = upsets(matches, history, start, today)
    up = up[up["personId"].isin(people.index)]
    up = _top(up.assign(gender=up["personId"].map(people["gender"]), group=up["own_class"].map(group_of)))
    upset_rows = [
        {
            "gender": r["gender"],
            "group": r["group"],
            "date": r["date"].date().isoformat(),
            "tournament": None if pd.isna(r["tournamentName"]) else str(r["tournamentName"]),
            "winner": {**_person(people, r["personId"]), "class": r["own_class"], "W": _num(r["own_W"])},
            "loser": {
                "id": int(r["adversaryPersonId"]),
                "name": people.at[r["adversaryPersonId"], "name"] if r["adversaryPersonId"] in people.index
                else " ".join(str(x) for x in (r["adversaryFirstname"], r["adversaryLastname"]) if not pd.isna(x)),
                "class": r["opp_class"],
                "W": _num(r["opp_W"]),
            },
            "levels": int(r["levels"]),
            "score": r["score"],
            "C": _num(match_gain(int(r["personId"]), r["row"])),
        }
        for _, r in up.iterrows()
    ]

    def mover_rows(before: pd.DataFrame, after: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> list[dict]:
        d = movers(before[["class", "rank", "C"]], after[["class", "rank", "C"]]).join(people[["name", "gender"]], how="inner")
        d = _top(d.assign(group=d["class_from"].map(group_of)))
        d = d.join(record(matches, start, end)).fillna({"won": 0, "lost": 0})
        return [
            {
                "gender": r["gender"],
                "group": r["group"],
                "player": _person(people, pid),
                "from": {"class": r["class_from"], "rank": _int(r["rank_from"]), "C": _num(r["C_from"])},
                "to": {"class": r["class_to"], "rank": _int(r["rank_to"]), "C": _num(r["C_to"])},
                "levels": int(r["levels"]),
                "gain": _num(r["gain"]),
                "won": int(r["won"]),
                "lost": int(r["lost"]),
            }
            for pid, r in d.iterrows()
        ]

    movers_json = {"today": {"from": official_date.date().isoformat(), "to": today.date().isoformat(), "players": mover_rows(official, today_list, official_date, today + pd.Timedelta(days=1))}}
    if previous_date is not None:
        previous = list_at(history, previous_date).rename(columns={"classification": "class"})
        earned = official[~official.index.isin(placed)]
        movers_json["season"] = {"from": previous_date.date().isoformat(), "to": official_date.date().isoformat(), "players": mover_rows(previous, earned, previous_date, official_date)}

    return {
        "leaderboards": {"since": (today - pd.DateOffset(years=LEADERBOARD_YEARS)).date().isoformat(), "min_played": {key: n for key, (_, _, n) in RATIOS.items()}, "players": boards},
        "upsets": {"from": start.date().isoformat(), "to": today.date().isoformat(), "matches": upset_rows},
        "movers": movers_json,
    }
