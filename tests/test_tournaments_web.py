import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from swisstennis_ranking import web

TODAY = pd.Timestamp.now().normalize()
D = lambda days: TODAY + pd.Timedelta(days=days)  # noqa: E731


def _write(tdir, name, rows, cols):
    df = pd.DataFrame(rows, columns=list(cols))
    for c, t in cols.items():
        df[c] = df[c].astype(t)
    df.to_parquet(tdir / f"{name}.parquet")


def _tables(tdir):
    tdir.mkdir()
    T = web.TOURNAMENT_TABLES
    t = lambda tid, name, city, canton, b, e, lat, lon: {  # noqa: E731
        "tournamentId": tid, "name": name, "city": city, "canton": canton, "dateBegin": D(b), "dateEnd": D(e),
        "drawingTime": D(b - 2), "statusId": 5, "competitionCategoryId": 1, "surface": "Terre battue", "place": None,
        "category": None, "selection": None, "entryFee": "40", "organiser": "TC " + city, "email": None, "phone": None,
        "homePage": None, "lat": lat, "lon": lon, "updated": TODAY,
    }
    _write(tdir, "tournaments", [
        t(100, "Open Lausanne", "Lausanne", "VD", 10, 15, 46.52, 6.63),
        t(200, "Cup Bern", "Bern", "BE", 3, 5, 46.95, 7.45),
        t(300, "Past Zurich", "Zürich", "ZH", -20, -15, 47.37, 8.54),
    ], T["tournaments"])
    ev = lambda eid, tid, mt, lower, upper, b, e, open_: {  # noqa: E731
        "eventId": eid, "tournamentId": tid, "matchTypeId": mt, "ageCategoryId": 1, "lowerRankingId": lower,
        "upperRankingId": upper, "dateFrom": D(b), "dateTo": D(e), "statusId": 1, "competition": f"E{eid}",
        "applicants": 9, "registrationOpen": open_, "onlineRegistration": True, "pdfUrl": f"pdf{eid}",
    }
    _write(tdir, "events", [
        ev(1001, 100, 1, 13, 9, 10, 15, True),  # MS R9/R5
        ev(1002, 100, 2, 9, 5, 10, 15, True),  # WS R5/R1
        ev(2001, 200, 1, 8, 5, 3, 5, None),  # MS R4/R1
        ev(2002, 200, 3, None, None, 3, 5, True),  # MD open
        ev(3001, 300, 1, 13, 5, -20, -15, False),
    ], T["events"])
    reg = lambda eid, pid, lic, first, last: {"eventId": eid, "licence": lic, "personId": pid, "firstname": first, "lastname": last}  # noqa: E731
    _write(tdir, "registrations", [
        reg(2001, 3, "3", "C", "Three"), reg(2001, 2, "2", "B", "Two"), reg(2001, 5, "5", "E", "Five"),
        reg(2001, None, "9.9", "Un", "Known"), reg(1001, 4, "4", "D", "Four"), reg(1001, 1, "1", "A", "One"),
        reg(1002, 6, "6", "F", "Six"),
    ], T["registrations"])
    m = lambda rnd, slot, a, b, winner, score: {  # noqa: E731
        "eventId": 3001, "round": rnd, "slot": slot, "a1": a, "a2": None, "b1": b, "b2": None,
        "aName": f"P {a}", "bName": f"P {b}", "winner": winner, "walkover": False, "score": score, "date": D(-16),
    }
    _write(tdir, "draws", [
        m(1, 0, 2, 1, 1, "6-2 3-6 10-8"), m(2, 2, 2, 5, 1, "6-0 6-0"), m(2, 0, 1, 3, 1, "6-3 6-4"),
    ], T["draws"])
    (tdir / "ages.json").write_text(json.dumps({
        "1": {"kind": "actives", "fr": "Actifs", "de": "Aktiv"}, "3": {"kind": "juniors", "max": 16, "fr": "Juniors II (16+U)"},
        "5": {"kind": "juniors", "max": 12}, "7": {"kind": "seniors", "men": 35, "women": 30}, "16": {"kind": "seniors", "men": 40, "women": 35},
    }))  # fmt: skip


