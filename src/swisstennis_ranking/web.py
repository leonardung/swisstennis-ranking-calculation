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
from .tournaments import locate

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
# ... and at most this many played matches removed, and matches added against given players
PREDICT_MAX_REMOVE = 200
PREDICT_MAX_ADD = 30
# tournaments: their tables (written by `scrape-tournaments`), refreshed every this many hours
TOURNAMENT_DIR = "tournaments"
TOURNAMENT_REFRESH_HOURS = 2
# tournament list: page size by default and at most; suggested tournaments of a player
TOURNAMENT_LIMIT = 50
TOURNAMENT_MAX_LIMIT = 200
SUGGESTED_MAX = 20
TOURNAMENT_SORTS = ("date", "-date", "distance", "players", "inRange")
TOURNAMENT_WHEN = ("upcoming", "live", "past")
AGE_KINDS = ("actives", "juniors", "seniors")  # the order of the age options
SINGLES = (1, 2)  # match types: men's and women's singles
MYTENNIS_URL = "https://www.mytennis.ch/fr/tournois/{}"
# category index (0 = N1, strongest) of each category, and of each ranking type id (1 N1 ... 13 R9)
CATEGORY_INDEX = {c: i for i, c in enumerate(evaluate.CATEGORIES)}
TOURNAMENT_TABLES = {
    "tournaments": {
        "tournamentId": "int64", "name": "string", "city": "string", "canton": "string",
        "dateBegin": "datetime64[ns]", "dateEnd": "datetime64[ns]", "drawingTime": "datetime64[ns]",
        "statusId": "int64", "competitionCategoryId": "Int64", "surface": "string", "place": "string",
        "category": "string", "selection": "string", "entryFee": "string", "organiser": "string",
        "email": "string", "phone": "string", "homePage": "string", "lat": "float64", "lon": "float64",
        "updated": "datetime64[ns]",
    },
    "events": {
        "eventId": "int64", "tournamentId": "int64", "matchTypeId": "int64", "ageCategoryId": "int64",
        "lowerRankingId": "Int64", "upperRankingId": "Int64", "dateFrom": "datetime64[ns]",
        "dateTo": "datetime64[ns]", "statusId": "Int64", "competition": "string", "applicants": "Int64",
        "registrationOpen": "boolean", "onlineRegistration": "boolean", "pdfUrl": "string",
    },
    "registrations": {"eventId": "int64", "licence": "string", "personId": "Int64", "firstname": "string", "lastname": "string"},
    "draws": {
        "eventId": "int64", "round": "int64", "slot": "int64", "a1": "Int64", "a2": "Int64", "b1": "Int64",
        "b2": "Int64", "aName": "string", "bName": "string", "winner": "Int64", "walkover": "boolean",
        "score": "string", "date": "datetime64[ns]",
    },
}


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
    # + 0.0: no -0.0 for tiny negatives
    return None if v is None or pd.isna(v) else round(float(v), digits) + 0.0


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


def c_with(w0: float, opp_w: np.ndarray, win: np.ndarray, extra_w, extra_win) -> float:
    """C of a player after one pass of the formula with his rows (opp_w, win) plus extra results
    against opponents of W extra_w, the opponents' values fixed (see App.move_up)."""
    extra_w, extra_win = np.atleast_1d(extra_w).astype(float), np.atleast_1d(extra_win).astype(bool)
    W, R = ranking.single_pass(
        np.array([w0]), np.zeros(len(win) + len(extra_win), int), np.r_[opp_w, extra_w], np.r_[win, extra_win]
    )
    return W[0] + R[0]


# --- tournaments -----------------------------------------------------------------------------


@dataclass
class Tournaments:
    """The tables of data/tournaments/ (empty when missing). Replaced as a whole on reload: a
    request reads App.tournaments once and keeps a consistent snapshot."""

    tournaments: pd.DataFrame  # index tournamentId; + key (search text), players (all events)
    # one row per event (junk match types dropped) with its tournament's columns, players
    # (registrations, else REST applicants), hasDraw, until (last day), strong/weak (category
    # indices of the bounds, open bounds N1/R9)
    events: pd.DataFrame
    registrations: pd.DataFrame
    draws: pd.DataFrame  # ordered by event, round (first round first), slot
    reg_rows: dict  # eventId -> positions in `registrations`
    draw_rows: dict  # eventId -> positions in `draws`
    cities: pd.DataFrame  # index city: lat, lon
    ages: dict  # age category id -> {"id", "kind", + "men"/"women" (seniors) or "max" (juniors)}
    geocode: Path  # the place cache (geocode.json, shared with the scraper)
    updated: datetime | None  # last refresh (the file's time)


