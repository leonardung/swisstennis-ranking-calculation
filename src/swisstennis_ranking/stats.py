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
    """Add the player's and the opponent's category and the opponent's W (opp_class, opp_W) on
    the list in force at each match."""
    h = history[["personId", "date", "classification", "W"]].dropna().sort_values("date")
    m = m.sort_values("date")
    own = h[h["personId"] == player_id]
    m = pd.merge_asof(m, own[["date", "classification"]].rename(columns={"classification": "own_class"}), on="date")
    opp = h[h["personId"].isin(m["adversaryPersonId"].dropna())].rename(
        columns={"personId": "adversaryPersonId", "classification": "opp_class", "W": "opp_W"}
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


def _count(mask) -> int:
    return int(np.count_nonzero(mask))


def player_stats(m: pd.DataFrame) -> dict:
    """Statistics of a player's matches (describe()d and with_categories()d, in date order)."""
    win = (m["result"] == "win").to_numpy()
    walk = (m["how"] == "walkover").to_numpy()
    ret = (m["how"] == "retired").to_numpy()
    played = m[~walk]
    pwin = (played["result"] == "win").to_numpy()
    mine, theirs = _sets(played)
    done = (mine >= 0) & (theirs >= 0)
    set_won, set_lost = done & (mine > theirs), done & (mine < theirs)
    mtb = done[:, 2] & (np.maximum(mine[:, 2], theirs[:, 2]) >= MATCH_TIEBREAK_MIN)
    games = done.copy()
    games[:, 2] &= ~mtb
    n_sets = done.sum(axis=1)
    first_won = set_won[:, 0]
    tb = done & (((mine == 7) & (theirs == 6)) | ((mine == 6) & (theirs == 7)))
    s75 = done & (((mine == 7) & (theirs == 5)) | ((mine == 5) & (theirs == 7)))
    decider = (n_sets == 3) & ~mtb
    # retired matches may end after one set: only complete two-set or three-set matches count there
    complete = played["how"].to_numpy() == "normal"

    rel = pd.Series("unknown", index=m.index)
    both = m["own_class"].isin(CATEGORIES) & m["opp_class"].isin(CATEGORIES)
    level = {c: i for i, c in enumerate(CATEGORIES)}
    diff = m.loc[both, "opp_class"].map(level) - m.loc[both, "own_class"].map(level)
    rel[both] = np.select([diff < 0, diff > 0], ["higher", "lower"], "same")

    def match_row(r) -> dict:
        return {
            "date": r["date"].date().isoformat(),
            "tournament": r["tournamentName"],
            "opponent": {"id": _id(r["adversaryPersonId"]), "name": _name(r), "class": _str(r["opp_class"])},
            "opponent_value": _num(r["value"]),
            "score": r["score"],
        }

    # opponent strength: W on the list in force, else the value stored with the result
    value = played["opp_W"].where(played["opp_W"].notna(), played["adversaryValue"].where(played["adversaryValue"] > 0))
    valued = played.assign(value=value).dropna(subset=["value"])
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
            "won": _count(win),
            "lost": _count(~win),
            "walkovers_won": _count(walk & win),
            "walkovers_lost": _count(walk & ~win),
            "retired_won": _count(ret & win),
            "retired_lost": _count(ret & ~win),
        },
        "sets": {"won": int(set_won.sum()), "lost": int(set_lost.sum())},
        "games": {
            "won": int(np.where(games, mine, 0).sum()),
            "lost": int(np.where(games, theirs, 0).sum()),
        },
        "two_sets": {"won": _count(complete & (n_sets == 2) & pwin), "lost": _count(complete & (n_sets == 2) & ~pwin)},
        "three_sets": {"won": _count(complete & (n_sets == 3) & pwin), "lost": _count(complete & (n_sets == 3) & ~pwin)},
        "after_first_set": {
            "won_first": {"played": _count(complete & first_won), "won": _count(complete & first_won & pwin)},
            "lost_first": {
                "played": _count(complete & set_lost[:, 0]),
                "won": _count(complete & set_lost[:, 0] & pwin),
            },
        },
        "deciding_set": {"won": _count(decider & set_won[:, 2]), "lost": _count(decider & set_lost[:, 2])},
        "match_tiebreak": {"won": _count(mtb & set_won[:, 2]), "lost": _count(mtb & set_lost[:, 2])},
        "tiebreaks": {"won": int((tb & set_won).sum()), "lost": int((tb & set_lost).sum())},
        "sets_7_5": {"won": int((s75 & set_won).sum()), "lost": int((s75 & set_lost).sum())},
        "bagels": {
            "given": int((games & (mine == 6) & (theirs == 0)).sum()),
            "received": int((games & (mine == 0) & (theirs == 6)).sum()),
        },
        "breadsticks": {
            "given": int((games & (mine == 6) & (theirs == 1)).sum()),
            "received": int((games & (mine == 1) & (theirs == 6)).sum()),
        },
        "streak": _streaks(list(m["result"])),
        "by_relation": _won_lost(m.assign(relation=rel), "relation"),
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
