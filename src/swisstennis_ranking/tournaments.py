"""Download tournaments, their events, registrations and draws into data/tournaments/.

Layout:
    tournaments.parquet    one row per tournament (Hasura + REST info + geocoded city)
    events.parquet         one row per event (Hasura + the REST category of the event)
    registrations.parquet  one row per (event, player), from the REST licence lists
    draws.parquet          one row per match, both perspectives of the results merged
    geocode.json           city or place -> [lat, lon] (null: not found), kept forever
    ages.json              age category id -> kind, ages and texts (Hasura AgeCategoryLng)

Every run refreshes a window of tournaments and merges it into the existing files: rows of the
window are replaced, rows outside it are kept, and tournaments of the window that disappeared
upstream are dropped only when the fetch looks complete (an outage must not wipe the data).
"""

import json
import re
import threading
import time
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests
from tqdm import tqdm

from .api import AGE_CATEGORIES_QUERY, DRAWS_QUERY, LICENCE_HOLDERS_QUERY, PREVIOUS_SEASON_QUERY, TOURNAMENTS_QUERY, Client
from .evaluate import CATEGORIES, LOSS_CODES, WIN_CODES
from .scrape import _write
from .stats import WALKOVER_LOSS, WALKOVER_WIN

INFO_URL = "https://low-scalability.microservices.swisstennis.ch/tournaments/info"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# Nominatim's usage policy asks for an identifying User-Agent and at most one request per second.
USER_AGENT = "swisstennis-ranking/1.0 (tournament locations; +https://github.com/leonardung)"

STATUSES = [2, 5]  # published / finished; the others are drafts and junk
JUNK_MATCH_TYPE = 100
PAST_DAYS, FUTURE_DAYS = 7, 183
PAGE_SIZE = 500
DRAW_BATCH = 50  # events per draw query
LICENCE_BATCH = 500
WORKERS = 8
PURGE_MIN_FETCHED = 0.5  # share of the window's known tournaments that must come back to purge

TOURNAMENTS = {
    "tournamentId": "int64",
    "name": "str",
    "city": "str",
    "canton": "str",
    "dateBegin": "datetime64[ns]",
    "dateEnd": "datetime64[ns]",
    "drawingTime": "datetime64[ns]",
    "statusId": "int64",
    "competitionCategoryId": "Int64",
    "surface": "str",
    "place": "str",
    "category": "str",
    "selection": "str",
    "entryFee": "str",
    "organiser": "str",
    "email": "str",
    "phone": "str",
    "homePage": "str",
    "lat": "float64",
    "lon": "float64",
    "updated": "datetime64[ns]",
}
EVENTS = {
    "eventId": "int64",
    "tournamentId": "int64",
    "matchTypeId": "int64",
    "ageCategoryId": "int64",
    "lowerRankingId": "Int64",
    "upperRankingId": "Int64",
    "dateFrom": "datetime64[ns]",
    "dateTo": "datetime64[ns]",
    "statusId": "Int64",
    "competition": "str",
    "applicants": "Int64",
    "registrationOpen": "boolean",
    "onlineRegistration": "boolean",
    "pdfUrl": "str",
}
REGISTRATIONS = {"eventId": "int64", "licence": "str", "personId": "Int64", "firstname": "str", "lastname": "str"}
DRAWS = {
    "eventId": "int64",
    "round": "int64",
    "slot": "int64",
    "a1": "Int64",
    "a2": "Int64",
    "b1": "Int64",
    "b2": "Int64",
    "aName": "str",
    "bName": "str",
    "winner": "Int64",
    "walkover": "bool",
    "score": "str",
    "date": "datetime64[ns]",
}
# columns that come from the REST info: kept from the last run when the request fails
INFO_COLUMNS = ["surface", "place", "category", "selection", "entryFee", "organiser", "email", "phone", "homePage"]
EVENT_INFO_COLUMNS = ["competition", "applicants", "registrationOpen", "onlineRegistration", "pdfUrl"]


def _typed(rows: list[dict] | pd.DataFrame, schema: dict) -> pd.DataFrame:
    df = pd.DataFrame(rows).reindex(columns=list(schema))
    for col, dtype in schema.items():
        if dtype.startswith("datetime"):
            df[col] = pd.to_datetime(df[col], format="mixed").dt.normalize()
    return df.astype(schema).reset_index(drop=True)


def _text(value) -> str | None:
    """Stripped text, None for missing or blank."""
    if value is None:
        return None
    value = str(value).strip()
    return value or None


# --- ranking bounds --------------------------------------------------------------------------


def ranking_id(value) -> int | None:
    """A ranking type id (1 N1 … 4 N4, 5 R1 … 13 R9) or None when out of range."""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if 1 <= value <= len(CATEGORIES) else None


def parse_bounds(competition: str | None) -> tuple[int | None, int | None]:
    """(lower, upper) ranking ids from a competition label ("MS A R1/R5" -> (9, 5)); lower is the
    weakest bound, i.e. the larger id."""
    labels = re.findall(r"\b([NR]\d)\b", competition or "")
    ids = [CATEGORIES.index(x) + 1 for x in labels if x in CATEGORIES]
    return (max(ids), min(ids)) if ids else (None, None)


