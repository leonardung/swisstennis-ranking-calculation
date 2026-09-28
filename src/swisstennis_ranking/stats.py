"""Per-player match statistics (results, sets, games, tiebreaks, opponents) for the web UI."""

import numpy as np
import pandas as pd

from .evaluate import CATEGORIES, LOSS_CODES, WIN_CODES

WALKOVER_WIN, WALKOVER_LOSS = "1", "0"
RETIRED = ["W", "Z"]
# first digit of the result source
TYPES = {1: "interclub", 2: "interclub", 3: "interclub", 4: "tournament", 6: "abroad", 7: "club"}
# a third set won with 10 or more points is a match tiebreak
MATCH_TIEBREAK_MIN = 10
# wins against the strongest opponents shown in a career
CAREER_BEST_WINS = 3


def _sets(m: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Games per set (rows x 3) of the player and the opponent; -1 for a set not played."""
    mine = m[[f"playerSet{i}WonGames" for i in (1, 2, 3)]].fillna(-1).to_numpy(dtype=int)
    theirs = m[[f"adversarySet{i}WonGames" for i in (1, 2, 3)]].fillna(-1).to_numpy(dtype=int)
    return mine, theirs


def describe(m: pd.DataFrame) -> pd.DataFrame:
    """Add result (win/loss), how (normal/walkover/retired), type and score to result rows.
    Rows with another code (not a win or a loss) are dropped."""
    code = m["playerWinnerCode"]
    m = m[code.isin(WIN_CODES + LOSS_CODES + [WALKOVER_WIN, WALKOVER_LOSS])].copy()
    code = m["playerWinnerCode"]
    m["result"] = np.where(code.isin(WIN_CODES + [WALKOVER_WIN]), "win", "loss")
    m["how"] = np.select([code.isin([WALKOVER_WIN, WALKOVER_LOSS]), code.isin(RETIRED)], ["walkover", "retired"], "normal")
    m["type"] = (m["source"] // 100).map(TYPES).fillna("other")
    mine, theirs = _sets(m)
    m["score"] = [
        " ".join(f"{a}-{b}" for a, b in zip(x, y) if a >= 0 and b >= 0) for x, y in zip(mine, theirs)
    ]
    return m


def with_categories(m: pd.DataFrame, history: pd.DataFrame, player_id: int) -> pd.DataFrame:
    """Add the player's and the opponent's category and rank and the opponent's W (own_class,
    own_rank, opp_class, opp_rank, opp_W) on the list in force at each match."""
    m = m.sort_values("date")
    # only the rows of the player and the opponents: sorting the whole history costs seconds
    ids = m["adversaryPersonId"].dropna().astype(int).tolist() + [player_id]
    h = history.loc[history["personId"].isin(ids), ["personId", "date", "classification", "rank", "W"]]
    h = h.dropna(subset=["classification", "W"]).sort_values("date")
    own = h[h["personId"] == player_id]
    own = own[["date", "classification", "rank"]].rename(columns={"classification": "own_class", "rank": "own_rank"})
    m = pd.merge_asof(m, own, on="date")
    opp = h[h["personId"].isin(m["adversaryPersonId"].dropna())].rename(
        columns={"personId": "adversaryPersonId", "classification": "opp_class", "rank": "opp_rank", "W": "opp_W"}
    )
    m = m.assign(adversaryPersonId=m["adversaryPersonId"].astype("Int64"))
    opp = opp.assign(adversaryPersonId=opp["adversaryPersonId"].astype("Int64"))
    return pd.merge_asof(m, opp, on="date", by="adversaryPersonId")


def _won_lost(m: pd.DataFrame, by: str) -> list[dict]:
    t = m.groupby(by)["result"].value_counts().unstack(fill_value=0)
    return [
        {by: k.item() if hasattr(k, "item") else k, "won": int(r.get("win", 0)), "lost": int(r.get("loss", 0))}
        for k, r in t.iterrows()
    ]


def _streaks(results: list[str]) -> dict:
    longest = {"win": 0, "loss": 0}
    run, prev = 0, None
    for r in results:
        run = run + 1 if r == prev else 1
        prev = r
        longest[r] = max(longest[r], run)
    return {"current": run, "current_type": prev, "longest_win": longest["win"], "longest_loss": longest["loss"]}


def per_match(m: pd.DataFrame) -> pd.DataFrame:
    """Per match (describe()d), how many of each statistic the player won and lost: columns
    <key>_won and <key>_lost. Sets, games, tiebreaks, 7-5 sets, 6-0 and 6-1 sets count per set or
    game; the rest is 0 or 1."""
    win = (m["result"] == "win").to_numpy()
    walk = (m["how"] == "walkover").to_numpy()
    mine, theirs = _sets(m)
    done = (mine >= 0) & (theirs >= 0) & ~walk[:, None]
    set_won, set_lost = done & (mine > theirs), done & (mine < theirs)
    mtb = done[:, 2] & (np.maximum(mine[:, 2], theirs[:, 2]) >= MATCH_TIEBREAK_MIN)
    games = done.copy()
    games[:, 2] &= ~mtb
    n_sets = done.sum(axis=1)
    tb = done & (((mine == 7) & (theirs == 6)) | ((mine == 6) & (theirs == 7)))
    s75 = done & (((mine == 7) & (theirs == 5)) | ((mine == 5) & (theirs == 7)))
    decider = (n_sets == 3) & ~mtb
    # retired matches may end after one set: only complete two-set or three-set matches count there
    complete = (m["how"] == "normal").to_numpy()

    def wl(mask):
        return mask & win, mask & ~win

    def score(a, b):
        return (games & (mine == a) & (theirs == b)).sum(axis=1), (games & (mine == b) & (theirs == a)).sum(axis=1)

    counts = {
        "matches": (win, ~win),
        "sets": (set_won.sum(axis=1), set_lost.sum(axis=1)),
        "games": (np.where(games, mine, 0).sum(axis=1), np.where(games, theirs, 0).sum(axis=1)),
        "two_sets": wl(complete & (n_sets == 2)),
        "three_sets": wl(complete & (n_sets == 3)),
        "after_won_first": wl(complete & set_won[:, 0]),
        "after_lost_first": wl(complete & set_lost[:, 0]),
        "deciding_set": (decider & set_won[:, 2], decider & set_lost[:, 2]),
        "match_tiebreak": (mtb & set_won[:, 2], mtb & set_lost[:, 2]),
        "tiebreaks": ((tb & set_won).sum(axis=1), (tb & set_lost).sum(axis=1)),
        "sets_7_5": ((s75 & set_won).sum(axis=1), (s75 & set_lost).sum(axis=1)),
        "bagels": score(6, 0),
        "breadsticks": score(6, 1),
        "walkovers": wl(walk),
        "retired": wl((m["how"] == "retired").to_numpy()),
    }
    return pd.DataFrame(
        {f"{k}_{side}": np.asarray(v, dtype=int) for k, wl_ in counts.items() for side, v in zip(("won", "lost"), wl_)},
        index=m.index,
    )


def _relation(m: pd.DataFrame) -> pd.Series:
    """The opponent's category compared with the player's: higher, same, lower or unknown."""
    rel = pd.Series("unknown", index=m.index)
    both = m["own_class"].isin(CATEGORIES) & m["opp_class"].isin(CATEGORIES)
    level = {c: i for i, c in enumerate(CATEGORIES)}
    diff = m.loc[both, "opp_class"].map(level) - m.loc[both, "own_class"].map(level)
    rel[both] = np.select([diff < 0, diff > 0], ["higher", "lower"], "same")
    return rel


# the groups of player_stats(): the value of each match
GROUPS = {
    "by_relation": _relation,
    "by_class": lambda m: m["opp_class"],
    "by_type": lambda m: m["type"],
    "by_year": lambda m: m["date"].dt.year.astype(str),
    "by_month": lambda m: m["date"].dt.strftime("%Y-%m"),
}


def _streak_rows(result: pd.Series, which: str) -> np.ndarray:
    """Rows of the current run of equal results ("current"), or of the longest run of "win" or
    "loss" (the latest of equal ones)."""
    r = result.to_numpy()
    if len(r) == 0:
        return np.zeros(0, bool)
    run = np.cumsum(np.r_[True, r[1:] != r[:-1]])
    if which == "current":
        return run == run[-1]
    size = np.bincount(run)[run]
    of = r == which
    if not of.any():
        return np.zeros(len(r), bool)
    return run == run[of & (size == size[of].max())][-1]


def matches_of(m: pd.DataFrame, key: str, value: str | None = None) -> pd.DataFrame:
    """The matches (of player_stats()'s input) behind one statistic: a per_match() key, a GROUPS
    key and its value, or "streak" and current, win or loss. KeyError for an unknown key."""
    if key == "streak":
        if value not in ("current", "win", "loss"):
            raise KeyError(value)
        return m[_streak_rows(m["result"], value)]
    if key in GROUPS:
        return m[(GROUPS[key](m).astype(str) == value).to_numpy()]
    c = per_match(m)
    return m[((c[f"{key}_won"] + c[f"{key}_lost"]) > 0).to_numpy()]


def at_the_time(r) -> tuple[dict, dict]:
    """The opponent (id, name) and both players' category and rank on the list in force at the
    match of a with_categories()d row."""
    opponent = {"id": _id(r["adversaryPersonId"]), "name": _name(r), "class": _str(r["opp_class"]), "rank": _id(r["opp_rank"])}
    return opponent, {"class": _str(r["own_class"]), "rank": _id(r["own_rank"])}


def match_list(m: pd.DataFrame) -> list[dict]:
    """Matches (describe()d, with_categories()d) for a list, the latest first."""
    out = []
    for _, r in m.sort_values("date", ascending=False, kind="stable").iterrows():
        opponent, own = at_the_time(r)
        out.append(
            {
                "date": r["date"].date().isoformat(),
                "tournament": _str(r["tournamentName"]),
                "opponent": opponent,
                "own": own,
                "score": r["score"],
                "result": r["result"],
                "how": r["how"],
            }
        )
    return out


def _valued(m: pd.DataFrame) -> pd.DataFrame:
    """The rows with a known opponent strength (value): W on the list in force, else the value
    stored with the result."""
    value = m["opp_W"].where(m["opp_W"].notna(), m["adversaryValue"].where(m["adversaryValue"] > 0))
    return m.assign(value=value).dropna(subset=["value"])


def career(m: pd.DataFrame) -> dict:
    """A player's whole career (describe()d and with_categories()d matches, in date order):
    matches, first and last year, the best wins and the first win against each category."""
    wins = m[(m["result"] == "win") & (m["how"] != "walkover")]
    best = _valued(wins).nlargest(CAREER_BEST_WINS, "value")

    def row(r) -> dict:
        opponent, own = at_the_time(r)
        return {
            "date": r["date"].date().isoformat(),
            "tournament": _str(r["tournamentName"]),
            "opponent": opponent,
            "own": own,
            "score": r["score"],
        }

    firsts = wins[wins["opp_class"].isin(CATEGORIES[:-1])].drop_duplicates("opp_class")
    firsts = firsts.sort_values("opp_class", key=lambda c: c.map(CATEGORIES.index))
    years = m["date"].dt.year
    return {
        "matches": len(m),
        "won": int((m["result"] == "win").sum()),
        "lost": int((m["result"] == "loss").sum()),
        "first_year": None if m.empty else int(years.min()),
        "last_year": None if m.empty else int(years.max()),
        "seasons": int(years.nunique()),
        "best_wins": [{**row(r), "opponent_value": _num(r["value"])} for _, r in best.iterrows()],
        "first_wins": [row(r) for _, r in firsts.iterrows()],
    }


def player_stats(m: pd.DataFrame) -> dict:
    """Statistics of a player's matches (describe()d and with_categories()d, in date order)."""
    c = per_match(m).sum()

    def wl(key: str) -> dict:
        return {"won": int(c[f"{key}_won"]), "lost": int(c[f"{key}_lost"])}

    def given(key: str) -> dict:
        return {"given": int(c[f"{key}_won"]), "received": int(c[f"{key}_lost"])}

    def after(key: str) -> dict:
        return {"played": int(c[f"{key}_won"] + c[f"{key}_lost"]), "won": int(c[f"{key}_won"])}

    played = m[m["how"] != "walkover"]

    def match_row(r) -> dict:
        return {
            "date": r["date"].date().isoformat(),
            "tournament": _str(r["tournamentName"]),
            "opponent": {"id": _id(r["adversaryPersonId"]), "name": _name(r), "class": _str(r["opp_class"])},
            "opponent_value": _num(r["value"]),
            "score": r["score"],
        }

    valued = _valued(played)
    best = valued[valued["result"] == "win"].nlargest(5, "value")
    worst = valued[valued["result"] == "loss"].nsmallest(5, "value")

    opponents = (
        m.assign(key=m["adversaryPersonId"].astype("Float64").fillna(-1), name=[_name(r) for _, r in m.iterrows()])
        .groupby(["key", "name"])
        .agg(won=("result", lambda s: int((s == "win").sum())), lost=("result", lambda s: int((s == "loss").sum())), last=("date", "max"))
        .reset_index()
    )
    opponents = opponents.assign(n=opponents["won"] + opponents["lost"]).sort_values(["n", "last"], ascending=False)

    return {
        "matches": {
            "played": len(m),
            **wl("matches"),
            "walkovers_won": int(c["walkovers_won"]),
            "walkovers_lost": int(c["walkovers_lost"]),
            "retired_won": int(c["retired_won"]),
            "retired_lost": int(c["retired_lost"]),
        },
        "sets": wl("sets"),
        "games": wl("games"),
        "two_sets": wl("two_sets"),
        "three_sets": wl("three_sets"),
        "after_first_set": {"won_first": after("after_won_first"), "lost_first": after("after_lost_first")},
        "deciding_set": wl("deciding_set"),
        "match_tiebreak": wl("match_tiebreak"),
        "tiebreaks": wl("tiebreaks"),
        "sets_7_5": wl("sets_7_5"),
        "bagels": given("bagels"),
        "breadsticks": given("breadsticks"),
        "streak": _streaks(list(m["result"])),
        "by_relation": _won_lost(m.assign(relation=_relation(m)), "relation"),
        "by_class": sorted(
            _won_lost(m.dropna(subset=["opp_class"]).rename(columns={"opp_class": "class"}), "class"),
            key=lambda r: CATEGORIES.index(r["class"]) if r["class"] in CATEGORIES else len(CATEGORIES),
        ),
        "by_type": _won_lost(m, "type"),
        "by_year": _won_lost(m.assign(year=m["date"].dt.year), "year"),
        "by_month": _won_lost(m.assign(month=m["date"].dt.strftime("%Y-%m")), "month"),
        "best_wins": [match_row(r) for _, r in best.iterrows()],
        "worst_losses": [match_row(r) for _, r in worst.iterrows()],
        "opponents": [
            {
                "id": None if r["key"] < 0 else int(r["key"]),
                "name": r["name"],
                "won": int(r["won"]),
                "lost": int(r["lost"]),
                "last_date": r["last"].date().isoformat(),
            }
            for _, r in opponents.iterrows()
        ],
    }


def _id(v) -> int | None:
    return None if pd.isna(v) else int(v)


def _num(v) -> float | None:
    return None if pd.isna(v) or v == 0 else round(float(v), 3)


def _str(v) -> str | None:
    return None if pd.isna(v) else str(v)


def _name(r) -> str:
    return " ".join(str(x) for x in (r["adversaryFirstname"], r["adversaryLastname"]) if not pd.isna(x)).strip()
