import pandas as pd

from swisstennis_ranking import tournaments as t


def _single(key2, key3, p, o, code, games=((6, 3), (6, 4)), names=("F", "F")):
    sets = list(games) + [(-1, -1)] * (5 - len(games))
    r = {"key2": key2, "key3": key3, "playerPersonId": p, "adversaryPersonId": o, "playerWinnerCode": code,
         "player": {"person": {"firstname": names[0], "lastname": str(p)}},
         "adversaryFirstname": names[1], "adversaryLastname": str(o), "playedDate": "2026-09-01T00:00:00"}  # fmt: skip
    for i, (a, b) in enumerate(sets, 1):
        r[f"playerSet{i}WonGames"], r[f"adversarySet{i}WonGames"] = a, b
    return r


def _both(key2, key3, winner, loser, games=((6, 3), (6, 4)), code=("S", "N")):
    """Both perspectives of a singles match."""
    return [
        t.single_side(_single(key2, key3, winner, loser, code[0], games)),
        t.single_side(_single(key2, key3, loser, winner, code[1], [(b, a) for a, b in games])),
    ]


def test_singles_both_perspectives_become_one_match_from_side_a():
    rows = t.draw_rows(1, _both(1, 0, 20, 10, ((6, 3), (4, 6), (10, 8))))
    assert len(rows) == 1
    r = rows[0]
    # no feeder match: side a is the lower personId, the score is from its view
    assert (r["a1"], r["b1"], r["a2"]) == (10, 20, None)
    assert r["winner"] == 2 and not r["walkover"]
    assert r["score"] == "3-6 6-4 8-10"
    assert r["aName"] == "F 10" and r["bName"] == "F 20"


def test_side_a_comes_from_the_upper_feeder():
    sides = _both(1, 0, 30, 10) + _both(2, 0, 30, 40) + _both(2, 2, 10, 20)
    final = next(r for r in t.draw_rows(1, sides) if r["round"] == 1)
    assert (final["a1"], final["b1"], final["winner"]) == (30, 10, 1)


def test_walkover_and_unknown_codes():
    unknown = t.Counter()
    wo = t.draw_rows(1, _both(1, 0, 1, 2, games=((-1, -1),), code=("1", "0")))[0]
    assert wo["walkover"] and wo["winner"] == 1 and wo["score"] is None
    odd = t.draw_rows(1, [t.single_side(_single(1, 0, 1, 2, "D"))], unknown)[0]
    assert odd["winner"] is None and unknown == {"D": 1}
    # a single perspective with a known code is enough
    lost = t.draw_rows(1, [t.single_side(_single(1, 0, 1, 2, "Z", ((6, 2), (1, 3))))])[0]
    assert lost["winner"] == 2 and lost["score"] == "6-2 1-3"


def _double(p, o, code):
    return t.double_side({
        "player1PersonId": p[0], "player2PersonId": p[1], "adversary1PersonId": o[0], "adversary2PersonId": o[1],
        "player1Firstname": "N", "player1Lastname": str(p[0]), "player2Firstname": "N", "player2Lastname": str(p[1]),
        "adversary1Firstname": "N", "adversary1Lastname": str(o[0]),
        "adversary2Firstname": "N", "adversary2Lastname": str(o[1]),
        "playerWinnerCode": code, "playerSet1WonGames": 6, "adversarySet1WonGames": 1, "playedDate": None,
    })  # fmt: skip


def test_doubles_knockout_gets_rounds_and_slots():
    x, y, z, w = (1, 2), (3, 4), (5, 6), (7, 8)
    sides = [_double(x, y, "S"), _double(y, x, "N"), _double(z, w, "S"), _double(w, z, "N"),
             _double(x, z, "S"), _double((6, 5), (2, 1), "N")]  # fmt: skip
    rows = t.draw_rows(9, sides)
    assert len(rows) == 3
    final = next(r for r in rows if r["round"] == 1)
    assert (final["a1"], final["a2"], final["b1"], final["b2"], final["winner"]) == (1, 2, 5, 6, 1)
    assert final["aName"] == "N 1 / N 2"
    assert sorted((r["round"], r["slot"]) for r in rows) == [(1, 0), (2, 0), (2, 2)]


def test_doubles_round_robin_stays_unplaced():
    x, y, z = (1, 2), (3, 4), (5, 6)
    rows = t.draw_rows(9, [_double(x, y, "S"), _double(y, z, "S"), _double(z, x, "S")])
    assert [r["round"] for r in rows] == [0, 0, 0] and sorted(r["slot"] for r in rows) == [0, 1, 2]


def _cell(col, row, name1=None, pid1=None, name2=None, pid2=None, score=None, court=None):
    c = {"position": {"column": col, "row": row}}
    for k, v in (("name1", name1), ("player1Number", pid1), ("name2", name2), ("player2Number", pid2),
                 ("score", score), ("court", court)):  # fmt: skip
        if v is not None:
            c[k] = v.replace(" ", "\xa0") if isinstance(v, str) else v
    return c