# --- draws -----------------------------------------------------------------------------------


def _name(first, last) -> str | None:
    return " ".join(x for x in (_text(first), _text(last)) if x) or None


def _sets(r: dict) -> list[tuple]:
    return [(r.get(f"playerSet{i}WonGames"), r.get(f"adversarySet{i}WonGames")) for i in range(1, 6)]


def _team(*players: tuple) -> tuple[tuple, tuple]:
    """(personIds, names) of a side, players ordered by personId so both perspectives agree."""
    players = sorted(players, key=lambda p: (p[0] is None, p[0] or 0))
    return tuple(p[0] for p in players), tuple(p[1] for p in players)


def single_side(r: dict) -> dict:
    """One row of singleResults as a match seen from the player's side."""
    person = (r.get("player") or {}).get("person") or {}
    p, p_names = _team((r["playerPersonId"], _name(person.get("firstname"), person.get("lastname"))))
    o, o_names = _team((r["adversaryPersonId"], _name(r.get("adversaryFirstname"), r.get("adversaryLastname"))))
    return {"round": r.get("key2"), "slot": r.get("key3"), "p": p, "o": o, "pNames": p_names, "oNames": o_names,
            "code": r.get("playerWinnerCode"), "sets": _sets(r), "date": r.get("playedDate") or r.get("date")}  # fmt: skip


def double_side(r: dict) -> dict:
    """One row of doubleResults (no round or slot upstream)."""
    side = lambda s, i: (r[f"{s}{i}PersonId"], _name(r.get(f"{s}{i}Firstname"), r.get(f"{s}{i}Lastname")))  # noqa: E731
    p, p_names = _team(side("player", 1), side("player", 2))
    o, o_names = _team(side("adversary", 1), side("adversary", 2))
    return {"round": None, "slot": None, "p": p, "o": o, "pNames": p_names, "oNames": o_names,
            "code": r.get("playerWinnerCode"), "sets": _sets(r), "date": r.get("playedDate") or r.get("date")}  # fmt: skip


WON = WIN_CODES + [WALKOVER_WIN]
LOST = LOSS_CODES + [WALKOVER_LOSS]


def merge_sides(sides: list[dict], unknown: Counter | None = None) -> list[dict]:
    """One match per (round, slot, pair of sides) out of both perspectives: winner (a side's
    personIds or None), walkover, sets from the first side's view, names of both sides."""
    groups = defaultdict(list)
    for s in sides:
        groups[(s["round"], s["slot"], frozenset((s["p"], s["o"])))].append(s)
    matches = []
    for rows in groups.values():
        names = {}
        for s in rows:
            for team, n in ((s["p"], s["pNames"]), (s["o"], s["oNames"])):
                if any(n) or team not in names:
                    names[team] = n
        known = [s for s in rows if s["code"] in WON + LOST]
        s = known[0] if known else rows[0]
        if not known and unknown is not None:
            unknown[s["code"]] += 1
        winner = s["p"] if s["code"] in WON else s["o"] if s["code"] in LOST else None
        walkover = s["code"] in (WALKOVER_WIN, WALKOVER_LOSS)
        matches.append({"round": s["round"], "slot": s["slot"], "teams": (s["p"], s["o"]), "winner": winner,
                        "walkover": walkover, "sets": s["sets"], "date": s["date"], "names": names})  # fmt: skip
    return matches


def place_knockout(matches: list[dict]) -> bool:
    """Give round (1 = final) and slot (feeders of (r, s) are (r+1, 2s) and (r+1, 2s+2), as in
    singleResults) to matches without them, when they form a single-elimination draw: every side
    loses at most once and one side never loses. Each side's wins are ordered by the number of
    wins of the side it beat (in a knockout that side reached a later round). False otherwise."""
    if not matches or any(m["winner"] is None for m in matches):
        return False
    loser = [m["teams"][m["teams"][0] == m["winner"]] for m in matches]
    wins, losses = defaultdict(list), Counter(loser)
    for i, m in enumerate(matches):
        wins[m["winner"]].append(i)
    teams = {t for m in matches for t in m["teams"]}
    unbeaten = [t for t in teams if not losses[t]]
    if len(unbeaten) != 1 or max(losses.values()) > 1:
        return False
    for t in wins:
        wins[t].sort(key=lambda i: (len(wins.get(loser[i], [])), str(matches[i]["date"])))
    placed = {}
    todo = [(wins[unbeaten[0]][-1], 1, 0)]
    while todo:
        i, r, s = todo.pop()
        if i in placed:
            return False
        placed[i] = (r, s)
        before = wins[matches[i]["winner"]].index(i)
        if before:
            todo.append((wins[matches[i]["winner"]][before - 1], r + 1, 2 * s))
        if loser[i] in wins:
            todo.append((wins[loser[i]][-1], r + 1, 2 * s + 2))
    if len(placed) != len(matches):
        return False
    for i, (r, s) in placed.items():
        matches[i]["round"], matches[i]["slot"] = r, s
    return True