def _state(chance=None):
    """A minimal ranking state: players 1..6 on today's list (6 is a woman)."""
    p = pd.DataFrame({
        "personId": [1, 2, 3, 4, 5, 6], "class": ["R3", "R2", "R4", "R6", "R3", "R5"], "on_list": True,
        "C": [6.5, 7.5, 5.5, 3.5, 6.2, 4.5], "W": [6.4, 7.4, 5.4, 3.4, 6.1, 4.4], "w0": [6.4, 7.4, 5.4, 3.4, 6.1, 4.4],
        "gender": ["M", "M", "M", "M", "M", "F"],
    })
    rows = pd.DataFrame({"opp_w": [6.0, 7.0], "win": [True, False]})
    return SimpleNamespace(
        today=TODAY, p=p, number=pd.Series(p.index, index=p["personId"]), rows=rows, player_rows={0: np.array([0, 1])},
        people=pd.DataFrame({"name": [f"P{i}" for i in range(1, 7)]}, index=pd.Index(range(1, 7), name="personId")),
        official_class=pd.Series(["R4", "R2", "R4", "R7", "R3", "R5"], index=range(1, 7)),
        chance=chance or (lambda q: None), official_date=TODAY, clubs=None, updated=TODAY.to_pydatetime(),
        period=SimpleNamespace(start=TODAY - pd.Timedelta(days=365)),
    )


PLACES = {"1025": [46.51, 6.56], "down": False}  # a postal code near Lausanne; Nominatim down


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "locate", lambda place, cache: PLACES.get(place))
    _tables(tmp_path / web.TOURNAMENT_DIR)
    api = web.create_app(tmp_path, None, None)
    api.state.app.state = _state()
    return TestClient(api)


def _ids(r):
    return [t["id"] for t in r.json()["tournaments"]]


def test_list_filters_and_options(client):
    r = client.get("/api/tournaments").json()
    assert [t["id"] for t in r["tournaments"]] == [200, 100] and r["total"] == 2  # upcoming, by date
    # options: of the upcoming tournaments (not 300), whatever the other filters
    assert r["options"] == {"matchTypes": [1, 2, 3], "ages": [{"id": 1, "kind": "actives"}], "surfaces": ["Terre battue"],
                            "cantons": ["BE", "VD"], "cities": ["Bern", "Lausanne"]}  # fmt: skip
    assert client.get("/api/tournaments", params={"age": "2"}).json()["options"] == r["options"]
    assert r["updated"] is not None
    t200 = r["tournaments"][0]
    assert t200["players"] == 3 + 1 + 9 and t200["inRange"] is None and t200["distanceKm"] is None  # 2002: applicants
    assert t200["surface"] == "Terre battue"
    assert t200["events"][0] == {"id": 2001, "matchType": 1, "age": 1, "ageInfo": {"id": 1, "kind": "actives"}, "lower": "R4",
                                 "upper": "R1", "competition": "E2001", "players": 4, "inRange": None,
                                 "registrationOpen": None, "hasDraw": False}  # fmt: skip
    # past: newest first; live: not over and no kept event takes registrations (2001 alone)
    past = client.get("/api/tournaments", params={"when": "past"}).json()
    assert [t["id"] for t in past["tournaments"]] == [300] and past["options"]["cities"] == ["Zürich"]
    assert _ids(client.get("/api/tournaments", params={"when": "live"})) == []
    assert _ids(client.get("/api/tournaments", params={"when": "live", "matchType": "1"})) == [200]
    assert _ids(client.get("/api/tournaments", params={"matchType": "1"})) == [100]
    assert _ids(client.get("/api/tournaments", params={"from": D(6).date().isoformat()})) == [100]
    assert _ids(client.get("/api/tournaments", params={"surface": "Gazon,Terre battue"})) == [200, 100]
    assert _ids(client.get("/api/tournaments", params={"surface": "Gazon"})) == []
    assert _ids(client.get("/api/tournaments", params={"to": D(7).date().isoformat()})) == [200]
    # only tournaments held within the dates, not the ones overlapping them
    assert _ids(client.get("/api/tournaments", params={"from": D(4).date().isoformat(), "to": D(12).date().isoformat()})) == []
    one = client.get("/api/tournaments", params={"matchType": "2"}).json()["tournaments"]
    assert [(t["id"], [e["id"] for e in t["events"]]) for t in one] == [(100, [1002])]
    assert _ids(client.get("/api/tournaments", params={"q": "bern"})) == [200]
    assert _ids(client.get("/api/tournaments", params={"q": "tc lausanne"})) == [100]  # organiser
    assert _ids(client.get("/api/tournaments", params={"canton": "vd,ZH"})) == [100]
    assert _ids(client.get("/api/tournaments", params={"age": "2"})) == []
    # events overlapping R3..R2 (either order): 1001 (R9/R5) is out
    for a, b in (("R3", "R2"), ("R2", "R3")):
        got = client.get("/api/tournaments", params={"eventMin": a, "eventMax": b}).json()["tournaments"]
        assert {t["id"]: [e["id"] for e in t["events"]] for t in got} == {200: [2001, 2002], 100: [1002]}
    # only the weakest end: R6 and stronger
    got = client.get("/api/tournaments", params={"eventMin": "R6"}).json()["tournaments"]
    assert {t["id"]: [e["id"] for e in t["events"]] for t in got} == {200: [2001, 2002], 100: [1001, 1002]}
    assert _ids(client.get("/api/tournaments", params={"player": 1})) == [100]
    assert _ids(client.get("/api/tournaments", params={"player": 1, "when": "past"})) == [300]