def load_tournaments(tdir: Path) -> Tournaments:
    tables = {}
    for name, cols in TOURNAMENT_TABLES.items():
        f = tdir / f"{name}.parquet"
        df = pd.read_parquet(f) if f.exists() else pd.DataFrame()
        for c, t in cols.items():
            if c not in df:
                df[c] = pd.Series(dtype=t)
        tables[name] = df.reset_index(drop=True)
    t = tables["tournaments"].drop_duplicates("tournamentId", keep="last").set_index("tournamentId")
    t["key"] = _fold(t["name"].astype(object).fillna("") + " " + t["city"].astype(object).fillna("") + " " + t["organiser"].astype(object).fillna(""))
    regs, draws = tables["registrations"], tables["draws"]
    draws = draws.sort_values(["eventId", "round", "slot"], ascending=[True, False, True], ignore_index=True)

    e = tables["events"]
    e = e[e["matchTypeId"] != 100].drop_duplicates("eventId", keep="last")
    e = e.join(t[["name", "city", "canton", "dateBegin", "dateEnd", "drawingTime", "statusId", "surface", "lat", "lon", "key"]], on="tournamentId", how="inner", rsuffix="_t")
    e = e.reset_index(drop=True)
    n = regs.groupby("eventId").size().reindex(e["eventId"]).to_numpy()
    e["players"] = pd.Series(n, dtype=float).fillna(e["applicants"].astype(float)).fillna(0).astype(int)
    e["hasDraw"] = e["eventId"].isin(draws["eventId"])
    e["until"] = e["dateTo"].fillna(e["dateEnd"])
    last = len(evaluate.CATEGORIES) - 1
    e["strong"] = (e["upperRankingId"].astype(float) - 1).clip(0, last).fillna(0).astype(int)
    e["weak"] = (e["lowerRankingId"].astype(float) - 1).clip(0, last).fillna(last).astype(int)
    t["players"] = e.groupby("tournamentId")["players"].sum().reindex(t.index).fillna(0).astype(int)

    located = t.dropna(subset=["lat", "lon"])
    cities = located.groupby(located["city"].astype(object))[["lat", "lon"]].mean()
    ages = {}
    if (tdir / "ages.json").exists():
        for k, v in json.loads((tdir / "ages.json").read_text()).items():
            ages[int(k)] = {"id": int(k), **{x: v[x] for x in ("kind", "men", "women", "max") if x in v}}
    e["open"] = e["registrationOpen"].fillna(False).astype(bool)
    f = tdir / "tournaments.parquet"
    return Tournaments(
        tournaments=t,
        events=e,
        registrations=regs,
        draws=draws,
        reg_rows=regs.groupby("eventId").indices,
        draw_rows=draws.groupby("eventId").indices,
        cities=cities,
        ages=ages,
        geocode=tdir / "geocode.json",
        updated=datetime.fromtimestamp(f.stat().st_mtime) if f.exists() else None,
    )


def haversine(lat: np.ndarray, lon: np.ndarray, lat0: float, lon0: float) -> np.ndarray:
    """Great-circle distance in km from (lat0, lon0)."""
    lat, lon, lat0, lon0 = map(np.radians, (lat, lon, lat0, lon0))
    a = np.sin((lat - lat0) / 2) ** 2 + np.cos(lat) * np.cos(lat0) * np.sin((lon - lon0) / 2) ** 2
    return 6371.0 * 2 * np.arcsin(np.sqrt(a))


def _date(v: str, name: str) -> pd.Timestamp:
    try:
        return pd.Timestamp(datetime.strptime(v, "%Y-%m-%d"))
    except ValueError:
        raise HTTPException(400, f"{name} must be a date YYYY-MM-DD") from None


