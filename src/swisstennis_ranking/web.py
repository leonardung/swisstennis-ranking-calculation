"""Web UI backend: today's calculated list, per-match points, a win/loss simulator and statistics.

Today's list is the monthly list at today's date in predict mode (see evaluate.build_period). It
is computed at startup and again after every nightly scrape.
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import evaluate, highlights, ranking, stats, winprob

# a nightly scrape refetches the players with a result dated in these last days
RECENT_DAYS = 45
# feedback messages accepted per visitor (IP) per hour
FEEDBACK_PER_HOUR = 5
# "what do I need to move up": wins counted up to this many, against these percentiles of W
MOVE_UP_MAX_WINS = 15
MOVE_UP_PERCENTILES = (25, 50, 75)
# custom prediction: at most this many results of a kind, and in all
PREDICT_MAX_PER_ROW = 30
PREDICT_MAX_RESULTS = 60


class Feedback(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    name: str | None = Field(None, max_length=100)
    email: str | None = Field(None, max_length=200)
    page: str | None = Field(None, max_length=300)
    # hidden field of the form: only bots fill it in
    website: str | None = None


@dataclass
class State:
    today: pd.Timestamp
    updated: datetime
    history: pd.DataFrame
    matches: pd.DataFrame
    match_rows: dict  # personId -> positions in `matches`
    period: evaluate.Period
    p: pd.DataFrame  # today's list, index = player number of the period
    rows: pd.DataFrame  # the period's match rows with opp_w and counted
    player_rows: dict  # player number -> positions in `rows`
    number: pd.Series  # personId -> player number
    people: pd.DataFrame  # index personId: name, licence, gender, key (search text)
    official_date: pd.Timestamp
    official_class: pd.Series  # personId -> category on the official list in force
    highlights: dict  # home page: leaderboards, upsets of the week, biggest movers (JSON)
    # win probability of personId against opp in matches (personId, opp, date, type); None
    # without a trained model
    # (hypothetical: the mean of both models, see winprob.Model.chance)
    chance: Callable[[pd.DataFrame], np.ndarray | None]
    # the chance before each result (rows of `matches`) of its player; NaN for walkovers
    match_chance: Callable[[pd.Index], pd.Series]


def _fold(s: pd.Series) -> pd.Series:
    """Lower case without accents, for search."""
    return s.fillna("").map(lambda x: unicodedata.normalize("NFKD", x).encode("ascii", "ignore").decode().lower())


def load_state(data_dir: Path) -> State:
    history = pd.read_parquet(data_dir / "history.parquet")
    matches = pd.read_parquet(data_dir / "matches.parquet")
    players = pd.read_parquet(data_dir / "players.parquet")
    today = pd.Timestamp.now().normalize()

    period = evaluate.build_period(history, matches, players, today)
    p, rows = evaluate.evaluate_detailed(period, predict=True)

    last = history.sort_values("date").drop_duplicates("personId", keep="last").set_index("personId")
    seen = matches.dropna(subset=["adversaryPersonId"]).drop_duplicates("adversaryPersonId", keep="last")
    seen = seen.set_index(seen["adversaryPersonId"].astype(int))
    people = pd.DataFrame(index=pd.Index(p["personId"], name="personId"))
    for first, lastname, src in [
        ("adversaryFirstname", "adversaryLastname", seen),
        ("firstname", "lastname", last),
        ("firstname", "lastname", players.set_index("personId")),
    ]:
        name = (src[first].fillna("") + " " + src[lastname].fillna("")).str.strip()
        name = name.where(name != "")  # a later source only overrides with a real name
        people["name"] = name.reindex(people.index).combine_first(people.get("name", pd.Series(dtype=str)))
    people["licence"] = players.set_index("personId")["licenceNumber"].reindex(people.index)
    people["gender"] = p.set_index("personId")["gender"].to_numpy()
    people = people[people["name"].fillna("") != ""]
    people["key"] = _fold(people["name"]) + " " + people["licence"].fillna("").str.replace(".", "", regex=False)

    lists = [d for d in evaluate.publications(history) if d <= today]
    official_date = lists[-1]
    official = highlights.list_at(history, official_date)
    today_list = p[p["on_list"]].set_index("personId")[["class", "rank", "C"]]
    number = pd.Series(p.index, index=p["personId"])
    player_rows = rows.groupby("player").indices
    match_rows = matches.groupby("personId").indices
    model = _winprob_model(data_dir)
    born = winprob.people(players)

    def chance(q: pd.DataFrame, played: bool = False) -> np.ndarray | None:
        if model is None:
            return None
        ids = pd.unique(pd.concat([q["personId"], q["opp"]]))
        pos = np.concatenate([match_rows.get(i, np.array([], int)) for i in ids] + [np.array([], int)])
        return model.chance(q, winprob.results(matches.iloc[pos]), history, born, played)

    def match_chance(rows: pd.Index) -> pd.Series:
        q = winprob.results(matches.loc[rows])
        c = chance(q, played=True)
        return pd.Series(np.nan if c is None else c, index=q.index).reindex(rows)

    def match_gain(pid: int, row: int) -> float | None:
        """The C that the result in row `row` of `matches` brings to player `pid` today."""
        if pid not in number.index:
            return None
        x = rows.iloc[player_rows.get(number[pid], np.array([], int))]
        hit = np.flatnonzero(x["match"].to_numpy() == row)
        if not len(hit):
            return None
        _, dC = match_deltas(p.at[number[pid], "w0"], x["opp_w"].to_numpy(), x["win"].to_numpy())
        return dC[hit[0]]

    return State(
        today=today,
        updated=datetime.fromtimestamp((data_dir / "matches.parquet").stat().st_mtime),
        history=history,
        matches=matches,
        match_rows=match_rows,
        period=period,
        p=p,
        rows=rows,
        player_rows=player_rows,
        number=number,
        people=people,
        official_date=official_date,
        official_class=official["classification"],
        highlights=highlights.build(
            history,
            matches,
            today_list,
            people,
            official_date,
            lists[-2] if len(lists) > 1 else None,
            highlights.placed_players(
                history, matches, players, official_date, data_dir / f"placed_{official_date.date()}.parquet"
            ),
            today,
            match_gain,
            match_chance,
        ),
        chance=chance,
        match_chance=match_chance,
    )


def _winprob_model(data_dir: Path) -> winprob.Model | None:
    """The trained win probability model, None before the first training (or if it cannot load)."""
    try:
        return winprob.Model(data_dir) if all((data_dir / f).exists() for f in winprob.MODEL_FILES) else None
    except Exception as e:  # a broken file only costs the probabilities
        print(f"win probability model not loaded: {e!r}", file=sys.stderr)
        return None


def _num(v, digits: int = 3):
    return None if v is None or pd.isna(v) else round(float(v), digits)


def _int(v):
    return None if v is None or pd.isna(v) else int(v)


def _str(v):
    return None if v is None or pd.isna(v) else str(v)


def values(r: pd.Series) -> dict:
    return {"W": _num(r["W"]), "C": _num(r["C"]), "rank": _int(r["rank"]), "class": _str(r["class"])}


def first_list(date: pd.Timestamp) -> pd.Timestamp:
    """The first official list that uses a result played on `date` (its window ends the day before)."""
    return evaluate.next_list(date.normalize() + pd.Timedelta(days=1))


def match_deltas(w0: float, opp_w: np.ndarray, win: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Change of W and C due to each match: the values with all matches minus the values without
    that one, opponents valued as in the last pass (so the discarded losses are chosen again)."""

    def values(keep: np.ndarray) -> tuple[float, float]:
        W, R = ranking.single_pass(np.array([w0]), np.zeros(keep.sum(), int), opp_w[keep], win[keep])
        return W[0], W[0] + R[0]

    all_rows = np.ones(len(opp_w), bool)
    W, C = values(all_rows)
    dW, dC = np.empty(len(opp_w)), np.empty(len(opp_w))
    for j in range(len(opp_w)):
        keep = all_rows.copy()
        keep[j] = False
        Wj, Cj = values(keep)
        dW[j], dC[j] = W - Wj, C - Cj
    return dW, dC