def test_age_options_order(client):
    app = client.app.state.app
    e = app.tournaments.events.iloc[[0] * 6].assign(ageCategoryId=[16, 5, 1, 7, 3, 99])
    assert [a["id"] for a in app._options(app.tournaments, e)["ages"]] == [1, 3, 5, 7, 16, 99]
    assert app._options(app.tournaments, e)["ages"][-1] == {"id": 99, "kind": "other"}


def test_list_in_range_distance_sorts_and_paging(client):
    r = client.get("/api/tournaments", params={"fieldMin": "R3", "fieldMax": "R2", "sort": "inRange"}).json()
    assert [(t["id"], t["inRange"]) for t in r["tournaments"]] == [(200, 2), (100, 1)]  # 100: 1 (R3) in 1001
    assert [e["inRange"] for e in r["tournaments"][0]["events"]] == [2, 0]  # 2 (R2) and 5 (R3); 3 is R4
    r = client.get("/api/tournaments", params={"near": "Lausanne", "sort": "distance"}).json()["tournaments"]
    assert [t["id"] for t in r] == [100, 200] and r[0]["distanceKm"] == 0 and 70 < r[1]["distanceKm"] < 90
    assert _ids(client.get("/api/tournaments", params={"near": "Lausanne", "radius": 50})) == [100]
    assert _ids(client.get("/api/tournaments", params={"near": " lausanne ", "radius": 50})) == [100]  # a city of the data
    r = client.get("/api/tournaments", params={"near": "1025", "radius": 30}).json()["tournaments"]  # any place
    assert [t["id"] for t in r] == [100] and 0 < r[0]["distanceKm"] < 10
    assert client.get("/api/tournaments", params={"near": "Nowhere"}).json() == {"detail": "unknown place"}
    assert client.get("/api/tournaments", params={"near": "down"}).status_code == 503
    assert _ids(client.get("/api/tournaments", params={"radius": 50})) == [200, 100]  # radius without near: ignored
    assert _ids(client.get("/api/tournaments", params={"sort": "-date"})) == [100, 200]
    assert _ids(client.get("/api/tournaments", params={"sort": "players"})) == [200, 100]
    r = client.get("/api/tournaments", params={"limit": 1, "offset": 1}).json()
    assert r["total"] == 2 and [t["id"] for t in r["tournaments"]] == [100]
    assert len(client.get("/api/tournaments", params={"limit": 1000}).json()["tournaments"]) == 2  # clamped