def format_score(sets: list[tuple], flip: bool = False) -> str | None:
    """"6-3 4-6 10-8" from (player, adversary) games per set; sets not played (None, -1) skipped."""
    out = [(b, a) if flip else (a, b) for a, b in sets if a is not None and b is not None and a >= 0 and b >= 0]
    return " ".join(f"{a}-{b}" for a, b in out) or None


def draw_rows(event_id: int, sides: list[dict], unknown: Counter | None = None, pools: bool = False) -> list[dict]:
    """Rows of draws.parquet for one event out of its results upstream (round 0: not placed in a
    bracket, e.g. pools). Side a is the side coming from the upper feeder (r+1, 2s), else the one
    with the lower personIds. Results without round (doubles without a draw, see json_draw_rows)
    are placed by place_knockout when they form a finished knockout (byes unknown: the slots may
    differ from the draw's)."""
    matches = merge_sides(sides, unknown)
    if matches and matches[0]["round"] is None:
        if pools or not place_knockout(matches):
            for i, m in enumerate(matches):  # round robin or unfinished draw: unplaced
                m["round"], m["slot"] = 0, i
    at = defaultdict(list)
    for m in matches:
        at[(m["round"], m["slot"])].append(m["teams"])
    played = lambda team, r, s: any(team in t for t in at.get((r, s), []))  # noqa: E731
    rows = []
    for m in matches:
        r, s = m["round"], m["slot"]
        t1, t2 = m["teams"]
        if played(t2, r + 1, 2 * s) or played(t1, r + 1, 2 * s + 2):
            a, b = t2, t1
        elif played(t1, r + 1, 2 * s) or played(t2, r + 1, 2 * s + 2):
            a, b = t1, t2
        else:
            a, b = sorted((t1, t2), key=lambda t: [(x is None, x or 0) for x in t])
        pad = lambda t: list(t) + [None] * (2 - len(t))  # noqa: E731
        rows.append({
            "eventId": event_id, "round": r, "slot": s,
            "a1": pad(a)[0], "a2": pad(a)[1], "b1": pad(b)[0], "b2": pad(b)[1],
            "aName": " / ".join(n or "?" for n in m["names"][a]) if any(m["names"][a]) else None,
            "bName": " / ".join(n or "?" for n in m["names"][b]) if any(m["names"][b]) else None,
            "winner": None if m["winner"] is None else 1 if m["winner"] == a else 2,
            "walkover": m["walkover"],
            "score": format_score(m["sets"], flip=a != t1),
            "date": m["date"],
        })  # fmt: skip
    return rows


# the draw of a knockout event as mytennis shows it: a grid of cells, the first column the
# entries, each later column the winners of the previous one between their two feeders
DRAW_URL = "https://low-scalability.microservices.swisstennis.ch/tournaments/draw"
# a first-round cell: "(1) (R5/R6) Last First", seed and classification(s) dropped
DRAW_ENTRY = re.compile(r"(?:\(\d+\)\s+)?(?:\([^()]{1,12}\)\s+)?(\S.*)")
DRAW_DATE = re.compile(r"(\d\d)/(\d\d)/(\d\d)(?:\s+(\d\d):(\d\d))?")
SCORE_SET = re.compile(r"(\d+)/(\d+)")


def fetch_draw(event_id: int) -> dict | None:
    """The draw of an event (see json_draw_rows); {} when there is none (pools, not drawn yet:
    the service answers 204), None on failure."""
    resp = _get(DRAW_URL, params={"eventId": event_id, "lang": "fr"})
    try:
        draw = resp.json() if resp is not None and resp.content else None
    except ValueError:
        draw = None
    if resp is not None and not resp.content:  # 204: no draw
        return {}
    return draw if isinstance(draw, dict) else None


def _clean(text) -> str:
    return " ".join(str(text or "").replace("\xa0", " ").split()).removeprefix("/ ").strip()