class App:
    def __init__(self, data_dir: Path, scrape_time: str | None):
        self.data_dir = data_dir
        self.scrape_time = scrape_time
        self.state: State | None = None
        self.scrape = {"running": False, "last_run": None, "last_error": None, "next_run": None}
        self.feedback_lock = threading.Lock()
        self.feedback_times: dict[str, list[float]] = {}

    # --- lifecycle ---------------------------------------------------------------------------

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        if (self.data_dir / "history.parquet").exists():
            self._reload()
        else:
            self._scrape()
        if not self.scrape_time:
            return
        while True:
            hour, minute = map(int, self.scrape_time.split(":"))
            now = datetime.now()
            run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if run <= now:
                run += timedelta(days=1)
            self.scrape["next_run"] = run.isoformat(timespec="seconds")
            time.sleep((run - now).total_seconds())
            self._scrape(recent_days=RECENT_DAYS)

    def _scrape(self, recent_days: int | None = None) -> None:
        self.scrape.update(running=True, last_error=None)
        args = ["scrape"] + ([] if recent_days is None else ["--recent-days", str(recent_days)])
        # then the win probability model on the new results (its own process: its memory is freed)
        if self._command(args, "scrape.log"):
            self._command(["train-winprob"], "winprob.log")
        self.scrape.update(running=False, last_run=datetime.now().isoformat(timespec="seconds"))
        # also when the scrape failed: today's list moves with the date
        if (self.data_dir / "history.parquet").exists():
            self._reload()

    def _command(self, args: list[str], log_name: str) -> bool:
        """Run a CLI command on the data, output to data/log_name; False (and last_error) on failure."""
        cmd = [sys.executable, "-m", "swisstennis_ranking", "--data", str(self.data_dir), *args]
        log = self.data_dir / log_name
        try:
            with log.open("w") as out:
                done = subprocess.run(cmd, stdout=out, stderr=subprocess.STDOUT)
            if done.returncode:
                self.scrape["last_error"] = log.read_text(errors="replace")[-2000:]
            return done.returncode == 0
        except Exception as e:  # keep the scheduler alive
            self.scrape["last_error"] = repr(e)
            return False

    def _reload(self) -> None:
        try:
            self.state = load_state(self.data_dir)
        except Exception as e:
            self.scrape["last_error"] = f"loading data failed: {e!r}"

    def s(self) -> State:
        if self.state is None:
            raise HTTPException(503, "loading")
        return self.state

    # --- queries -----------------------------------------------------------------------------

    def feedback(self, f: Feedback, ip: str) -> None:
        """Append a visitor's message to data/feedback.jsonl (bots are dropped silently)."""
        if f.website:
            return
        now = time.time()
        with self.feedback_lock:
            recent = [t for t in self.feedback_times.get(ip, []) if now - t < 3600]
            if len(recent) >= FEEDBACK_PER_HOUR:
                raise HTTPException(429, "too many messages, try again later")
            self.feedback_times[ip] = recent + [now]
            entry = {"time": datetime.now().isoformat(timespec="seconds"), **f.model_dump(exclude={"website"})}
            with open(self.data_dir / "feedback.jsonl", "a", encoding="utf-8") as out:
                out.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def meta(self) -> dict:
        s = self.s()
        return {
            "today": s.today.date().isoformat(),
            "official_date": s.official_date.date().isoformat(),
            "next_official": evaluate.next_list(s.today).date().isoformat(),
            "window_start": s.period.start.date().isoformat(),
            "data_updated": s.updated.isoformat(timespec="seconds"),
            "scrape": self.scrape,
        }

    def _player(self, s: State, pid: int) -> pd.Series:
        if pid not in s.number.index or pid not in s.people.index:
            raise HTTPException(404, f"unknown player {pid}")
        return s.p.loc[s.number[pid]]

    def _official(self, s: State, pid: int) -> pd.Series | None:
        h = s.history[(s.history["personId"] == pid) & (s.history["date"].dt.normalize() == s.official_date)]
        return None if h.empty else h.iloc[-1]

    def search(self, q: str, limit: int) -> list[dict]:
        s = self.s()
        words = _fold(pd.Series([q.replace(".", "")])).iloc[0].split()
        if not words:
            return []
        key = s.people["key"]
        hit = np.ones(len(key), bool)
        for w in words:
            hit &= key.str.contains(w, regex=False).to_numpy()
        found = s.people[hit]
        # words matching the start of a name part first, then players on today's list, strongest first
        starts = sum(found["key"].str.contains(r"(?:^| )" + re.escape(w)).astype(int) for w in words)
        today = s.p.loc[s.number[found.index]].set_index("personId")
        found = found.assign(starts=starts, on_list=today["on_list"], C=today["C"]).sort_values(
            ["starts", "on_list", "C"], ascending=False
        )[:limit]
        official = s.official_class
        return [
            {
                "id": int(pid),
                "name": r["name"],
                "licence": _str(r["licence"]),
                "gender": _str(r["gender"]),
                "class_official": _str(official.get(pid)),
                "class_today": _str(today.loc[pid, "class"]) if r["on_list"] else None,
            }
            for pid, r in found.iterrows()
        ]

    def player(self, pid: int) -> dict:
        s = self.s()
        r = self._player(s, pid)
        person = s.people.loc[pid]
        off = self._official(s, pid)
        hist = s.history[s.history["personId"] == pid].sort_values("date")
        hist = hist.assign(day=hist["date"].dt.normalize()).drop_duplicates("day", keep="last")
        # the list in force, else the latest list the player was on
        last = off if off is not None else None if hist.empty else hist.iloc[-1]
        note = "classified" if r["classified"] else "assigned" if r["foreign"] and r["W5_prev"] >= evaluate.ASSIGNED_MIN_W5 else None
        return {
            "id": pid,
            "name": person["name"],
            "licence": _str(person["licence"]),
            "gender": _str(person["gender"]),
            "official": None
            if last is None
            else {
                "date": (s.official_date if off is not None else last["day"]).date().isoformat(),
                "current": off is not None,
                "class": _str(last["classification"]),
                "rank": _int(last["rank"]),
                "W": _num(last["W"]),
                "C": _num(last["C"]),
            },
            "today": None
            if not r["on_list"]
            else {
                "date": s.today.date().isoformat(),
                **values(r),
                "R": _num(r["R"]),
                "w0": _num(r["w0"]),
                "n_matches": int(r["n_matches"]),
                "note": note,
                "free": self._free_values(s, r) if note == "assigned" else None,
            },
            "history": [
                {
                    "date": h["day"].date().isoformat(),
                    "class": _str(h["classification"]),
                    "rank": _int(h["rank"]),
                    "W": _num(h["W"]),
                    "C": _num(h["C"]),
                }
                for _, h in hist.iterrows()
            ],
        }

    def _free_values(self, s: State, r: pd.Series) -> dict:
        """Values of a player with an assigned value (foreigner, Art. 9.2) as if computed from
        the matches, for information: one pass against the opponents' values of today's list;
        rank and category of that C among the ranked players of the same gender."""
        rows = s.rows.iloc[s.player_rows.get(r.name, np.array([], int))]
        W, R = ranking.single_pass(np.array([r["w0"]]), np.zeros(len(rows), int), rows["opp_w"].to_numpy(), rows["win"].to_numpy())
        C = W[0] + R[0]
        others = s.p[evaluate.ranked(s.p) & (s.p["gender"] == r["gender"]) & (s.p["personId"] != r["personId"])]
        others = others.sort_values("C", ascending=False)
        rank = int((others["C"] > C).sum()) + 1
        cls = others["class"].iloc[min(rank, len(others)) - 1] if len(others) else None
        return {"W": _num(W[0]), "R": _num(R[0]), "C": _num(C), "rank": rank, "class": _str(cls)}

    def _seasons(self, s: State, m: pd.DataFrame) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
        """Today's window, then the year windows before it back to the first of the matches `m`
        (each the window of an official list); the later ones first."""
        out = [(s.period.start, s.period.end)]
        first = m["date"].min()
        while not pd.isna(first) and out[-1][0] > first:
            start = out[-1][0]
            out.append((start - pd.DateOffset(years=1), start))
        return out

    def player_matches(self, pid: int, season: int = 0) -> dict:
        """The player's matches in a season: 0 is today's window, with what each match counts;
        1, 2... the year windows before it. Both players' category and rank at each match."""
        s = self.s()
        r = self._player(s, pid)
        i = s.number[pid]
        pos = s.match_rows.get(pid, np.array([], int))
        m = stats.describe(evaluate.dedupe(s.matches.iloc[pos]))
        seasons = self._seasons(s, m)
        if not 0 <= season < len(seasons):
            raise HTTPException(404, f"no season {season}")
        start, end = seasons[season]
        m = m[(m["date"] >= start) & (m["date"] < end)]
        # with_categories() renumbers the rows: keep the row of each result in `matches`
        m = stats.with_categories(m.assign(row=m.index), s.history, pid).set_index("row")
        m = m.sort_values("date", ascending=False, kind="stable")

        current = season == 0
        rows = s.rows.iloc[s.player_rows.get(i, np.array([], int))]
        dW, dC = match_deltas(r["w0"], rows["opp_w"].to_numpy(), rows["win"].to_numpy())
        calc = rows.assign(dW=dW, dC=dC).set_index("match")
        next_official = evaluate.next_list(s.today)
        chance = s.match_chance(m.index)

        out = []
        for idx, x in m.iterrows():
            opponent, own = stats.at_the_time(x)
            opp_id = opponent["id"]
            opp = s.p.loc[s.number[opp_id]] if opp_id in s.number.index else None
            c = calc.loc[idx] if current and idx in calc.index else None
            if not current:
                reason = "past"
            elif c is not None:
                reason = None if c["counted"] else "discarded_loss"
            else:
                reason = "walkover" if x["how"] == "walkover" else "no_value"
            last = first_list(x["date"]) + pd.DateOffset(months=6)
            out.append(
                {
                    "id": int(idx),
                    "date": x["date"].date().isoformat(),
                    "tournament": _str(x["tournamentName"]),
                    "type": x["type"],
                    "opponent": {
                        **opponent,
                        "class_today": None if opp is None or not opp["on_list"] else _str(opp["class"]),
                        "class_official": _str(s.official_class.get(opp_id)),
                        "value": None if c is None else _num(c["opp_w"]),
                    },
                    "own": own,
                    "score": x["score"],
                    "result": x["result"],
                    "how": x["how"],
                    "chance": _num(chance[idx]),
                    "counted": reason is None,
                    "reason": reason,
                    "delta_W": None if reason else _num(c["dW"]),
                    "delta_C": None if reason else _num(c["dC"]),
                    "last_list": last.date().isoformat() if current else None,
                    "drops_after_next_official": bool(current and last <= next_official),
                }
            )
        return {
            "season": season,
            "seasons": [{"start": a.date().isoformat(), "end": b.date().isoformat()} for a, b in seasons],
            "window": {
                "start": start.date().isoformat(),
                "end": end.date().isoformat(),
                "next_official": next_official.date().isoformat(),
            },
            "matches": out,
        }

    def simulate(self, pid: int, oid: int) -> dict:
        s = self.s()
        before_a, before_b = self._player(s, pid), self._player(s, oid)
        if pid == oid:
            raise HTTPException(400, "a player cannot play himself")
        a, b = s.number[pid], s.number[oid]
        out = {"player": {"before": values(before_a)}, "opponent": {"before": values(before_b)}}
        q = pd.DataFrame({"personId": [pid], "opp": [oid], "date": [s.today], "type": [winprob.TOURNAMENT]})
        c = s.chance(q)
        out["chance"] = None if c is None else _num(c[0])
        for outcome, win in (("win", True), ("loss", False)):
            extra = pd.DataFrame(
                {"player": [a, b], "opp": [b, a], "opp_fixed": [np.nan] * 2, "win": [win, not win], "match": [-1, -1]}
            )
            players = s.period.players.copy()
            players.loc[[a, b], "n_matches"] += 1
            period = replace(s.period, players=players, matches=pd.concat([s.period.matches, extra], ignore_index=True))
            p = evaluate.evaluate(period, predict=True)
            out["player"][outcome] = values(p.loc[a])
            out["opponent"][outcome] = values(p.loc[b])
        return out

    def _strengths(self, s: State, gender: str) -> dict[str, dict[int, float]]:
        """Per category, the W of a weak, a middle and a strong player (MOVE_UP_PERCENTILES of W
        among its ranked players of `gender`)."""
        g = s.p[evaluate.ranked(s.p) & (s.p["gender"] == gender)]
        out = {}
        for cls in evaluate.CATEGORIES:
            w = g.loc[g["class"] == cls, "W"]
            if not w.empty:
                out[cls] = {q: float(w.quantile(q / 100)) for q in MOVE_UP_PERCENTILES}
        return out

    def predict(self, pid: int, results: str) -> dict:
        """The player's values after hypothetical results: `results` is a comma-separated list of
        outcome:count:category:percentile (e.g. win:3:R2:75), each against an opponent of that
        category at that percentile of W (see _strengths). The full calculation, as simulate()."""
        s = self.s()
        r = self._player(s, pid)
        strengths = self._strengths(s, r["gender"])
        wins, opp_w = [], []
        for item in filter(None, results.split(",")):
            try:
                outcome, count, cls, q = item.split(":")
                count, q = int(count), int(q)
                value = strengths[cls][q]
            except (ValueError, KeyError):
                raise HTTPException(400, f"bad result {item!r}: outcome:count:category:percentile") from None
            if outcome not in ("win", "loss") or not 1 <= count <= PREDICT_MAX_PER_ROW:
                raise HTTPException(400, f"bad result {item!r}")
            wins += [outcome == "win"] * count
            opp_w += [value] * count
        if not wins or len(wins) > PREDICT_MAX_RESULTS:
            raise HTTPException(400, f"between 1 and {PREDICT_MAX_RESULTS} results")
        a = s.number[pid]
        extra = pd.DataFrame(
            {"player": a, "opp": -1, "opp_fixed": opp_w, "win": wins, "match": -1}, index=range(len(wins))
        )
        players = s.period.players.copy()
        players.loc[a, "n_matches"] += len(wins)
        period = replace(s.period, players=players, matches=pd.concat([s.period.matches, extra], ignore_index=True))
        p = evaluate.evaluate(period, predict=True)
        return {"before": values(r), "after": values(p.loc[a])}

    def move_up(self, pid: int) -> dict | None:
        """What the player needs to reach the next category: the wins (up to MOVE_UP_MAX_WINS)
        against a weak, a middle and a strong opponent of each category (the 25th, 50th and 75th
        percentile of W among its ranked players of the player's gender). An estimate: one pass
        of the formula with today's opponent values, the others' values unchanged. None when the
        player has no category to reach."""
        s = self.s()
        r = self._player(s, pid)
        if not r["on_list"] or r["class"] not in evaluate.CATEGORIES[1:]:
            return None
        g = s.p[evaluate.ranked(s.p) & (s.p["gender"] == r["gender"])]
        target = evaluate.CATEGORIES[evaluate.CATEGORIES.index(r["class"]) - 1]
        need = g.loc[g["class"] == target, "C"].min()
        if pd.isna(need):
            return None
        rows = s.rows.iloc[s.player_rows.get(r.name, np.array([], int))]
        opp_w, win = rows["opp_w"].to_numpy(), rows["win"].to_numpy()
        extra = np.arange(MOVE_UP_MAX_WINS + 1)

        def gain(value: float) -> np.ndarray:
            """C gained with 0, 1, 2... more wins against an opponent of W `value`."""
            C = []
            for k in extra:
                W, R = ranking.single_pass(
                    np.array([r["w0"]]), np.zeros(len(win) + k, int), np.r_[opp_w, np.full(k, value)], np.r_[win, np.ones(k, bool)]
                )
                C.append(W[0] + R[0])
            return np.array(C) - C[0]

        levels = []
        for cls, values_ in self._strengths(s, r["gender"]).items():
            opponents = []
            for q, value in values_.items():
                enough = np.flatnonzero(r["C"] + gain(value) >= need)
                opponents.append({"percentile": q, "W": _num(value), "wins": int(enough[0]) if len(enough) else None})
            levels.append({"class": cls, "opponents": opponents})
        return {"class": _str(r["class"]), "target": target, "C": _num(r["C"]), "need": _num(need), "max_wins": MOVE_UP_MAX_WINS, "levels": levels}

    def _stats_matches(self, pid: int, range_: str) -> tuple[pd.DataFrame, list[int]]:
        """The player's matches in `range_` (window, all or a year), describe()d and
        with_categories()d, and the years with matches."""
        s = self.s()
        self._player(s, pid)
        m = s.matches.iloc[s.match_rows.get(pid, np.array([], int))]
        m = stats.describe(evaluate.dedupe(m))
        years = sorted(m["date"].dt.year.unique().tolist(), reverse=True)
        if range_ == "window":
            m = m[(m["date"] >= s.period.start) & (m["date"] < s.period.end)]
        elif range_ != "all":
            if not range_.isdigit():
                raise HTTPException(400, "range must be window, all or a year")
            m = m[m["date"].dt.year == int(range_)]
        return stats.with_categories(m, s.history, pid), years

    def player_stats(self, pid: int, range_: str) -> dict:
        m, years = self._stats_matches(pid, range_)
        out = stats.player_stats(m)
        # each opponent's category on today's list
        today = self.s().p.loc[lambda p: p["on_list"]].set_index("personId")["class"]
        for o in out["opponents"]:
            o["class"] = _str(today.get(o["id"]))
        return {"range": range_, "years": years, **out}

    def stat_matches(self, pid: int, range_: str, key: str, value: str | None) -> list[dict]:
        m, _ = self._stats_matches(pid, range_)
        try:
            return stats.match_list(stats.matches_of(m, key, value))
        except KeyError:
            raise HTTPException(400, f"unknown statistic {key} {value or ''}".strip()) from None

    def board_matches(self, key: str, pid: int) -> list[dict]:
        s = self.s()
        # a leaderboard player may have no matches in today's period: known is enough
        if pid not in s.people.index:
            raise HTTPException(404, f"unknown player {pid}")
        if key not in ("streak", "longest_super_tiebreak", "biggest_upset") and key not in highlights.RATIOS:
            raise HTTPException(404, f"unknown leaderboard {key}")
        m = s.matches.iloc[s.match_rows.get(pid, np.array([], int))]
        if key == "biggest_upset":
            since = s.today - pd.DateOffset(years=highlights.LEADERBOARD_YEARS)
            m = m.loc[highlights.upsets(m, s.history, since, s.today)["row"].head(1)]
        else:
            m = highlights.board_matches(m, s.today, key)
        m = stats.describe(m)
        return stats.match_list(stats.with_categories(m, s.history, pid))

    def highlights(self) -> dict:
        return self.s().highlights

    def h2h(self, pid: int, oid: int) -> list[dict]:
        s = self.s()
        self._player(s, pid)
        m = s.matches.iloc[s.match_rows.get(pid, np.array([], int))]
        m = stats.describe(evaluate.dedupe(m[m["adversaryPersonId"] == oid]))
        return stats.match_list(stats.with_categories(m, s.history, pid))

    def career(self, pid: int) -> dict:
        m, _ = self._stats_matches(pid, "all")
        return stats.career(m)