@pytest.mark.parametrize("params", [
    {"sort": "name"}, {"from": "2026-13-01"}, {"to": "tomorrow"}, {"eventMin": "X9"}, {"fieldMax": "R10"},
    {"matchType": "a"}, {"near": "Nowhere"}, {"sort": "distance"}, {"sort": "inRange"}, {"when": "soon"},
])
def test_list_rejects_bad_parameters(client, params):
    assert client.get("/api/tournaments", params=params).status_code == 400


def test_detail_players_and_draw(client):
    r = client.get("/api/tournaments/200").json()
    assert r["mytennisUrl"] == "https://www.mytennis.ch/fr/tournois/200"
    assert r["surface"] == "Terre battue" and r["drawingTime"] == D(1).date().isoformat()
    ev = r["events"][0]
    assert ev["id"] == 2001 and ev["draw"] is None and ev["applicants"] == 9
    assert ev["pdfUrl"] is None  # not drawn yet
    assert client.get("/api/tournaments/300").json()["events"][0]["pdfUrl"] == "pdf3001"
    # strongest first (today's C), unknown players last
    assert [p["personId"] for p in ev["players"]] == [2, 5, 3, None]
    assert ev["players"][0] == {"personId": 2, "name": "P2", "class": "R2", "licence": "2", "officialClass": "R2", "C": 7.5}  # the ranking's name
    assert ev["players"][-1] == {"personId": None, "name": "Un Known", "class": None, "licence": "9.9", "officialClass": None, "C": None}
    draw = client.get("/api/tournaments/300").json()["events"][0]["draw"]
    assert [x["round"] for x in draw["rounds"]] == [2, 1]  # first round first
    assert [m["slot"] for m in draw["rounds"][0]["matches"]] == [0, 2]
    final = draw["rounds"][1]["matches"][0]
    assert final["a"] == [{"personId": 2, "name": "P2", "class": "R2"}] and final["winner"] == 1 and final["walkover"] is False
    assert client.get("/api/tournaments/999").status_code == 404


def test_player_registered_suggested_past(client):
    r = client.get("/api/players/1/tournaments").json()
    assert [(x["tournament"]["id"], x["event"]["id"]) for x in r["registered"]] == [(100, 1001)]
    assert r["registered"][0]["tournament"]["players"] == 2 + 1
    # men's singles, R3 within the bounds, not registered: 2001 (R4/R1); 3 players within R2..R4
    assert [(x["event"]["id"], x["inRange"]) for x in r["suggested"]] == [(2001, 3)]
    assert [x["event"]["id"] for x in r["past"]] == [3001]
    past = r["past"][0]
    assert past["reached"] == 1  # lost the final
    assert [(x["round"], x["win"], x["score"]) for x in past["results"]] == [(2, True, "6-3 6-4"), (1, False, "2-6 6-3 8-10")]
    assert past["results"][1]["opponent"] == [{"personId": 2, "name": "P2", "class": "R2"}]
    # the woman: no men's events suggested; 1002 (WS R5/R1) is her registration
    r = client.get("/api/players/6/tournaments").json()
    assert r["suggested"] == [] and [x["event"]["id"] for x in r["registered"]] == [1002]
    assert client.get("/api/players/99/tournaments").status_code == 404


