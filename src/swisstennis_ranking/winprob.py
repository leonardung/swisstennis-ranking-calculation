"""Win probability: an XGBoost model of a match's result from what is known before it.

Features, for the player (a_) and the opponent (b_): the official list in force (W, C, category,
rank, lists appeared on, W change since the previous list), the form from earlier days (career
and last 90 / 365 days' wins and matches, days since the last match), the age (the licence
number holds the birth year) and, for the match, the head to head so far, the gender and its
type (interclub, tournament...). Trained on all the results when a new official list is out
(stale()); a probability is the mean of the model from both sides, so the two players' add up
to 1.

Two models, each trained on one half of the matches (fold()): a played match gets the chance
from the model that did not see it (a model knows the results it was trained on too well), a
hypothetical one the mean of both.
"""

import os
from pathlib import Path

import numpy as np
import pandas as pd

from .evaluate import CATEGORIES, LOSS_CODES, WIN_CODES, dedupe

MODEL_FILES = ("winprob-0.json", "winprob-1.json")
# model attribute: the latest official list when it was trained
LIST_ATTR = "list_date"
# a hypothetical match (the simulator) is a tournament match: first digit of the result source
TOURNAMENT = 4
# licence numbers 903.96.276.0: born 1996; two digits above this are 19xx
BIRTH_CENTURY_CUT = 20
PARAMS = {
    "objective": "binary:logistic",
    "eta": 0.1,
    "max_depth": 7,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "tree_method": "hist",
    "max_bin": 128,
}
ROUNDS = 800
# features are computed, and given to the training, this many matches at a time (memory)
CHUNK = 100_000

SIDE = ["W", "C", "cls", "rank", "n_lists", "W_trend", "days_since", "career_n", "rate_career",
        "w90", "n90", "w365", "n365", "rate365", "age"]
FEATURES = [f"{s}_{c}" for s in "ab" for c in SIDE] + ["dW", "dC", "dcls", "list_age", "h2h_w", "h2h_l", "female", "type"]


def results(matches: pd.DataFrame) -> pd.DataFrame:
    """Played results (not walkovers) against a known opponent, one per match and side: personId,
    opp, date, type, win; index = the row in `matches`."""
    m = matches[matches["playerWinnerCode"].isin(WIN_CODES + LOSS_CODES) & matches["adversaryPersonId"].notna()]
    m = dedupe(m).drop_duplicates(["personId", "adversaryPersonId", "date", "tournamentName", "playerWinnerCode"])
    return pd.DataFrame(
        {
            "personId": m["personId"].astype("int64"),
            "opp": m["adversaryPersonId"].astype("int64"),
            "date": m["date"],
            "type": m["source"] // 100,
            "win": m["playerWinnerCode"].isin(WIN_CODES).astype("int8"),
        },
        index=m.index,
    )


def fold(q: pd.DataFrame) -> np.ndarray:
    """0 or 1 per match of `q` (personId, opp, date), the same from both sides."""
    a, b = np.minimum(q["personId"], q["opp"]), np.maximum(q["personId"], q["opp"])
    return (pd.util.hash_pandas_object(pd.DataFrame({"a": a, "b": b, "date": q["date"]}), index=False) % 2).to_numpy()


def people(players: pd.DataFrame) -> pd.DataFrame:
    """Index personId: birth year (from the licence number) and female (1 / 0)."""
    yy = pd.to_numeric(players["licenceNumber"].str.split(".").str[1], errors="coerce")
    return pd.DataFrame(
        {
            "birth": np.where(yy > BIRTH_CENTURY_CUT, 1900 + yy, 2000 + yy),
            "female": players["gender"].map({"F": 1.0, "M": 0.0}).to_numpy(),
        },
        index=pd.Index(players["personId"].to_numpy(), name="personId"),
    )


def _asof(q: pd.DataFrame, table: pd.DataFrame, by: list[str], days: int = 0, exact: bool = False) -> pd.DataFrame:
    """Per row of `q` (in its order), the last row of `table` (sorted by date) with the same `by`
    dated before q's date minus `days` (on that date too with `exact`)."""
    left = q[by].assign(date=q["date"] - pd.Timedelta(days=days), _i=np.arange(len(q))).sort_values("date")
    x = pd.merge_asof(left, table, on="date", by=by, allow_exact_matches=exact)
    return x.sort_values("_i").reset_index(drop=True)