def _words(name: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return [w for w in re.split(r"[\s\-.]+", folded) if w]


def _abbreviates(short: str, full: str) -> bool:
    """"Von Flüe D." is "Von Flüe Dario" (the last word an initial)."""
    s, f = _words(short), _words(full)
    return bool(s) and len(f) >= len(s) and s[:-1] == f[: len(s) - 1] and f[len(s) - 1].startswith(s[-1])


def _players(cell: dict) -> list[tuple[int | None, str]]:
    """(personId, name as written) of the players of a cell; unlicensed players have no id."""
    out = []
    for i in (1, 2):
        name = _clean(cell.get(f"name{i}"))
        if name:
            out.append((_int_or_none(cell.get(f"player{i}Number")), name))
    return out


def _int_or_none(x) -> int | None:
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def parse_score(text) -> tuple[str | None, bool]:
    """A draw cell's score (the winner's view, "6/4 3/0") as "6-4 3-0", and whether it is a
    walkover: "WO" alone ("WO 6/4 3/0", a retirement after these games, is a win as upstream)."""
    text = _clean(text)
    sets = " ".join(f"{a}-{b}" for a, b in SCORE_SET.findall(text))
    return sets or None, not sets and bool(re.search(r"\bW\.?O\b", text, re.I))


def parse_draw_date(text) -> pd.Timestamp | None:
    """"29/09/26 17:15" (a match to play) -> Timestamp."""
    m = DRAW_DATE.search(_clean(text))
    if not m:
        return None
    try:
        return pd.Timestamp(2000 + int(m[3]), int(m[2]), int(m[1]), int(m[4] or 0), int(m[5] or 0))
    except ValueError:
        return None


def json_draw_rows(event_id: int, draw: dict, people: dict[int, str] | None = None) -> list[dict] | None:
    """Rows of draws.parquet from a draw (fetch_draw): every match of the bracket,
    played (winner, score from side a's view, walkover) or to play (no winner, the scheduled
    date when given); a side not known yet is empty, and so is a bye (first round only). Side a
    comes from the upper feeder. A cell of column c + 1 sits between its two feeders of column c:
    it is matched to the nearest place, and its player to the entries of its block by personId,
    else by the abbreviated name ("Rizzo S.").
    `people`: personId -> name of the registered players (else the name of the draw). None when
    the first column is not a power of 2 (not a knockout draw)."""
    people = people or {}
    cells = [c for c in draw.get("results") or [] if isinstance(c.get("position"), dict)]
    col = lambda c: c["position"].get("column")  # noqa: E731
    row = lambda c: c["position"].get("row") or 0  # noqa: E731
    first = sorted((c for c in cells if col(c) == 1), key=row)
    n = len(first)
    if n < 2 or n & (n - 1):
        return None
    rounds = n.bit_length() - 1
    places = [[row(c) for c in first]]
    for _ in range(rounds):
        prev = places[-1]
        places.append([(prev[2 * j] + prev[2 * j + 1]) / 2 for j in range(len(prev) // 2)])
    at = {}  # (k, j) -> the cell of the side that won match j of round k (1 = first)
    for c in cells:
        k = (col(c) or 0) - 1
        if 1 <= k <= rounds:
            j = min(range(len(places[k])), key=lambda j: abs(places[k][j] - row(c)))
            if abs(places[k][j] - row(c)) <= 1.5 and ((k, j) not in at or _players(c)):
                at[(k, j)] = c
    entries = []  # per position: [(personId, name as written)] or None (bye)
    for c in first:
        players = [(p, DRAW_ENTRY.fullmatch(x)[1]) for p, x in _players(c)]
        entries.append(None if not players or players[0][1].upper() == "BYE" else players)
    # (personIds, names) of each position, partners in the draw's order
    sides = [e and tuple(zip(*((p, people.get(p) or x) for p, x in e))) for e in entries]

    def who(cell, lo, hi):
        """The position in [lo, hi) of the side in the cell, None when unknown."""
        players = _players(cell)
        ids = {p for p, _ in players if p is not None}
        found = [i for i in range(lo, hi) if entries[i] and ids & {p for p, _ in entries[i]}]
        if not found and players:
            found = [i for i in range(lo, hi) if entries[i] and len(entries[i]) == len(players)
                     and all(_abbreviates(s, f) for (_, s), (_, f) in zip(players, entries[i]))]  # fmt: skip
        return found[0] if len(found) == 1 else None

    holder = [None if e is None else i for i, e in enumerate(entries)]  # None: bye, -1: unknown
    rows = []
    for k in range(1, rounds + 1):
        nxt = []
        for j in range(len(holder) // 2):
            x, y = holder[2 * j], holder[2 * j + 1]
            cell = at.get((k, j))
            won = who(cell, j << k, (j + 1) << k) if cell and _players(cell) else None
            if x is None or y is None:  # a bye moves the other side on
                nxt.append(y if x is None else x)
                if k > 1 or x is y:  # a bye only shows in the first round
                    continue
                winner = None
            else:
                nxt.append(won if won is not None else -1)
                winner = 1 if won == x else 2 if won == y else None
            # a side not known yet (winner of a match still to play) or a bye is empty
            (a, a_names), (b, b_names) = (sides[i] if i is not None and i >= 0 else ((), ()) for i in (x, y))
            score, walkover = parse_score(cell.get("score")) if winner else (None, False)
            if score and winner == 2:
                score = " ".join("-".join(s.split("-")[::-1]) for s in score.split())
            pad = lambda t: list(t) + [None] * (2 - len(t))  # noqa: E731
            rows.append({
                "eventId": event_id, "round": rounds - k + 1, "slot": 2 * j,
                "a1": pad(a)[0], "a2": pad(a)[1], "b1": pad(b)[0], "b2": pad(b)[1],
                "aName": " / ".join(x or "?" for x in a_names) or None,
                "bName": " / ".join(x or "?" for x in b_names) or None,
                "winner": winner, "walkover": walkover, "score": score,
                "date": None if winner or cell is None else parse_draw_date(cell.get("court")),
            })  # fmt: skip
        holder = nxt
    return rows


def fetch_draws(
    client: Client, event_ids: list[int], knockouts: set[int], people: dict[int, dict], unknown: Counter
) -> tuple[pd.DataFrame, set[int]]:
    """Draw rows of the events: the results upstream (Hasura) for the singles closed (with
    results up to the final), else the draw shown by mytennis for the knockouts (`knockouts`: events with a
    knockout draw sheet; the results upstream give the dates of its matches), else the results
    upstream (pools, no draw). people: eventId -> {personId: name} of the registered players.
    Also returns the events whose draw could not be fetched (their old rows are kept)."""
    events = []
    batches = [event_ids[i : i + DRAW_BATCH] for i in range(0, len(event_ids), DRAW_BATCH)]
    for batch in tqdm(batches, desc="results", unit_scale=DRAW_BATCH):
        events += client.query(DRAWS_QUERY, {"ids": batch})["ioEvent"]
    # the singles results carry round and slot; the doubles ones do not, and none come before
    # the event is closed: the draw of mytennis gives them, and the matches missing upstream
    # (e.g. with unlicensed players) when the singles results have no final
    final = lambda e: any(r.get("key2") == 1 for r in e["singleResults"])  # noqa: E731
    need = [e["id"] for e in events if e["id"] in knockouts and not final(e)]
    with ThreadPoolExecutor(WORKERS) as pool:
        grids = dict(zip(need, tqdm(pool.map(fetch_draw, need), total=len(need), desc="draws")))
    # a failed request without results upstream: the old rows of the event are kept
    failed = {e["id"] for e in events if e["id"] in grids and grids[e["id"]] is None and not e["doubleResults"]}
    rows = []
    for e in events:
        sides = [single_side(r) for r in e["singleResults"]] + [double_side(r) for r in e["doubleResults"]]
        drawn = grids.get(e["id"]) and json_draw_rows(e["id"], grids[e["id"]], people.get(e["id"]))
        if drawn:
            ids = lambda xs: frozenset(x for x in xs if x is not None)  # noqa: E731
            dates = {ids(m["teams"][0] + m["teams"][1]): m["date"] for m in merge_sides(sides)} if sides else {}
            for r in drawn:
                date = dates.get(ids(r[c] for c in ("a1", "a2", "b1", "b2")))
                if r["winner"] is not None and date is not None:
                    r["date"] = date
            rows += drawn
        elif sides:
            rows += draw_rows(e["id"], sides, unknown, pools=e["id"] not in knockouts)
    print(f"{len(need)} draws fetched, {len(failed)} failed")
    return _typed(rows, DRAWS), failed


# --- tournaments and events (Hasura) ---------------------------------------------------------


def fetch_tournaments(client: Client, since: pd.Timestamp, until: pd.Timestamp) -> list[dict]:
    """Published tournaments overlapping [since, until], junk (end before start) left out."""
    where = {
        "tournamentStatusId": {"_in": STATUSES},
        "dateEnd": {"_gte": since.strftime("%Y-%m-%dT00:00:00")},
        "dateBegin": {"_lte": until.strftime("%Y-%m-%dT00:00:00")},
    }
    rows, offset = [], 0
    while True:
        page = client.query(TOURNAMENTS_QUERY, {"where": where, "offset": offset, "limit": PAGE_SIZE})["ioTournament"]
        rows += page
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return [t for t in rows if t["dateEnd"] >= t["dateBegin"]]


def tournament_row(t: dict) -> dict:
    return {
        "tournamentId": t["id"],
        "name": _text(t["name"]),
        "city": _text(t["city"]),
        "canton": _text((t.get("kanton") or {}).get("label")),
        "dateBegin": t["dateBegin"],
        "dateEnd": t["dateEnd"],
        "drawingTime": t["drawingTime"],
        "statusId": t["tournamentStatusId"],
        "competitionCategoryId": (t.get("competitionCategory") or {}).get("id"),
    }


def event_rows(t: dict) -> list[dict]:
    return [
        {
            "eventId": e["id"],
            "tournamentId": t["id"],
            "matchTypeId": e["Evt_Id_MatchType_l"],
            "ageCategoryId": e["Evt_Id_AgeCategory_l"],
            "lowerRankingId": ranking_id(e["lowerRankingTypeId"]),
            "upperRankingId": ranking_id(e["upperRankingTypeId"]),
            "dateFrom": e["dateFrom"],
            "dateTo": e["dateTo"],
            "statusId": e["eventStatusId"],
        }
        for e in t["events"]
        if e["Evt_Id_MatchType_l"] not in (None, JUNK_MATCH_TYPE) and e["Evt_Id_AgeCategory_l"] is not None
    ]


# --- REST info -------------------------------------------------------------------------------


def _get(url: str, retries: int = 2, **kwargs) -> requests.Response | None:
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, timeout=30, **kwargs)
        except requests.RequestException:
            resp = None
        if resp is not None and resp.status_code < 500:
            return resp if resp.ok else None
        if attempt < retries:
            time.sleep(2**attempt)
    return None


def fetch_info(tournament_id: int) -> dict | None:
    """The REST info of one tournament, None on failure."""
    resp = _get(INFO_URL, params={"tournamentId": tournament_id, "lang": "fr"})
    try:
        info = resp.json() if resp is not None else None
    except ValueError:
        info = None
    return info if isinstance(info, dict) else None


def info_fields(info: dict) -> dict:
    """The tournament columns from a REST info (French texts)."""
    general, contact = info.get("generalInformation") or {}, info.get("contact") or {}
    fr = lambda key: _text((general.get(key) or {}).get("fr"))  # noqa: E731
    return {
        "surface": fr("surface"),
        "place": fr("place"),
        "category": fr("category"),
        "selection": fr("selectionCriteria"),
        "entryFee": _text(general.get("entryFee")),
        "organiser": _text(contact.get("organiser")),
        "email": _text(contact.get("email")),
        "phone": _text(contact.get("phoneNumber")),
        "homePage": _text(contact.get("homePage")),
    }


def info_events(info: dict) -> tuple[dict[int, dict], list[dict]]:
    """({eventId: event columns incl. REST ranking bounds}, registrations) from a REST info."""
    events, registrations = {}, []
    for c in info.get("categories") or []:
        if c.get("eventId") is None:
            continue
        rank = (c.get("sort") or {}).get("rank") or {}
        events[c["eventId"]] = {
            "competition": _text(c.get("competition")),
            "applicants": c.get("numberOfApplicants"),
            "registrationOpen": c.get("isRegistrationOpen"),
            "onlineRegistration": c.get("isOnlineRegistration"),
            "pdfUrl": _text(c.get("pdfUrl")),
            "restLower": ranking_id(rank.get("lower")),
            "restUpper": ranking_id(rank.get("upper")),
        }
        registrations += [{"eventId": c["eventId"], "licence": x} for x in dict.fromkeys(c.get("licences") or []) if x]
    return events, registrations


def resolve_licences(client: Client, regs: pd.DataFrame, players: pd.DataFrame) -> pd.DataFrame:
    """Add personId, firstname, lastname: from players.parquet, else from the licence table."""
    known = players.set_index("licenceNumber")[["personId", "firstname", "lastname"]]
    known = known[~known.index.duplicated()]
    missing = sorted(set(regs["licence"]) - set(known.index))
    found = []
    for i in range(0, len(missing), LICENCE_BATCH):
        for x in client.query(LICENCE_HOLDERS_QUERY, {"licences": missing[i : i + LICENCE_BATCH]})["lizenz_nehmer"]:
            person = x.get("person") or {}
            found.append({"licenceNumber": x["licenceNumber"], "personId": person.get("id"),
                          "firstname": person.get("firstname"), "lastname": person.get("lastname")})  # fmt: skip
    if found:
        extra = pd.DataFrame(found).drop_duplicates("licenceNumber").set_index("licenceNumber")
        known = pd.concat([known, extra])
    return regs.join(known, on="licence")


# --- geocoding -------------------------------------------------------------------------------


_nominatim_lock = threading.Lock()  # one request at a time, at most one per second
_nominatim_last = 0.0
_cache_lock = threading.Lock()  # read-modify-write of geocode.json


def nominatim(query: str) -> list | None | bool:
    """[lat, lon] of Nominatim's first hit in Switzerland or Liechtenstein, None when there is
    none, False on a network or server error (not to be cached)."""
    global _nominatim_last
    with _nominatim_lock:
        time.sleep(max(0.0, _nominatim_last + 1 - time.monotonic()))
        resp = _get(
            NOMINATIM_URL,
            retries=1,
            params={"q": query, "countrycodes": "ch,li", "format": "json", "limit": 1},
            headers={"User-Agent": USER_AGENT},
        )
        _nominatim_last = time.monotonic()
    try:
        hits = resp.json() if resp is not None else None
    except ValueError:  # an error page
        hits = None
    if not isinstance(hits, list):
        return False
    return [float(hits[0]["lat"]), float(hits[0]["lon"])] if hits else None


def _load_cache(cache_path: Path) -> dict:
    return json.loads(cache_path.read_text()) if cache_path.exists() else {}


def _save_cache(cache: dict, cache_path: Path) -> None:
    tmp = cache_path.with_suffix(f".{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=0))
    tmp.replace(cache_path)


def geocode(cities: set[str], cache_path: Path) -> dict[str, list | None]:
    """City -> [lat, lon] (None when Nominatim does not know it), cached forever; cities that
    failed on a network error are retried on the next run."""
    cache = _load_cache(cache_path)
    todo = sorted(c for c in cities if c and c not in cache)
    try:
        for city in tqdm(todo, desc="geocoding"):
            # "Marin, Romont, Bulle", "Lodrino/Preonzo/Arbedo": else the first place named
            for query in dict.fromkeys([city, re.split(r"[,/_]| und | et ", city)[0].strip()]):
                hit = nominatim(query)
                if hit is False:  # retried on the next run
                    break
                cache[city] = hit
                if hit:
                    break
    finally:
        with _cache_lock:
            _save_cache({**_load_cache(cache_path), **cache}, cache_path)
    return cache


def locate(place: str, cache_path: Path) -> list | None | bool:
    """[lat, lon] of any place text (a city, a postal code, a village) through the geocode.json
    cache, else Nominatim (the answer is cached); None when unknown, False on a network error."""
    place = " ".join(place.split())
    with _cache_lock:
        cache = _load_cache(cache_path)
    if place in cache:
        return cache[place]
    hit = nominatim(place)
    if hit is not False:
        with _cache_lock:
            _save_cache({**_load_cache(cache_path), place: hit}, cache_path)
    return hit


# --- age categories --------------------------------------------------------------------------


def age_info(texts: dict[int, str]) -> dict:
    """Kind and ages of an age category from its texts by language id (1 de, 2 fr): actives,
    juniors (max: "Juniors II (16+U)" -> 16), seniors (men, women: "(Men 35+ Women 30+)")."""
    fr, de = _text(texts.get(2)), _text(texts.get(1))
    text = " ".join(x for x in (fr, de) if x)
    info = {"kind": "other"}
    if m := re.search(r"Men (\d+)\+\s*Women (\d+)\+", text):
        info = {"kind": "seniors", "men": int(m[1]), "women": int(m[2])}
    elif m := re.search(r"\((\d+)\+?U\)", text):
        info = {"kind": "juniors", "max": int(m[1])}
    elif re.search(r"\b(Actifs|Aktiv)\b", text):
        info = {"kind": "actives"}
    return {**info, "fr": fr and " ".join(fr.split()), "de": de and " ".join(de.split())}


def fetch_ages(client: Client) -> dict[str, dict]:
    """Age category id (as a JSON key) -> age_info."""
    texts = defaultdict(dict)
    for x in client.query(AGE_CATEGORIES_QUERY, {})["AgeCategoryLng"]:
        texts[x["id"]][x["lang"]] = x["text"]
    return {str(i): age_info(t) for i, t in sorted(texts.items())}


# --- merge -----------------------------------------------------------------------------------


def purge_ok(expected: int, fetched: int) -> bool:
    """Drop the known tournaments that disappeared only when most of them came back (else the
    fetch is probably incomplete)."""
    return expected == 0 or fetched >= PURGE_MIN_FETCHED * expected


def upsert(old: pd.DataFrame, new: pd.DataFrame, key: str, replaced) -> pd.DataFrame:
    """The rows of `old` whose key is not in `replaced`, plus `new`."""
    kept = old[~old[key].isin(list(replaced))]
    return pd.concat([kept, new], ignore_index=True) if len(kept) else new.reset_index(drop=True)


def keep_old(new: pd.DataFrame, old: pd.DataFrame, key: str, columns: list[str], ids) -> pd.DataFrame:
    """Take `columns` from `old` for the rows of `new` whose key is in `ids` (a failed request)."""
    prev = old.drop_duplicates(key).set_index(key)
    mask = new[key].isin(list(ids)) & new[key].isin(prev.index)
    new = new.copy()
    for c in columns:
        new[c] = new[c].where(~mask, new[key].map(prev[c]))
    return new


def _read(path: Path, schema: dict) -> pd.DataFrame:
    # only the schema's columns: files of an older version may carry dropped ones
    return pd.read_parquet(path).reindex(columns=list(schema)) if path.exists() else _typed([], schema)


def scrape_tournaments(data_dir: Path, all_: bool = False, draws_days: int = 14) -> None:
    out = data_dir / "tournaments"
    out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    now = pd.Timestamp.now().floor("s")
    today = now.normalize()
    client = Client()

    until = today + pd.Timedelta(days=FUTURE_DAYS)
    if all_:
        since = pd.Timestamp(client.query(PREVIOUS_SEASON_QUERY, {})["RankSeasonRange"][0]["dateBegin"])
    else:
        since = today - pd.Timedelta(days=max(PAST_DAYS, draws_days))
    raw = fetch_tournaments(client, since, until)
    print(f"{len(raw)} tournaments {since.date()} .. {until.date()}")

    tournaments = _typed([tournament_row(t) for t in raw], TOURNAMENTS)
    events = pd.DataFrame([e for t in raw for e in event_rows(t)])
    ids = tournaments["tournamentId"].tolist()

    # REST info, in parallel (plain HTTP, no login)
    with ThreadPoolExecutor(WORKERS) as pool:
        fetched = list(tqdm(pool.map(fetch_info, ids), total=len(ids), desc="info"))
    infos = {i: info for i, info in zip(ids, fetched) if info is not None}
    info_cols = pd.DataFrame([{"tournamentId": i, **info_fields(x)} for i, x in infos.items()])
    if len(info_cols):
        tournaments = tournaments.drop(columns=INFO_COLUMNS).merge(info_cols, on="tournamentId", how="left")
    event_info, regs = {}, []
    for x in infos.values():
        e, r = info_events(x)
        event_info |= e
        regs += r
    if len(events):
        rest = pd.DataFrame.from_dict(event_info, orient="index")
        events = events.join(rest, on="eventId") if len(rest) else events
        # bounds missing upstream: from the REST sort keys, else from the competition label
        for col, rest_col, pos in (("lowerRankingId", "restLower", 0), ("upperRankingId", "restUpper", 1)):
            parsed = events.get("competition", pd.Series(None, index=events.index)).map(lambda c: parse_bounds(c)[pos])
            events[col] = events[col].fillna(events.get(rest_col, parsed)).fillna(parsed)
    events = _typed(events, EVENTS)

    players_path = data_dir / "players.parquet"
    players = pd.read_parquet(players_path) if players_path.exists() else pd.DataFrame(
        columns=["licenceNumber", "personId", "firstname", "lastname"]
    )
    regs = pd.DataFrame(regs, columns=["eventId", "licence"]).drop_duplicates()
    regs = _typed(resolve_licences(client, regs, players), REGISTRATIONS)

    ages = fetch_ages(client)
    tmp = out / "ages.tmp"
    tmp.write_text(json.dumps(ages, ensure_ascii=False, indent=1))
    tmp.replace(out / "ages.json")

    # geocode the cities (new ones only)
    cache = geocode(set(tournaments["city"].dropna()), out / "geocode.json")
    coords = tournaments["city"].map(lambda c: cache.get(c) or [float("nan")] * 2)
    tournaments["lat"], tournaments["lon"] = coords.str[0], coords.str[1]
    tournaments["updated"] = now

    # draws of the tournaments drawn or started, in progress or ended in the last draws_days (all
    # past ones with --all)
    drawn = (tournaments["dateBegin"] <= today) | (tournaments["drawingTime"] <= now)
    recent = drawn if all_ else drawn & (tournaments["dateEnd"] >= today - pd.Timedelta(days=draws_days))
    draw_events = events.loc[events["tournamentId"].isin(tournaments.loc[recent, "tournamentId"]), "eventId"].tolist()
    unknown = Counter()
    # the knockouts (not pools) with at least two applicants: the others have no draw to fetch
    few = events["applicants"].fillna(2) < 2
    knockouts = set(events.loc[~events["pdfUrl"].fillna("").str.contains("DisplayPools") & ~few, "eventId"])
    named = regs.dropna(subset=["personId"])
    people = defaultdict(dict)
    for eid, pid, first, last in zip(named["eventId"], named["personId"], named["firstname"], named["lastname"]):
        people[eid][int(pid)] = _name(first, last)
    draws, failed_draws = fetch_draws(client, draw_events, knockouts, people, unknown)

    # merge into the existing files
    old_t = _read(out / "tournaments.parquet", TOURNAMENTS)
    old_e = _read(out / "events.parquet", EVENTS)
    old_r = _read(out / "registrations.parquet", REGISTRATIONS)
    old_d = _read(out / "draws.parquet", DRAWS)
    failed = set(ids) - set(infos)
    tournaments = keep_old(tournaments, old_t, "tournamentId", INFO_COLUMNS, failed)
    failed_events = events.loc[events["tournamentId"].isin(failed), "eventId"]
    events = keep_old(events, old_e, "eventId", EVENT_INFO_COLUMNS, failed_events)

    in_window = old_t["tournamentId"][(old_t["dateEnd"] >= since) & (old_t["dateBegin"] <= until)]
    expected = set(in_window)
    gone = expected - set(ids)
    if gone and not purge_ok(len(expected), len(expected & set(ids))):
        print(f"only {len(expected & set(ids))} of {len(expected)} known tournaments fetched: nothing purged")
        gone = set()
    replaced = set(ids) | gone
    tournaments = upsert(old_t, tournaments, "tournamentId", replaced)
    events = upsert(old_e, events, "tournamentId", replaced)
    ok_events = events.loc[events["tournamentId"].isin(set(infos)), "eventId"]
    regs = upsert(old_r, regs, "eventId", ok_events)
    draws = upsert(old_d, draws, "eventId", set(draw_events) - failed_draws)
    regs = regs[regs["eventId"].isin(events["eventId"])]
    draws = draws[draws["eventId"].isin(events["eventId"])]

    _write(_typed(regs, REGISTRATIONS), out, "registrations")
    _write(_typed(draws, DRAWS), out, "draws")
    _write(_typed(events, EVENTS), out, "events")
    _write(_typed(tournaments, TOURNAMENTS), out, "tournaments")
    print(
        f"{len(tournaments)} tournaments ({len(gone)} purged, {len(failed)} without REST info), "
        f"{len(events)} events, {len(regs)} registrations "
        f"({regs['personId'].isna().sum()} unknown licences), {len(draws)} draw matches "
        f"in {time.monotonic() - started:.0f}s"
    )
    if unknown:
        print(f"results with an unknown winner code: {dict(unknown)}")