def test_suggested_around_the_clubs(client):
    app = client.app.state.app
    assert client.get("/api/players/1/tournaments").json()["clubs"] == []  # clubs unknown: by the field
    # a club in Bern (the 2001 event) and one in Lausanne: the closest counts
    app.state.clubs = pd.DataFrame({
        "personId": [1, 1, 2], "clubId": [7, 8, 9], "club": ["TC Bern", "TC Lausanne", "TC X"],
        "city": ["Bern", "Lausanne", "X"], "lat": [46.95, 46.52, 47.0], "lon": [7.46, 6.63, 8.0],
    })  # fmt: skip
    r = client.get("/api/players/1/tournaments").json()
    assert r["clubs"] == [{"name": "TC Bern", "city": "Bern"}, {"name": "TC Lausanne", "city": "Lausanne"}]
    assert [(x["event"]["id"], x["tournament"]["distanceKm"]) for x in r["suggested"]] == [(2001, 0.8)]


def test_outlook_without_and_with_model(client):
    r = client.get("/api/players/1/tournaments/events/2001/outlook").json()
    assert r["field"] == {"count": 4, "medianW": 6.1, "topW": 7.4, "inRange": 3}
    assert [o["personId"] for o in r["opponents"]] == [2, 5, 3, None]
    top = r["opponents"][0]
    assert top["chance"] is None and top["gainWin"] > 0 > top["lossLoss"]
    assert r["opponents"][-1]["W"] is None and r["opponents"][-1]["gainWin"] is None
    # 5 players: a draw of 8, byes for the 3 strongest; player 1 is the second: from the semi-finals
    rounds = r["expected"]["rounds"]
    assert [x["round"] for x in rounds] == [2, 1] and rounds[0]["reach"] == 1.0 and rounds[1]["reach"] < 1
    # C if out: a loss in the semi-final, a win then a loss in the final, two wins for the title
    assert rounds[0]["ifOut"] < 0 < rounds[1]["ifOut"] < r["expected"]["ifTitle"]
    assert client.get("/api/players/1/tournaments/events/1002/outlook").status_code == 200  # not registered: fine

    app = client.app.state.app
    app.state = _state(chance=lambda q: np.full(len(q), 0.5))
    r = client.get("/api/players/1/tournaments/events/2001/outlook").json()
    assert r["opponents"][0]["chance"] == 0.5
    ex = r["expected"]
    assert [x["reach"] for x in ex["rounds"]] == [1.0, 0.5] and ex["title"] == 0.25
    # each outcome by its chance: out in the semi-final 0.5, in the final 0.25, the title 0.25
    out = [x["ifOut"] for x in ex["rounds"]]
    assert ex["gain"] == pytest.approx(0.5 * out[0] + 0.25 * out[1] + 0.25 * ex["ifTitle"], abs=2e-3)
    assert client.get("/api/players/1/tournaments/events/2002/outlook").status_code == 400  # doubles
    assert client.get("/api/players/1/tournaments/events/9/outlook").status_code == 404


def test_missing_files_give_empty_results(tmp_path):
    api = web.create_app(tmp_path, None, None)
    api.state.app.state = _state()
    c = TestClient(api)
    r = c.get("/api/tournaments").json()
    assert r == {"total": 0, "tournaments": [], "options": {"matchTypes": [], "ages": [], "surfaces": [], "cantons": [], "cities": []}, "updated": None}
    assert c.get("/api/tournaments/1").status_code == 404
    assert c.get("/api/players/1/tournaments").json() == {"registered": [], "suggested": [], "past": [], "clubs": []}
    assert c.get("/api/meta").json()["tournaments"] == {"updated": None, "running": False, "lastError": None}


def test_scrape_reloads_only_the_tournaments(tmp_path):
    app = web.App(tmp_path, None, 2)
    state = app.state = _state()
    calls = []

    def command(args, log_name, status=None):
        assert app.scrape_lock.locked() and status is app.tournament_scrape
        calls.append((args, log_name))
        _tables(tmp_path / web.TOURNAMENT_DIR)
        return True

    app._command = command
    assert app.tournaments.events.empty
    app._scrape_tournaments()
    assert calls == [(["scrape-tournaments"], "tournaments.log")]
    assert len(app.tournaments.events) == 5 and app.state is state
    assert app.tournament_scrape["running"] is False and app.tournament_scrape["last_run"] is not None
    assert app.scrape["last_run"] is None