def features(q: pd.DataFrame, res: pd.DataFrame, history: pd.DataFrame, born: pd.DataFrame) -> pd.DataFrame:
    """FEATURES of the matches `q` (personId, opp, date, type), in q's order. `res`: results()
    of at least both players of each match (the form counts those of earlier days); `history`:
    the official lists; `born`: people()."""
    q = q.reset_index(drop=True)
    day = res.groupby(["personId", "date"])["win"].agg(w="sum", n="size").reset_index()
    g = day.groupby("personId")
    day = day.assign(cw=g["w"].cumsum().astype("int32"), cn=g["n"].cumsum().astype("int32"), last=day["date"])
    day = day[["personId", "date", "last", "cw", "cn"]].sort_values("date")
    pair = res.groupby(["personId", "opp", "date"])["win"].agg(w="sum", n="size").reset_index()
    gp = pair.groupby(["personId", "opp"])
    pair = pair.assign(hw=gp["w"].cumsum().astype("int32"), hn=gp["n"].cumsum().astype("int32"))
    pair = pair[["personId", "opp", "date", "hw", "hn"]].sort_values("date")

    ids = pd.concat([q["personId"], q["opp"]]).unique()
    h = history.loc[history["personId"].isin(ids) & history["W"].notna()].sort_values("date")
    gh = h.groupby("personId")
    lists = pd.DataFrame(
        {
            "personId": h["personId"],
            "date": h["date"],
            "list_date": h["date"],
            "W": h["W"],
            "C": h["C"],
            "cls": h["classification"].map({c: i for i, c in enumerate(CATEGORIES)}).astype(float),
            "rank": h["rank"],
            "n_lists": gh.cumcount() + 1,
            "W_trend": h["W"] - gh["W"].shift(),
        }
    ).astype({c: "float32" for c in ("W", "C", "cls", "rank", "n_lists", "W_trend")})
    del h, gh
    _free()

    # filled in place: the whole table exists once (float32)
    out = np.empty((len(q), len(FEATURES)), dtype="float32")
    for i in range(0, len(q), CHUNK):
        out[i : i + CHUNK] = _features(q.iloc[i : i + CHUNK].reset_index(drop=True), day, pair, lists, born).to_numpy()
    return pd.DataFrame(out, columns=FEATURES, copy=False)


def _features(q: pd.DataFrame, day: pd.DataFrame, pair: pd.DataFrame, lists: pd.DataFrame, born: pd.DataFrame) -> pd.DataFrame:
    """features() of `q` from the tables it builds (each sorted by date)."""
    out = pd.DataFrame(index=q.index)
    list_date = {}
    for s, key in (("a", "personId"), ("b", "opp")):
        side = q[[key, "date"]].rename(columns={key: "personId"})
        now = _asof(side, day, ["personId"])
        cw, cn = now["cw"].fillna(0), now["cn"].fillna(0)
        for days in (90, 365):
            then = _asof(side, day, ["personId"], days)
            out[f"{s}_w{days}"] = cw - then["cw"].fillna(0)
            out[f"{s}_n{days}"] = cn - then["cn"].fillna(0)
        out[f"{s}_rate365"] = (out[f"{s}_w365"] + 1) / (out[f"{s}_n365"] + 2)
        out[f"{s}_days_since"] = (q["date"] - now["last"]).dt.days
        out[f"{s}_career_n"] = cn
        out[f"{s}_rate_career"] = (cw + 1) / (cn + 2)
        lst = _asof(side, lists, ["personId"], exact=True)
        for c in ("W", "C", "cls", "rank", "n_lists", "W_trend"):
            out[f"{s}_{c}"] = lst[c].to_numpy()
        list_date[s] = lst["list_date"]
        out[f"{s}_age"] = q["date"].dt.year - side["personId"].map(born["birth"])
    out["dW"], out["dC"], out["dcls"] = out["a_W"] - out["b_W"], out["a_C"] - out["b_C"], out["a_cls"] - out["b_cls"]
    out["list_age"] = (q["date"] - list_date["a"].combine_first(list_date["b"])).dt.days

    h2h = _asof(q[["personId", "opp", "date"]], pair, ["personId", "opp"])
    out["h2h_w"] = h2h["hw"].fillna(0)
    out["h2h_l"] = h2h["hn"].fillna(0) - out["h2h_w"]
    out["female"] = q["personId"].map(born["female"]).combine_first(q["opp"].map(born["female"]))
    out["type"] = q["type"].astype(float)
    return out[FEATURES].astype("float32")


