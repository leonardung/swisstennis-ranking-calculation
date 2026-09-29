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

import io
import json
import re
import threading
import time
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import pypdf
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


def draw_rows(
    event_id: int, sides: list[dict], unknown: Counter | None = None, pools: bool = False, entries=None, people=None
) -> list[dict]:
    """Rows of draws.parquet for one event (round 0: not placed in a bracket, e.g. pools). Side a
    is the side coming from the upper feeder (r+1, 2s), else the one with the lower personIds.
    Results without round (doubles) are placed by the first round of the draw when `entries()`
    gives it (see draw_entries; `people`: the registered players' names by personId), with the
    missing matches whose sides are known (see place_by_entries); else by place_knockout when
    they form a finished knockout (byes unknown: the slots may differ from the draw's)."""
    matches = merge_sides(sides, unknown)
    if matches and matches[0]["round"] is None:
        first_round = None if pools or entries is None else entries()
        todo = None if first_round is None else place_by_entries(matches, first_round, people or {})
        if todo is not None:
            matches += todo
        elif pools or not place_knockout(matches):
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
        if "first" in m:  # placed by the draw's first round
            a, b = (t1, t2) if m["first"] == 0 else (t2, t1)
        elif played(t2, r + 1, 2 * s) or played(t1, r + 1, 2 * s + 2):
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


# a first-round line of a draw PDF: "(1) (R5/R6) Last First / Last First", "(R8) Last First", "BYE"
PDF_ENTRY = re.compile(r"(?:\(\d+\)\s+)?\([^()]{1,12}\)\s+(\S.*)")


def draw_entries(text: str) -> list[list[str] | None] | None:
    """The first round of a knockout draw from its PDF's text (layout mode): per position the
    players' names ("Last First") or None for a bye. They are the lines' first cells at the left
    margin (the later rounds are indented). None unless a power of 2 (not a knockout draw)."""
    out = []
    for line in text.splitlines():
        if not line.strip() or len(line) - len(line.lstrip()) > 3:
            continue
        cell = re.split(r"\s{2,}", line.strip())[0]
        if cell == "BYE":
            out.append(None)
        elif m := PDF_ENTRY.fullmatch(cell):
            out.append([x.strip() for x in m[1].split(" / ")])
    n = len(out)
    return out if n >= 2 and n & (n - 1) == 0 and any(out) else None


def fetch_draw_entries(url: str) -> list[list[str] | None] | None:
    """draw_entries of a draw PDF; None when it is not available (the servlet answers an HTML
    page for archived tournaments) or not a knockout draw."""
    resp = _get(url)
    if resp is None or not resp.content.startswith(b"%PDF"):
        return None
    try:
        pages = pypdf.PdfReader(io.BytesIO(resp.content)).pages
        return draw_entries("\n".join(p.extract_text(extraction_mode="layout") for p in pages))
    except Exception:  # a broken PDF: keep the unplaced list
        return None


def _person_key(name: str | None) -> frozenset | None:
    """A name's words, folded (accents, case): "Klaus Patrick" and "Patrick Klaus" agree."""
    if not name:
        return None
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return frozenset(w for w in re.split(r"[\s\-.]+", folded) if w)