def test_draw_of_a_singles_event_in_progress():
    # 8 places: first column at rows 1 3 | 7 9 | 13 15 | 19 21, winners between their feeders
    draw = {"results": [
        _cell(1, 1, "(1) (R5) Rizzo Stefano", 1), _cell(1, 3, "BYE"),
        _cell(1, 7, "(R6) Ricci Renzo", 2), _cell(1, 9, "(NC) Varini Luca"),
        _cell(1, 13, "(R6) Adamo Mario", 4), _cell(1, 15, "(R7) Cavadini Luigi", 5),
        _cell(1, 19, "(R6) Nazzari Paolo", 6), _cell(1, 21, "BYE"),
        _cell(2, 2, "Rizzo S.", 1), _cell(2, 8, "Varini L.", score="WO 6/4 3/0"),  # retired after 6-4 3-0
        _cell(2, 14, court="30/09/26 18:00"), _cell(2, 20, "Nazzari P.", 6),
        _cell(3, 5, "Rizzo S.", 1, score="6/1 6/0"),
    ]}  # fmt: skip
    rows = t.json_draw_rows(9, draw, {1: "Stefano Rizzo"})
    got = {(r["round"], r["slot"]): (r["a1"], r["b1"], r["aName"], r["bName"], r["winner"], r["score"], r["walkover"])
           for r in rows}  # fmt: skip
    assert got == {
        (3, 2): (2, None, "Ricci Renzo", "Varini Luca", 2, "4-6 0-3", False),  # side a's view
        (3, 4): (4, 5, "Adamo Mario", "Cavadini Luigi", None, None, False),  # to play
        (2, 0): (1, None, "Stefano Rizzo", "Varini Luca", 1, "6-1 6-0", False),
    }  # byes give no row, nor (2, 2): one side unknown yet
    dates = {(r["round"], r["slot"]): r["date"] for r in rows}
    assert dates[(3, 4)] == pd.Timestamp("2026-09-30 18:00") and dates[(2, 0)] is None


def test_draw_of_a_doubles_event_by_abbreviated_names():
    draw = {"results": [
        _cell(1, 1, "(1) (R5/R5) Kieliger Thomas", 10, " / Schaub Mario", 11),
        _cell(1, 3, "(NC/R8) Von Flüe Dario", None, " / Klaus Patrick", 13),
        _cell(1, 7, "(R7/R7) Gloor Christian", 14, " / Liem Janik", 15),
        _cell(1, 9, "(R8/R8) Odermatt Pirmin", 16, " / Hess Fabian", 17),
        _cell(2, 2, "Von Flüe D.", None, " / Klaus P.", 13, score="WO"),
        _cell(2, 8, "Odermatt P.", None, " / Hess F.", score="6/3 6/4"),  # no ids: by the names
        _cell(3, 5, court="01/10/26 00:00"),
    ]}  # fmt: skip
    rows = {(r["round"], r["slot"]): r for r in t.json_draw_rows(9, draw)}
    first = rows[(2, 0)]
    assert (first["a1"], first["a2"], first["b1"], first["b2"]) == (10, 11, 13, None)
    assert (first["winner"], first["walkover"], first["score"]) == (2, True, None)
    assert first["bName"] == "Klaus Patrick / Von Flüe Dario"  # players ordered by personId
    assert (rows[(2, 2)]["winner"], rows[(2, 2)]["score"]) == (2, "3-6 4-6")
    final = rows[(1, 0)]
    assert (final["a1"], final["b1"], final["winner"], final["date"]) == (13, 16, None, pd.Timestamp("2026-10-01"))


def test_draw_helpers():
    assert t.json_draw_rows(9, {"results": [_cell(1, r, f"(R5) P{r} X", r) for r in (1, 3, 5)]}) is None
    assert t._abbreviates("Von Flüe D.", "Von Flue Dario") and not t._abbreviates("Von Flüe D.", "Von Flue Marco")
    assert t._abbreviates("Simeone M.", "Simeone Marco Tullio")
    assert t.parse_score("5/7 6/4 10/8") == ("5-7 6-4 10-8", False) and t.parse_score("WO") == (None, True)
    assert t.parse_draw_date("29/09/26 17:15") == pd.Timestamp("2026-09-29 17:15") and t.parse_draw_date("") is None


def test_age_info_from_the_category_texts():
    assert t.age_info({1: "Aktiv", 2: "Actifs"}) == {"kind": "actives", "fr": "Actifs", "de": "Aktiv"}
    assert t.age_info({1: "Junioren V (10+U)", 2: "Juniors V  (10+U)"})["max"] == 10
    seniors = t.age_info({2: "Jeunes Seniors (Men 35+ Women 30+)"})
    assert (seniors["kind"], seniors["men"], seniors["women"], seniors["de"]) == ("seniors", 35, 30, None)
    assert t.age_info({2: None})["kind"] == "other"


def test_ranking_bounds():
    assert t.parse_bounds("MS A R1/R5 ") == (9, 5)
    assert t.parse_bounds("WS N4/R3") == (7, 4)
    assert t.parse_bounds("MS 45+ R6") == (10, 10)
    assert t.parse_bounds("MD Open") == (None, None)
    assert t.ranking_id(13) == 13 and t.ranking_id(0) is None and t.ranking_id(None) is None


def test_purge_needs_half_of_the_known_tournaments():
    assert t.purge_ok(0, 0)
    assert t.purge_ok(10, 5)
    assert not t.purge_ok(10, 4)


def test_upsert_and_keep_old():
    old = pd.DataFrame({"id": [1, 2, 3], "v": ["a", "b", "c"]})
    new = pd.DataFrame({"id": [2, 4], "v": [None, "d"]})
    merged = t.upsert(old, new, "id", {2, 3, 4})
    assert merged["id"].tolist() == [1, 2, 4]
    kept = t.keep_old(new, old, "id", ["v"], {2})
    assert kept["v"].tolist() == ["b", "d"]