def _latest_list(data_dir: Path) -> str:
    import pyarrow.parquet as pq

    return pq.read_table(data_dir / "history.parquet", columns=["date"])["date"].to_pandas().max().date().isoformat()


def stale(data_dir: Path) -> bool:
    """True without a model, or when it was trained before the latest official list."""
    import xgboost as xgb

    try:
        attrs = []
        for name in MODEL_FILES:
            b = xgb.Booster()
            b.load_model(data_dir / name)
            attrs.append(b.attr(LIST_ATTR))
    except Exception:  # missing or unreadable
        return True
    return any(a != _latest_list(data_dir) for a in attrs)


def _free() -> None:
    """Give the memory freed so far back to the system (the training runs on a small server)."""
    import ctypes

    import pyarrow as pa

    pa.default_memory_pool().release_unused()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except OSError:  # not glibc
        pass


def train(data_dir: Path) -> None:
    """Fit the two models (one per fold) on all the results, saved to data_dir."""
    import pyarrow.parquet as pq
    import xgboost as xgb

    cols = ["personId", "adversaryPersonId", "date", "playerWinnerCode", "source", "encounterId", "tournamentName"]
    # names as categories: a fraction of the memory
    played = [("playerWinnerCode", "in", WIN_CODES + LOSS_CODES)]
    matches = pq.read_table(data_dir / "matches.parquet", columns=cols, filters=played, read_dictionary=["tournamentName"])
    res = results(matches.to_pandas(self_destruct=True, split_blocks=True))
    del matches
    _free()
    history = pq.read_table(
        data_dir / "history.parquet", columns=["personId", "date", "classification", "rank", "W", "C"], read_dictionary=["classification"]
    ).to_pandas(self_destruct=True, split_blocks=True)
    born = people(pd.read_parquet(data_dir / "players.parquet", columns=["personId", "licenceNumber", "gender"]))
    latest = history["date"].max().date().isoformat()
    X, y, f = features(res, res, history, born).to_numpy(), res["win"].to_numpy(), fold(res)
    del res, history
    _free()

    class Rows(xgb.DataIter):
        """Rows `rows` of X and y, CHUNK at a time: a fold is never copied whole."""

        def __init__(self, rows: np.ndarray):
            self.rows, self.i = rows, 0
            super().__init__()

        def next(self, input_data) -> bool:
            if self.i >= len(self.rows):
                return False
            r = self.rows[self.i : self.i + CHUNK]
            input_data(data=X[r], label=y[r], feature_names=FEATURES)
            self.i += CHUNK
            return True

        def reset(self) -> None:
            self.i = 0

    for k, name in enumerate(MODEL_FILES):
        data = xgb.QuantileDMatrix(Rows(np.flatnonzero(f == k)), max_bin=PARAMS["max_bin"])
        model = xgb.train({**PARAMS, "nthread": os.cpu_count()}, data, ROUNDS)
        del data
        model.set_attr(**{LIST_ATTR: latest})
        tmp = data_dir / f"tmp-{name}"  # the extension picks the format
        model.save_model(tmp)
        os.replace(tmp, data_dir / name)


class Model:
    """The saved models: chance() of the player of each match to win it."""

    def __init__(self, data_dir: Path):
        import xgboost as xgb

        self.boosters = []
        for name in MODEL_FILES:
            b = xgb.Booster()
            b.load_model(data_dir / name)
            b.set_param({"nthread": 1})
            self.boosters.append(b)

    def chance(self, q: pd.DataFrame, res: pd.DataFrame, history: pd.DataFrame, born: pd.DataFrame, played: bool) -> np.ndarray:
        """Probability that personId beats opp in each match of `q` (see features()), the mean of
        the model from both sides. `played`: matches already played, each by the model of the
        other fold; else the mean of both models."""
        if q.empty:
            return np.array([])
        # both sides in one pass: features() builds its tables (form, head to head, lists) per call
        both = features(pd.concat([q, q.rename(columns={"personId": "opp", "opp": "personId"})], ignore_index=True), res, history, born)
        a, b = both.iloc[: len(q)], both.iloc[len(q) :]
        p = [(m.inplace_predict(a) + 1 - m.inplace_predict(b)) / 2 for m in self.boosters]
        return np.where(fold(q) == 0, p[1], p[0]) if played else (p[0] + p[1]) / 2