def place_by_entries(matches: list[dict], entries: list, people: dict[int, str]) -> list[dict] | None:
    """Give round and slot to the matches of a knockout from its first round (entries, e.g. from
    the draw PDF): sides at positions p and q meet in round k (1 = first) where p and q first
    fall into the same block of 2^k, slot 2 * (p >> k) (as in singleResults). Returns the matches
    missing from the results whose two sides are known (a bye moves a side on): won by the side
    that plays a later round (a result upstream does not have, e.g. with an unlicensed player),
    else still to play (no winner). None when a side is not in the draw or two matches fall on
    the same place. `people`: personId -> name, for sides without names."""
    rounds = len(entries).bit_length() - 1
    team_key = lambda names: frozenset(_person_key(x) for x in names)  # noqa: E731
    position = {team_key(e): i for i, e in enumerate(entries) if e}
    by_name = {_person_key(n): p for p, n in people.items() if n}
    team_at, names_at, pos = {}, {}, {}
    for m in matches:
        for t in m["teams"]:
            names = [n or people.get(p) for p, n in zip(t, m["names"][t])]
            i = position.get(team_key(names)) if all(names) else None
            if i is None or pos.get(t, i) != i:
                return None
            pos[t], team_at[i], names_at[i] = i, t, m["names"][t]
    placed = {}
    for m in matches:
        p, q = (pos[t] for t in m["teams"])
        k = (p ^ q).bit_length()
        key = (rounds - k + 1, 2 * (p >> k))
        if p == q or key in placed:
            return None
        placed[key] = m
        m["round"], m["slot"], m["first"] = *key, 0 if p < q else 1
    for i, e in enumerate(entries):  # the sides without results: personIds from the registrations
        if e and i not in team_at:
            ids = [by_name.get(_person_key(x)) for x in e]
            team, names = _team(*zip(ids, [people.get(p) or x for p, x in zip(ids, e)]))
            team_at[i], names_at[i] = team, names
    last = defaultdict(int)  # position -> the last round (1 = first) where its side played
    for m in matches:
        for t in m["teams"]:
            last[pos[t]] = max(last[pos[t]], rounds - m["round"] + 1)
    # who holds each position of each round: a side (its position), None (bye), -1 (unknown)
    holder, todo = [i if e else None for i, e in enumerate(entries)], []
    for k in range(1, rounds + 1):
        nxt = []
        for j in range(len(holder) // 2):
            x, y = holder[2 * j], holder[2 * j + 1]
            m = placed.get((rounds - k + 1, 2 * j))
            if m is not None:
                x, y = (pos[t] for t in m["teams"])
                if m["winner"] is not None:
                    nxt.append(pos[m["winner"]])
                    continue
            elif x is None or y is None:  # a bye
                nxt.append(y if x is None else x)
                continue
            on = [h for h in (x, y) if h is not None and h >= 0 and last[h] > k]  # won: plays later
            nxt.append(on[0] if len(on) == 1 else -1)
            if m is None and x >= 0 and y >= 0 and team_at[x] != team_at[y]:
                teams = (team_at[x], team_at[y])
                todo.append({"round": rounds - k + 1, "slot": 2 * j, "teams": teams, "first": 0,
                             "winner": team_at[on[0]] if len(on) == 1 else None, "walkover": False, "sets": [],
                             "date": None, "names": dict(zip(teams, (names_at[x], names_at[y])))})  # fmt: skip
        holder = nxt
    return todo


def fetch_draws(
    client: Client, event_ids: list[int], pdfs: dict[int, str], people: dict[int, dict], unknown: Counter
) -> pd.DataFrame:
    """Draw rows of the events; pdfs: eventId -> draw PDF url (pools are not knockouts), people:
    eventId -> {personId: name} of the registered players."""
    rows = []
    batches = [event_ids[i : i + DRAW_BATCH] for i in range(0, len(event_ids), DRAW_BATCH)]
    for batch in tqdm(batches, desc="draws", unit_scale=DRAW_BATCH):
        for e in client.query(DRAWS_QUERY, {"ids": batch})["ioEvent"]:
            url = pdfs.get(e["id"])
            kw = {
                "pools": "DisplayPools" in (url or ""),
                "entries": None if url is None else lambda url=url: fetch_draw_entries(url),
                "people": people.get(e["id"]),
            }
            if e["singleResults"]:
                rows += draw_rows(e["id"], [single_side(r) for r in e["singleResults"]], unknown, **kw)
            if e["doubleResults"]:
                rows += draw_rows(e["id"], [double_side(r) for r in e["doubleResults"]], unknown, **kw)
    return _typed(rows, DRAWS)


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

    # draws of the tournaments in progress or ended in the last draws_days (all past ones with --all)
    started_ = tournaments["dateBegin"] <= today
    recent = started_ if all_ else started_ & (tournaments["dateEnd"] >= today - pd.Timedelta(days=draws_days))
    draw_events = events.loc[events["tournamentId"].isin(tournaments.loc[recent, "tournamentId"]), "eventId"].tolist()
    unknown = Counter()
    # the draw PDFs place the doubles (only while online: archived tournaments have none)
    online = tournaments.loc[tournaments["dateEnd"] >= today - pd.Timedelta(days=draws_days), "tournamentId"]
    urls = events.set_index("eventId")["pdfUrl"].dropna()
    pdfs = urls[urls.index.isin(events.loc[events["tournamentId"].isin(online), "eventId"]) | urls.str.contains("DisplayPools")]
    pdfs = pdfs.to_dict()
    named = regs.dropna(subset=["personId"])
    people = defaultdict(dict)
    for eid, pid, first, last in zip(named["eventId"], named["personId"], named["firstname"], named["lastname"]):
        people[eid][int(pid)] = _name(first, last)
    draws = fetch_draws(client, draw_events, pdfs, people, unknown)

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
    draws = upsert(old_d, draws, "eventId", draw_events)
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