def _ids(v: str, name: str) -> list[int]:
    try:
        return [int(x) for x in v.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(400, f"{name} must be comma-separated ids") from None


def _category_range(weakest: str | None, strongest: str | None, name: str) -> tuple[int, int] | None:
    """(strong, weak) category indices of a range from its weakest (min) and strongest (max)
    category; a missing end is open (R9 or N1), swapped ends are accepted. None without both."""
    if not weakest and not strongest:
        return None
    try:
        a = CATEGORY_INDEX[weakest.upper()] if weakest else len(evaluate.CATEGORIES) - 1
        b = CATEGORY_INDEX[strongest.upper()] if strongest else 0
    except KeyError:
        raise HTTPException(400, f"{name}Min and {name}Max must be categories N1 to R9") from None
    return min(a, b), max(a, b)


def _category_of(ranking_id) -> str | None:
    """Label of a ranking type id (1 N1 ... 13 R9)."""
    return evaluate.CATEGORIES[int(ranking_id) - 1] if pd.notna(ranking_id) and 1 <= ranking_id <= len(evaluate.CATEGORIES) else None


def _day(v) -> str | None:
    return None if v is None or pd.isna(v) else pd.Timestamp(v).date().isoformat()


def _bool(v) -> bool | None:
    return None if v is None or pd.isna(v) else bool(v)


def _in_range(t: Tournaments, level: pd.Series, events: pd.Series, strong: int, weak: int) -> pd.Series:
    """Per event id of `events`, the registered players whose category index (level: personId ->
    index of today's class) is within [strong, weak]."""
    regs = t.registrations[t.registrations["eventId"].isin(events)]
    lv = regs["personId"].map(level)
    hit = regs[(lv >= strong) & (lv <= weak)]
    return hit.groupby("eventId").size().reindex(events).fillna(0).astype(int)


def _played_by(draws: pd.DataFrame, pid: int) -> np.ndarray:
    """Mask of the draw rows where `pid` played (on either side)."""
    return draws[["a1", "a2", "b1", "b2"]].eq(pid).fillna(False).any(axis=1).to_numpy(bool)


def _tournament_summary(tid: int, r: pd.Series) -> dict:
    return {
        "id": int(tid),
        "name": _str(r["name"]),
        "city": _str(r["city"]),
        "canton": _str(r["canton"]),
        "dateBegin": _day(r["dateBegin"]),
        "dateEnd": _day(r["dateEnd"]),
        "status": _int(r["statusId"]),
        "surface": _str(r["surface"]),
    }


def _event_summary(t: Tournaments, e: pd.Series, in_range=None) -> dict:
    return {
        "id": int(e["eventId"]),
        "matchType": _int(e["matchTypeId"]),
        "age": _int(e["ageCategoryId"]),
        "ageInfo": t.ages.get(int(e["ageCategoryId"])),
        "lower": _category_of(e["lowerRankingId"]),
        "upper": _category_of(e["upperRankingId"]),
        "competition": _str(e["competition"]),
        "players": int(e["players"]),
        "inRange": _int(in_range),
        "registrationOpen": _bool(e["registrationOpen"]),
        "hasDraw": bool(e["hasDraw"]),
    }


class App:
    def __init__(self, data_dir: Path, scrape_time: str | None, tournament_hours: float | None = None):
        self.data_dir = data_dir
        self.scrape_time = scrape_time
        self.tournament_hours = tournament_hours
        self.state: State | None = None
        self.scrape = {"running": False, "last_run": None, "last_error": None, "next_run": None}
        self.tournament_scrape = {"running": False, "last_run": None, "last_error": None, "next_run": None}
        # the ranking scrape (and training) and the tournament scrape never run at the same time
        self.scrape_lock = threading.Lock()
        self.tournaments = load_tournaments(data_dir / TOURNAMENT_DIR)
        self.feedback_lock = threading.Lock()
        self.feedback_times: dict[str, list[float]] = {}

    # --- lifecycle ---------------------------------------------------------------------------

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        if (self.data_dir / "history.parquet").exists():
            self._reload()
            # no model yet (first start): train it, then load it
            with self.scrape_lock:
                trained = self._train_if_stale()
            if trained:
                self._reload()
        else:
            self._scrape()
        # after the first ranking scrape: the tournament scrape needs players.parquet for the licences
        threading.Thread(target=self._run_tournaments, daemon=True).start()
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
        with self.scrape_lock:
            self.scrape.update(running=True, last_error=None)
            args = ["scrape"] + ([] if recent_days is None else ["--recent-days", str(recent_days)])
            if self._command(args, "scrape.log"):
                self._train_if_stale()
            self.scrape.update(running=False, last_run=datetime.now().isoformat(timespec="seconds"))
        # also when the scrape failed: today's list moves with the date
        if (self.data_dir / "history.parquet").exists():
            self._reload()

    def _train_if_stale(self) -> bool:
        """Train the win probability model when a new official list is out or there is none (its
        own process: its memory is freed); True if it was trained."""
        return winprob.stale(self.data_dir) and self._command(["train-winprob"], "winprob.log")

    def _run_tournaments(self) -> None:
        """Scrape the tournaments on first start (no data yet), then every tournament_hours."""
        if not (self.data_dir / TOURNAMENT_DIR / "tournaments.parquet").exists():
            self._scrape_tournaments()
        if not self.tournament_hours:
            return
        while True:
            run = datetime.now() + timedelta(hours=self.tournament_hours)
            self.tournament_scrape["next_run"] = run.isoformat(timespec="seconds")
            time.sleep(self.tournament_hours * 3600)
            self._scrape_tournaments()

    def _scrape_tournaments(self) -> None:
        with self.scrape_lock:
            self.tournament_scrape.update(running=True, last_error=None)
            self._command(["scrape-tournaments"], "tournaments.log", self.tournament_scrape)
            self.tournament_scrape.update(running=False, last_run=datetime.now().isoformat(timespec="seconds"))
        self._reload_tournaments()

    def _command(self, args: list[str], log_name: str, status: dict | None = None) -> bool:
        """Run a CLI command on the data, output to data/log_name; False (and last_error of
        `status`, by default the ranking scrape's) on failure."""
        status = self.scrape if status is None else status
        cmd = [sys.executable, "-m", "swisstennis_ranking", "--data", str(self.data_dir), *args]
        log = self.data_dir / log_name
        try:
            with log.open("w") as out:
                done = subprocess.run(cmd, stdout=out, stderr=subprocess.STDOUT)
            if done.returncode:
                status["last_error"] = log.read_text(errors="replace")[-2000:]
            return done.returncode == 0
        except Exception as e:  # keep the scheduler alive
            status["last_error"] = repr(e)
            return False

    def _reload(self) -> None:
        try:
            self.state = load_state(self.data_dir)
        except Exception as e:
            self.scrape["last_error"] = f"loading data failed: {e!r}"

    def _reload_tournaments(self) -> None:
        """Reload only the tournament tables (a new object: requests in flight keep the old one)."""
        try:
            self.tournaments = load_tournaments(self.data_dir / TOURNAMENT_DIR)
        except Exception as e:
            self.tournament_scrape["last_error"] = f"loading tournaments failed: {e!r}"

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
            "tournaments": {
                "updated": None if self.tournaments.updated is None else self.tournaments.updated.isoformat(timespec="seconds"),
                "running": self.tournament_scrape["running"],
                "lastError": self.tournament_scrape["last_error"],
            },
        }

    def _player(self, s: State, pid: int) -> pd.Series:
        if pid not in s.number.index or pid not in s.people.index:
            raise HTTPException(404, f"unknown player {pid}")
        return s.p.loc[s.number[pid]]

    def _official(self, s: State, pid: int) -> pd.Series | None:
        h = s.history[(s.history["personId"] == pid) & (s.history["date"].dt.normalize() == s.official_date)]
        return None if h.empty else h.iloc[-1]

    def search(self, q: str, limit: int, gender: str | None = None) -> list[dict]:
        s = self.s()
        words = _fold(pd.Series([q.replace(".", "")])).iloc[0].split()
        if not words:
            return []
        key = s.people["key"]
        hit = np.ones(len(key), bool) if gender is None else (s.people["gender"] == gender).to_numpy(copy=True)
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

    def predict(self, pid: int, results: str = "", remove: str = "", add: str = "") -> dict:
        """The player's values after a what-if, all in one full calculation (as simulate()):
        - `results`: hypothetical results, a comma-separated list of outcome:count:category:percentile
          (e.g. win:3:R2:75), each against an opponent of that category at that percentile of W
          (see _strengths);
        - `remove`: played matches of today's window left out, comma-separated ids (the row of the
          result in `matches`, the ids of player_matches()); the opponent's side goes too;
        - `add`: matches against given players of the same gender, outcome:personId (win:123),
          counted for both players."""
        s = self.s()
        r = self._player(s, pid)
        a = s.number[pid]
        m = s.period.matches
        wins, opp_w = [], []
        if results:
            strengths = self._strengths(s, r["gender"])
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

        own = m.index[m["player"].to_numpy() == a]
        by_match = pd.Series(own, index=m.loc[own, "match"].to_numpy())
        drop: set[int] = set()
        ids = list(filter(None, remove.split(",")))
        if len(ids) > PREDICT_MAX_REMOVE:
            raise HTTPException(400, f"at most {PREDICT_MAX_REMOVE} matches removed")
        for item in ids:
            try:
                row = int(by_match.loc[int(item)])
            except (ValueError, KeyError):
                raise HTTPException(400, f"not a match of the player's window: {item!r}") from None
            drop.add(row)
            if (b := m.at[row, "opp"]) >= 0:  # the same result on the opponent's side, same day
                date = s.matches.at[m.at[row, "match"], "date"]
                mirror = m.index[(m["player"] == b) & (m["opp"] == a) & (m["win"] != m.at[row, "win"])]
                mirror = [x for x in mirror if x not in drop and s.matches.at[m.at[x, "match"], "date"] == date]
                drop.update(mirror[:1])

        extra = [pd.DataFrame({"player": a, "opp": -1, "opp_fixed": opp_w, "win": wins, "match": -1}, index=range(len(wins)))]
        added = list(filter(None, add.split(",")))
        if len(added) > PREDICT_MAX_ADD:
            raise HTTPException(400, f"at most {PREDICT_MAX_ADD} matches added")
        for item in added:
            try:
                outcome, oid = item.split(":")
                oid = int(oid)
            except ValueError:
                raise HTTPException(400, f"bad match {item!r}: outcome:personId") from None
            if outcome not in ("win", "loss"):
                raise HTTPException(400, f"bad match {item!r}")
            if oid == pid or oid not in s.number.index or oid not in s.people.index:
                raise HTTPException(400, f"unknown opponent {oid}")
            b = s.number[oid]
            if s.p.at[b, "gender"] != r["gender"]:
                raise HTTPException(400, f"opponent {oid} is not of the player's gender")
            win = outcome == "win"
            extra.append(pd.DataFrame({"player": [a, b], "opp": [b, a], "opp_fixed": [np.nan] * 2, "win": [win, not win], "match": [-1, -1]}))
        if len(wins) + len(added) > PREDICT_MAX_RESULTS:
            raise HTTPException(400, f"at most {PREDICT_MAX_RESULTS} results")
        if not wins and not added and not drop:
            raise HTTPException(400, "nothing to predict: give results, remove or add")

        matches = pd.concat([m.drop(index=list(drop)), *(x for x in extra if len(x))], ignore_index=True)
        players = s.period.players.copy()
        players["n_matches"] = np.bincount(matches["player"], minlength=len(players))
        p = evaluate.evaluate(replace(s.period, players=players, matches=matches), predict=True)
        return {"before": values(r), "after": values(p.loc[a]), "removed": len(ids), "added": len(wins) + len(added)}

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
        opp_w, win = self._own_rows(s, r)
        extra = np.arange(MOVE_UP_MAX_WINS + 1)

        def gain(value: float) -> np.ndarray:
            """C gained with 0, 1, 2... more wins against an opponent of W `value`."""
            C = [c_with(r["w0"], opp_w, win, np.full(k, value), np.ones(k, bool)) for k in extra]
            return np.array(C) - C[0]

        levels = []
        for cls, values_ in self._strengths(s, r["gender"]).items():
            opponents = []
            for q, value in values_.items():
                enough = np.flatnonzero(r["C"] + gain(value) >= need)
                opponents.append({"percentile": q, "W": _num(value), "wins": int(enough[0]) if len(enough) else None})
            levels.append({"class": cls, "opponents": opponents})
        return {"class": _str(r["class"]), "target": target, "C": _num(r["C"]), "need": _num(need), "max_wins": MOVE_UP_MAX_WINS, "levels": levels}

    def _own_rows(self, s: State, r: pd.Series) -> tuple[np.ndarray, np.ndarray]:
        """The opponents' W and the outcomes of the player's (row r of s.p) results today."""
        rows = s.rows.iloc[s.player_rows.get(r.name, np.array([], int))]
        return rows["opp_w"].to_numpy(), rows["win"].to_numpy()

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

    # --- tournaments -------------------------------------------------------------------------

    def _levels(self) -> pd.DataFrame:
        """Today's class, its category index (level), C and W of the players on today's list
        (index personId); empty while the ranking data loads."""
        s = self.state
        if s is None:
            return pd.DataFrame({"class": [], "C": [], "W": [], "level": []}, index=pd.Index([], dtype="int64"))
        cached = getattr(self, "_levels_cache", None)
        if cached is not None and cached[0] is s:
            return cached[1]
        live = s.p.loc[s.p["on_list"], ["personId", "class", "C", "W"]].set_index("personId")
        live = live.assign(level=live["class"].map(CATEGORY_INDEX))
        self._levels_cache = (s, live)
        return live

    def _person(self, pid: int | None, name: str | None, live: pd.DataFrame) -> dict:
        s = self.state
        if not name and pid is not None and s is not None and pid in s.people.index:
            name = s.people.at[pid, "name"]
        return {"personId": pid, "name": _str(name) or None, "class": None if pid is None else _str(live["class"].get(pid))}

    def _side(self, x: pd.Series, side: str, live: pd.DataFrame) -> list[dict]:
        """The player(s) of side a or b of a draw row; doubles names are "X / Y"."""
        ids = [i for i in (_int(x[f"{side}1"]), _int(x[f"{side}2"])) if i is not None]
        name = _str(x[f"{side}Name"])
        if not ids:  # players without licence (from the draw PDF)
            return [] if name is None else [self._person(None, n, live) for n in name.split(" / ")]
        names = (name or "").split(" / ") if len(ids) == 2 else [name]
        if len(names) != len(ids):
            names = [None] * len(ids)
        return [self._person(i, n, live) for i, n in zip(ids, names)]

    def _class_of(self, s: State, r: pd.Series) -> str | None:
        """Today's class of the player (row r of s.p), else his official one."""
        return r["class"] if r["on_list"] else s.official_class.get(r["personId"])

    def _tournament_item(self, t: Tournaments, e: pd.Series, in_range=None) -> dict:
        """A tournament (list summary without events) and one of its events."""
        tid = int(e["tournamentId"])
        tr = t.tournaments.loc[tid]
        return {
            "tournament": {**_tournament_summary(tid, tr), "distanceKm": None, "players": int(tr["players"]), "inRange": None},
            "event": _event_summary(t, e, in_range),
        }

    def _place(self, t: Tournaments, near: str) -> tuple[float, float]:
        """(lat, lon) of a place text: a tournament city (accents and case ignored), else any
        place Nominatim knows (a postal code, a village), cached in geocode.json."""
        near = " ".join(near.split())[:100]
        folded = _fold(pd.Series([near])).iloc[0]
        same = t.cities[_fold(pd.Series(t.cities.index.astype(str), index=t.cities.index)) == folded]
        if len(same):
            return tuple(same.iloc[0][["lat", "lon"]])
        hit = locate(near, t.geocode)
        if hit is False:
            raise HTTPException(503, "geocoding unavailable")
        if not hit:
            raise HTTPException(400, "unknown place")
        return hit[0], hit[1]

    def _options(self, t: Tournaments, e: pd.DataFrame) -> dict:
        """The filter values present among these events: age categories actives, juniors
        (oldest first), seniors (youngest first), then unknown ones."""

        def age_order(a: dict):
            kind = AGE_KINDS.index(a["kind"]) if a["kind"] in AGE_KINDS else len(AGE_KINDS)
            return kind, -a.get("max", 0), a.get("men", 0), a["id"]

        ages = [t.ages.get(i, {"id": i, "kind": "other"}) for i in {int(x) for x in e["ageCategoryId"]}]
        return {
            "matchTypes": sorted(int(x) for x in e["matchTypeId"].unique()),
            "ages": sorted(ages, key=age_order),
            "surfaces": sorted(e["surface"].dropna().astype(str).unique().tolist()),
            "cantons": sorted(e["canton"].dropna().astype(str).unique().tolist()),
            "cities": sorted(e.loc[e["lat"].notna(), "city"].dropna().astype(str).unique().tolist()),
        }

    def tournament_list(
        self,
        from_: str | None = None,
        to: str | None = None,
        q: str | None = None,
        match_type: str | None = None,
        age: str | None = None,
        event_min: str | None = None,
        event_max: str | None = None,
        field_min: str | None = None,
        field_max: str | None = None,
        near: str | None = None,
        radius: float | None = None,
        canton: str | None = None,
        player: int | None = None,
        sort: str | None = None,
        limit: int = TOURNAMENT_LIMIT,
        offset: int = 0,
        when: str = "upcoming",
        surface: str | None = None,
    ) -> dict:
        """Tournaments with at least one event matching all filters, with only those events (see
        the API contract). Filters are masks over the events table, no loop over its rows.
        when: upcoming (not over, at least one kept event takes registrations), live (not over,
        none does), past (over). The options are the values of the tournaments of `when` and the
        date window, whatever the other filters."""
        t = self.tournaments
        e = t.events
        if when not in TOURNAMENT_WHEN:
            raise HTTPException(400, f"when must be one of {', '.join(TOURNAMENT_WHEN)}")
        sort = sort or ("-date" if when == "past" else "date")
        if sort not in TOURNAMENT_SORTS:
            raise HTTPException(400, f"sort must be one of {', '.join(TOURNAMENT_SORTS)}")
        limit, offset = min(max(limit, 1), TOURNAMENT_MAX_LIMIT), max(offset, 0)
        end = e["dateEnd"].fillna(e["dateBegin"])
        today = pd.Timestamp.now().normalize()
        window = np.array(end < today if when == "past" else end >= today, bool)  # a writable copy
        if from_:
            window &= (end >= _date(from_, "from")).to_numpy(bool)
        if to:
            window &= (e["dateBegin"] <= _date(to, "to")).to_numpy(bool)
        keep = window.copy()
        for w in _fold(pd.Series([q or ""])).iloc[0].split():
            keep &= e["key"].str.contains(w, regex=False).to_numpy(bool)
        if match_type:
            keep &= e["matchTypeId"].isin(_ids(match_type, "matchType")).to_numpy(bool)
        if age:
            keep &= e["ageCategoryId"].isin(_ids(age, "age")).to_numpy(bool)
        if canton:
            keep &= e["canton"].isin([c.strip().upper() for c in canton.split(",")]).fillna(False).to_numpy(bool)
        if surface:
            keep &= e["surface"].isin([x.strip() for x in surface.split(",")]).fillna(False).to_numpy(bool)
        bounds = _category_range(event_min, event_max, "event")
        if bounds:
            keep &= ((e["strong"] <= bounds[1]) & (e["weak"] >= bounds[0])).to_numpy(bool)
        if player is not None:
            regs = t.registrations
            ids = set(regs.loc[regs["personId"].eq(player).fillna(False).to_numpy(bool), "eventId"])
            ids |= set(t.draws.loc[_played_by(t.draws, player), "eventId"])
            keep &= e["eventId"].isin(ids).to_numpy(bool)
        if when != "past":  # a tournament is upcoming or live by its kept events
            opened = e["tournamentId"].isin(e.loc[keep & e["open"].to_numpy(bool), "tournamentId"]).to_numpy(bool)
            keep &= opened if when == "upcoming" else ~opened
            opened = e["tournamentId"].isin(e.loc[window & e["open"].to_numpy(bool), "tournamentId"]).to_numpy(bool)
            window &= opened if when == "upcoming" else ~opened
        dist = np.full(len(e), np.nan)
        if near:
            dist = haversine(e["lat"].to_numpy(float), e["lon"].to_numpy(float), *self._place(t, near))
        if radius is not None and near:  # without near, radius means nothing
            keep &= dist <= radius
        if sort == "distance" and not near:
            raise HTTPException(400, "sort by distance needs near")
        field = _category_range(field_min, field_max, "field")
        if sort == "inRange" and field is None:
            raise HTTPException(400, "sort by inRange needs fieldMin or fieldMax")

        k = e[keep].assign(distance=dist[keep])
        k["inRange"] = _in_range(t, self._levels()["level"], k["eventId"], *field).to_numpy() if field else 0
        g = k.groupby("tournamentId").agg(
            dateBegin=("dateBegin", "first"), distance=("distance", "first"), players=("players", "sum"), inRange=("inRange", "sum")
        )
        by, ascending = {
            "date": (["dateBegin"], [True]),
            "-date": (["dateBegin"], [False]),
            "distance": (["distance", "dateBegin"], [True, True]),
            "players": (["players", "dateBegin"], [False, True]),
            "inRange": (["inRange", "dateBegin"], [False, True]),
        }[sort]
        g = g.reset_index().sort_values(by + ["tournamentId"], ascending=ascending + [True], na_position="last")
        events_of = k.groupby("tournamentId").indices
        out = []
        for _, x in g.iloc[offset : offset + limit].iterrows():
            tid = int(x["tournamentId"])
            ev = k.iloc[events_of[tid]].sort_values(["matchTypeId", "ageCategoryId", "strong", "eventId"])
            out.append({
                **_tournament_summary(tid, t.tournaments.loc[tid]),
                "distanceKm": _num(x["distance"], 1),
                "players": int(x["players"]),
                "inRange": int(x["inRange"]) if field else None,
                "events": [_event_summary(t, y, y["inRange"] if field else None) for _, y in ev.iterrows()],
            })
        return {
            "total": len(g),
            "tournaments": out,
            "options": self._options(t, e[window]),
            "updated": None if t.updated is None else t.updated.isoformat(timespec="seconds"),
        }

    def tournament(self, tid: int) -> dict:
        t = self.tournaments
        if tid not in t.tournaments.index:
            raise HTTPException(404, f"unknown tournament {tid}")
        r = t.tournaments.loc[tid]
        live = self._levels()
        ev = t.events[t.events["tournamentId"] == tid].sort_values(["matchTypeId", "ageCategoryId", "strong", "eventId"])
        today = pd.Timestamp.now().normalize()
        drawn = lambda day: pd.notna(day) and day <= today  # noqa: E731
        return {
            **_tournament_summary(tid, r),
            "drawingTime": _day(r["drawingTime"]),
            **{k: _str(r[k]) for k in ("surface", "place", "category", "selection", "entryFee", "organiser", "email", "phone", "homePage")},
            "mytennisUrl": MYTENNIS_URL.format(tid),
            "events": [
                {
                    **_event_summary(t, e),
                    "dateFrom": _day(e["dateFrom"]),
                    "dateTo": _day(e["dateTo"]),
                    # before the draw the PDF only says "no player"
                    "pdfUrl": _str(e["pdfUrl"]) if e["hasDraw"] or drawn(e["drawingTime"]) or drawn(e["dateBegin"]) else None,
                    "applicants": _int(e["applicants"]),
                    "players": self._registered(t, int(e["eventId"]), live),
                    "draw": self._draw(t, int(e["eventId"]), live),
                }
                for _, e in ev.iterrows()
            ],
        }

    def _registered(self, t: Tournaments, eid: int, live: pd.DataFrame) -> list[dict]:
        """The players registered in an event, strongest first (today's C, then the official
        class; unknown players last)."""
        s = self.state
        out = []
        for _, x in t.registrations.iloc[t.reg_rows.get(eid, np.array([], int))].iterrows():
            pid = _int(x["personId"])
            name = " ".join(filter(None, (_str(x["firstname"]), _str(x["lastname"]))))
            out.append({
                **self._person(pid, name, live),
                "licence": _str(x["licence"]),
                "officialClass": None if s is None or pid is None else _str(s.official_class.get(pid)),
                "C": None if pid is None else _num(live["C"].get(pid)),
            })
        out.sort(key=lambda p: (p["C"] is None, -(p["C"] or 0), CATEGORY_INDEX.get(p["officialClass"], 99), p["name"] or ""))
        return out

    def _draw(self, t: Tournaments, eid: int, live: pd.DataFrame) -> dict | None:
        """The results of an event by round, first round first; None without results."""
        d = t.draws.iloc[t.draw_rows.get(eid, np.array([], int))]
        if d.empty:
            return None
        rounds = []
        for rnd, m in d.groupby("round", sort=False):  # draws are ordered first round first
            matches = [
                {
                    "slot": int(x["slot"]),
                    "a": self._side(x, "a", live),
                    "b": self._side(x, "b", live),
                    "winner": _int(x["winner"]),
                    "score": _str(x["score"]),
                    "walkover": bool(_bool(x["walkover"])),
                    "date": _day(x["date"]),
                }
                for _, x in m.iterrows()
            ]
            rounds.append({"round": int(rnd), "matches": matches})
        return {"rounds": rounds}

    def player_tournaments(self, pid: int) -> dict:
        """The player's upcoming registrations, suggested events and past results (see the API
        contract). Suggested: upcoming singles of his gender, actives, registration open or
        unknown, his class within the event's bounds, ranked by the registered players within
        one category of his class. Past: from the draws, "reached" is the round (1 = final) of
        his last match (whether he won it is in his last result), None outside a bracket (round 0:
        pools, round robin)."""
        s = self.s()
        r = self._player(s, pid)
        t = self.tournaments
        e = t.events
        live = self._levels()
        level = CATEGORY_INDEX.get(self._class_of(s, r))

        def around(ids: pd.Series) -> np.ndarray | list:
            """Registered players within one category of the player's class, per event."""
            return [None] * len(ids) if level is None else _in_range(t, live["level"], ids, max(level - 1, 0), level + 1).to_numpy()

        regs = t.registrations
        mine = regs.loc[regs["personId"].eq(pid).fillna(False).to_numpy(bool), "eventId"]
        upcoming = (e["until"] >= s.today).to_numpy(bool)
        registered = e[upcoming & e["eventId"].isin(mine).to_numpy(bool)].sort_values(["dateBegin", "eventId"])
        registered = registered.assign(inRange=around(registered["eventId"]))

        gender_type = {"M": 1, "F": 2}.get(r["gender"])
        if level is None or gender_type is None:
            suggested = e.iloc[:0].assign(inRange=0)
        else:
            ok = (
                upcoming
                & ~e["eventId"].isin(mine).to_numpy(bool)
                & e["registrationOpen"].fillna(True).to_numpy(bool)
                & (e["matchTypeId"] == gender_type).to_numpy(bool)
                & (e["ageCategoryId"] == 1).to_numpy(bool)
                & ((e["strong"] <= level) & (e["weak"] >= level)).to_numpy(bool)
            )
            suggested = e[ok]
            suggested = suggested.assign(inRange=around(suggested["eventId"]))
            suggested = suggested.sort_values(["inRange", "dateBegin", "eventId"], ascending=[False, True, True]).head(SUGGESTED_MAX)

        d = t.draws[_played_by(t.draws, pid)]
        at = pd.Series(np.arange(len(e)), index=e["eventId"])
        past = []
        for eid, m in d.groupby("eventId"):  # rows ordered first round first
            if eid not in at.index:
                continue
            x = e.iloc[at[eid]]
            results = []
            for _, y in m.iterrows():
                side = "a" if pid in (_int(y["a1"]), _int(y["a2"])) else "b"
                winner, score = _int(y["winner"]), _str(y["score"])
                if winner is None and score is None:  # still to play (placed by the draw)
                    continue
                if side == "b" and score:  # from the player's view
                    score = " ".join("-".join(reversed(p.split("-"))) for p in score.split())
                results.append({
                    "round": int(y["round"]),
                    "opponent": self._side(y, "b" if side == "a" else "a", live),
                    "win": None if winner is None else winner == (1 if side == "a" else 2),
                    "score": score,
                    "walkover": bool(_bool(y["walkover"])),
                })
            if results:
                past.append((x["until"], {**self._tournament_item(t, x), "reached": results[-1]["round"] or None, "results": results}))
        past.sort(key=lambda p: (pd.isna(p[0]), -(p[0].value if pd.notna(p[0]) else 0)))
        return {
            "registered": [self._tournament_item(t, x, x["inRange"]) for _, x in registered.iterrows()],
            "suggested": [{**self._tournament_item(t, x, x["inRange"]), "inRange": _int(x["inRange"])} for _, x in suggested.iterrows()],
            "past": [p for _, p in past],
        }

    def outlook(self, pid: int, eid: int) -> dict:
        """The player's prospects in a singles event. Per registered opponent: today's W, the
        chance to beat him (the win probability model; null without one) and the change of the
        player's C by a win (gainWin) or a loss (lossLoss) against him: one pass of the formula
        with the player's results plus that one, the others' values fixed (as move_up).

        Expected gain, a heuristic: a knockout draw of ceil(log2(field + 1)) rounds. The
        opponent of the player's round k (1 = his first match) is the typical survivor of the
        rounds before: the registered player at the 1 - 0.5^k quantile of W (the median of the
        field, then of its stronger half, of its strongest quarter...). The chance to beat him is
        the model's, else a logistic in the W difference, 1 / (1 + e^(W_opp - W)). The player
        reaches round k+1 with reach_k times that chance; round k adds reach_k * (chance * C
        change by the win + (1 - chance) * C change by the loss), both given the wins of the
        rounds before, so the rounds sum to the expected change of C over the tournament."""
        s = self.s()
        r = self._player(s, pid)
        t = self.tournaments
        hit = np.flatnonzero(t.events["eventId"].to_numpy() == eid)
        if not len(hit):
            raise HTTPException(404, f"unknown event {eid}")
        e = t.events.iloc[hit[0]]
        if e["matchTypeId"] not in SINGLES:
            raise HTTPException(400, "the outlook is for singles only")
        live = self._levels()
        regs = t.registrations.iloc[t.reg_rows.get(eid, np.array([], int))]
        regs = regs[regs["personId"].ne(pid).fillna(True).to_numpy(bool)]
        ids = [_int(i) for i in regs["personId"]]
        W = np.array([np.nan if i is None else live["W"].get(i, np.nan) for i in ids], float)
        known = [i for i in ids if i is not None and i in s.number.index]
        c = None
        if known:
            q = pd.DataFrame({"personId": pid, "opp": known, "date": s.today, "type": winprob.TOURNAMENT})
            c = s.chance(q)
        chance = {} if c is None else dict(zip(known, c))
        own_w, own_win = self._own_rows(s, r)
        C0 = c_with(r["w0"], own_w, own_win, [], [])

        opponents = []
        for (_, x), i, w in zip(regs.iterrows(), ids, W):
            name = " ".join(filter(None, (_str(x["firstname"]), _str(x["lastname"]))))
            known_w = not np.isnan(w)
            opponents.append({
                **self._person(i, name, live),
                "W": _num(w),
                "chance": _num(chance.get(i)),
                "gainWin": _num(c_with(r["w0"], own_w, own_win, w, True) - C0) if known_w else None,
                "lossLoss": _num(c_with(r["w0"], own_w, own_win, w, False) - C0) if known_w else None,
            })
        opponents.sort(key=lambda o: (o["W"] is None, -(o["W"] or 0)))

        level = CATEGORY_INDEX.get(self._class_of(s, r))
        levels = pd.Series([live["level"].get(i) if i is not None else None for i in ids], dtype=float)
        field = {
            "count": len(ids),
            "medianW": _num(np.nanmedian(W)) if (~np.isnan(W)).any() else None,
            "topW": _num(np.nanmax(W)) if (~np.isnan(W)).any() else None,
            "inRange": None if level is None else int(((levels >= level - 1) & (levels <= level + 1)).sum()),
        }

        ranked = sorted((w, i) for i, w in zip(ids, W) if not np.isnan(w))
        expected = None
        if ranked:
            rounds, reach, gain, prior_w = [], 1.0, 0.0, []
            for k in range(1, max(1, int(np.ceil(np.log2(len(ids) + 1)))) + 1):
                w, i = ranked[min(int(round((1 - 0.5**k) * (len(ranked) - 1))), len(ranked) - 1)]
                p = chance.get(i, 1 / (1 + np.exp(w - r["W"])))
                if pd.isna(p):
                    rounds = None
                    break
                wins = np.ones(len(prior_w), bool)
                base = c_with(r["w0"], own_w, own_win, prior_w, wins)
                win_c = c_with(r["w0"], own_w, own_win, prior_w + [w], np.r_[wins, True]) - base
                loss_c = c_with(r["w0"], own_w, own_win, prior_w + [w], np.r_[wins, False]) - base
                g = reach * (p * win_c + (1 - p) * loss_c)
                rounds.append({"round": k, "reach": _num(reach), "gain": _num(g)})
                gain, reach = gain + g, reach * p
                prior_w.append(w)
            if rounds is not None:
                expected = {"gain": _num(gain), "rounds": rounds}
        return {"field": field, "opponents": opponents, "expected": expected}


