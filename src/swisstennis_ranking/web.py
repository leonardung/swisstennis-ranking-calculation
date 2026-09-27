"""Web UI backend: today's calculated list, per-match points, a win/loss simulator and statistics.

Today's list is the monthly list at today's date in predict mode (see evaluate.build_period). It
is computed at startup and again after every nightly scrape.
"""

import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import evaluate, ranking, stats

# a nightly scrape refetches the players with a result dated in these last days
RECENT_DAYS = 45


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

    official_date = [d for d in evaluate.publications(history) if d <= today][-1]
    return State(
        today=today,
        updated=datetime.fromtimestamp((data_dir / "matches.parquet").stat().st_mtime),
        history=history,
        matches=matches,
        match_rows=matches.groupby("personId").indices,
        period=period,
        p=p,
        rows=rows,
        player_rows=rows.groupby("player").indices,
        number=pd.Series(p.index, index=p["personId"]),
        people=people,
        official_date=official_date,
        official_class=history[history["date"].dt.normalize() == official_date]
        .drop_duplicates("personId", keep="last")
        .set_index("personId")["classification"],
    )


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
        cmd = [sys.executable, "-m", "swisstennis_ranking", "--data", str(self.data_dir), "scrape"]
        if recent_days is not None:
            cmd += ["--recent-days", str(recent_days)]
        log = self.data_dir / "scrape.log"
        try:
            with log.open("w") as out:
                done = subprocess.run(cmd, stdout=out, stderr=subprocess.STDOUT)
            if done.returncode:
                self.scrape["last_error"] = log.read_text(errors="replace")[-2000:]
        except Exception as e:  # keep the scheduler alive
            self.scrape["last_error"] = repr(e)
        self.scrape.update(running=False, last_run=datetime.now().isoformat(timespec="seconds"))
        # also when the scrape failed: today's list moves with the date
        if (self.data_dir / "history.parquet").exists():
            self._reload()

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
        note = "classified" if r["classified"] else "assigned" if r["foreign"] and r["W5_prev"] >= evaluate.ASSIGNED_MIN_W5 else None
        return {
            "id": pid,
            "name": person["name"],
            "licence": _str(person["licence"]),
            "gender": _str(person["gender"]),
            "official": None
            if off is None
            else {
                "date": s.official_date.date().isoformat(),
                "class": _str(off["classification"]),
                "rank": _int(off["rank"]),
                "W": _num(off["W"]),
                "C": _num(off["C"]),
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

    def player_matches(self, pid: int) -> dict:
        s = self.s()
        r = self._player(s, pid)
        i = s.number[pid]
        pos = s.match_rows.get(pid, np.array([], int))
        m = s.matches.iloc[pos]
        m = m[(m["date"] >= s.period.start) & (m["date"] < s.period.end)]
        m = stats.describe(evaluate.dedupe(m)).sort_values("date", ascending=False)

        rows = s.rows.iloc[s.player_rows.get(i, np.array([], int))]
        dW, dC = match_deltas(r["w0"], rows["opp_w"].to_numpy(), rows["win"].to_numpy())
        calc = rows.assign(dW=dW, dC=dC).set_index("match")
        next_official = evaluate.next_list(s.today)

        out = []
        for idx, x in m.iterrows():
            opp_id = _int(x["adversaryPersonId"])
            opp = s.p.loc[s.number[opp_id]] if opp_id in s.number.index else None
            c = calc.loc[idx] if idx in calc.index else None
            if c is not None:
                reason = None if c["counted"] else "discarded_loss"
            else:
                reason = "walkover" if x["how"] == "walkover" else "no_value"
            last = first_list(x["date"]) + pd.DateOffset(months=6)
            out.append(
                {
                    "id": int(idx),
                    "date": x["date"].date().isoformat(),
                    "tournament": x["tournamentName"],
                    "type": x["type"],
                    "opponent": {
                        "id": opp_id,
                        "name": stats._name(x),
                        "class": None if opp is None or not opp["on_list"] else _str(opp["class"]),
                        "class_official": _str(s.official_class.get(opp_id)),
                        "value": None if c is None else _num(c["opp_w"]),
                    },
                    "score": x["score"],
                    "result": x["result"],
                    "how": x["how"],
                    "counted": reason is None,
                    "reason": reason,
                    "delta_W": None if reason else _num(c["dW"]),
                    "delta_C": None if reason else _num(c["dC"]),
                    "last_list": last.date().isoformat(),
                    "drops_after_next_official": bool(last <= next_official),
                }
            )
        return {
            "window": {
                "start": s.period.start.date().isoformat(),
                "end": s.period.end.date().isoformat(),
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

    def player_stats(self, pid: int, range_: str) -> dict:
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
        m = stats.with_categories(m, s.history, pid)
        return {"range": range_, "years": years, **stats.player_stats(m)}

    def h2h(self, pid: int, oid: int) -> list[dict]:
        s = self.s()
        self._player(s, pid)
        m = s.matches.iloc[s.match_rows.get(pid, np.array([], int))]
        m = stats.describe(evaluate.dedupe(m[m["adversaryPersonId"] == oid])).sort_values("date", ascending=False)
        return [
            {
                "date": x["date"].date().isoformat(),
                "tournament": x["tournamentName"],
                "score": x["score"],
                "result": x["result"],
                "how": x["how"],
            }
            for _, x in m.iterrows()
        ]


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
    def matches(pid: int):
        return app.player_matches(pid)

    @api.get("/api/players/{pid}/stats")
    def player_stats(pid: int, range: str = "window"):
        return app.player_stats(pid, range)

    @api.get("/api/players/{pid}/h2h/{oid}")
    def h2h(pid: int, oid: int):
        return app.h2h(pid, oid)

    @api.get("/api/simulate")
    def simulate(player: int, opponent: int):
        return app.simulate(player, opponent)

    if static_dir is not None and static_dir.is_dir():
        api.mount("/", SinglePageApp(directory=static_dir, html=True), name="ui")
    return api