class SinglePageApp(StaticFiles):
    """The UI's files; any other path outside /api gets index.html (the UI routes itself)."""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code != 404 or path == "api" or path.startswith("api/"):
                raise
            return await super().get_response("index.html", scope)


def create_app(data_dir: Path, static_dir: Path | None, scrape_time: str | None) -> FastAPI:
    app = App(data_dir, scrape_time)

    @asynccontextmanager
    async def lifespan(_):
        app.start()
        yield

    api = FastAPI(title="Tennis Radar", lifespan=lifespan)

    @api.get("/api/config")
    def config():
        # Umami tracker, loaded by the UI when both are set
        url, website = os.environ.get("UMAMI_SCRIPT_URL"), os.environ.get("UMAMI_WEBSITE_ID")
        return {"analytics": {"script": url, "website_id": website} if url and website else None}

    @api.post("/api/feedback", status_code=204)
    def feedback(f: Feedback, request: Request):
        # behind the Cloudflare tunnel every request comes from cloudflared
        ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "")
        app.feedback(f, ip)
        return Response(status_code=204)

    @api.get("/api/meta")
    def meta():
        return app.meta()

    @api.get("/api/players/search")
    def search(q: str, limit: int = Query(20, ge=1, le=100)):
        return app.search(q, limit)

    @api.get("/api/players/{pid}")
    def player(pid: int):
        return app.player(pid)

    @api.get("/api/players/{pid}/matches")
    def matches(pid: int, season: int = 0):
        return app.player_matches(pid, season)

    @api.get("/api/players/{pid}/stats")
    def player_stats(pid: int, range: str = "window"):
        return app.player_stats(pid, range)

    @api.get("/api/players/{pid}/predict")
    def predict(pid: int, results: str):
        return app.predict(pid, results)

    @api.get("/api/players/{pid}/move-up")
    def move_up(pid: int):
        return app.move_up(pid)

    @api.get("/api/players/{pid}/career")
    def career(pid: int):
        return app.career(pid)

    @api.get("/api/players/{pid}/h2h/{oid}")
    def h2h(pid: int, oid: int):
        return app.h2h(pid, oid)

    @api.get("/api/players/{pid}/stats/matches")
    def stat_matches(pid: int, key: str, range: str = "window", value: str | None = None):
        return app.stat_matches(pid, range, key, value)

    @api.get("/api/highlights/{key}/{pid}")
    def board_matches(key: str, pid: int):
        return app.board_matches(key, pid)

    @api.get("/api/highlights")
    def highlights():
        return app.highlights()

    @api.get("/api/simulate")
    def simulate(player: int, opponent: int):
        return app.simulate(player, opponent)

    if static_dir is not None and static_dir.is_dir():
        api.mount("/", SinglePageApp(directory=static_dir, html=True), name="ui")
    return api