class SinglePageApp(StaticFiles):
    """The UI's files; any other path outside /api gets index.html (the UI routes itself)."""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code != 404 or path == "api" or path.startswith("api/"):
                raise
            return await super().get_response("index.html", scope)


def create_app(
    data_dir: Path, static_dir: Path | None, scrape_time: str | None, tournament_hours: float | None = None
) -> FastAPI:
    """tournament_hours: refresh interval of the tournaments (0 = never), by default
    TOURNAMENT_REFRESH_HOURS from the environment (2)."""
    if tournament_hours is None:
        tournament_hours = float(os.environ.get("TOURNAMENT_REFRESH_HOURS") or TOURNAMENT_REFRESH_HOURS)
    app = App(data_dir, scrape_time, tournament_hours)

    @asynccontextmanager
    async def lifespan(_):
        app.start()
        yield

    api = FastAPI(title="Tennis Radar", lifespan=lifespan)
    api.state.app = app  # for tests

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
    def search(q: str, limit: int = Query(20, ge=1, le=100), gender: str | None = Query(None, pattern="^[MF]$")):
        return app.search(q, limit, gender)

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
    def predict(pid: int, results: str = "", remove: str = "", add: str = ""):
        return app.predict(pid, results, remove, add)

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

    @api.get("/api/tournaments")
    def tournaments(
        from_: str | None = Query(None, alias="from"),
        to: str | None = None,
        q: str | None = None,
        matchType: str | None = None,
        age: str | None = None,
        eventMin: str | None = None,
        eventMax: str | None = None,
        fieldMin: str | None = None,
        fieldMax: str | None = None,
        near: str | None = None,
        radius: float | None = Query(None, ge=0),
        canton: str | None = None,
        player: int | None = None,
        sort: str | None = None,
        limit: int = TOURNAMENT_LIMIT,
        offset: int = 0,
        when: str = "upcoming",
        surface: str | None = None,
    ):
        return app.tournament_list(
            from_, to, q, matchType, age, eventMin, eventMax, fieldMin, fieldMax, near, radius, canton, player, sort, limit,
            offset, when, surface,
        )

    @api.get("/api/tournaments/{tid}")
    def tournament(tid: int):
        return app.tournament(tid)

    @api.get("/api/players/{pid}/tournaments")
    def player_tournaments(pid: int):
        return app.player_tournaments(pid)

    @api.get("/api/players/{pid}/tournaments/events/{eid}/outlook")
    def outlook(pid: int, eid: int):
        return app.outlook(pid, eid)

    if static_dir is not None and static_dir.is_dir():
        api.mount("/", SinglePageApp(directory=static_dir, html=True), name="ui")
    return api
