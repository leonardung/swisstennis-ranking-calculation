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


DRAW_PDF_TEXT = """Club Champion Trophy TC X (155860),                       29 Septembre 2026
                                                            Page 1 / 1
MD R1/R9 (Herrendoppel)
TC X
(1) (R5/R5) 1 N / 2 N
                              1 N. / 2 N.
 BYE
                                              1 N. / 2 N.
(R7/R7) 3 N / 4 N                                 6/1
                              3 N. / 4 N.
(NC/R8) 5 N / 6 N              6/1
(2) (R6/R6) 7 N / 8 N
                              7 N. / 8 N.
BYE                                             7 N. / 8 N.
                                                   6/1
(R9/R9) 9 N / 10 N
                              9 N. / 10 N.
(NC/NC) Zed Foo / Bar Baz      6/2 6/2
"""


def test_draw_entries_from_the_pdf_text():
    entries = t.draw_entries(DRAW_PDF_TEXT)
    assert entries == [["1 N", "2 N"], None, ["3 N", "4 N"], ["5 N", "6 N"], ["7 N", "8 N"], None,
                       ["9 N", "10 N"], ["Zed Foo", "Bar Baz"]]  # fmt: skip
    assert t.draw_entries("MS R1/R9\n(R5) A B\n(R6) C D\n(R7) E F\n") is None  # 3: not a knockout draw


def test_doubles_placed_by_the_draw_with_byes_and_missing_matches():
    a, b, c, d, e = (1, 2), (3, 4), (5, 6), (7, 8), (9, 10)
    sides = [_double(b, c, "S"), _double(a, b, "S"), _double(d, e, "S")]  # E beat F: not upstream
    rows = t.draw_rows(9, sides, entries=lambda: t.draw_entries(DRAW_PDF_TEXT), people={9: "N 9", 10: "N 10"})
    got = {(r["round"], r["slot"]): (r["a1"], r["b1"], r["winner"], r["aName"], r["bName"]) for r in rows}
    assert got == {
        (3, 2): (3, 5, 1, "N 3 / N 4", "N 5 / N 6"),
        (2, 0): (1, 3, 1, "N 1 / N 2", "N 3 / N 4"),
        (2, 2): (7, 9, 1, "N 7 / N 8", "N 9 / N 10"),
        (3, 6): (9, None, 1, "N 9 / N 10", "Zed Foo / Bar Baz"),  # inferred: E plays the next round
        (1, 0): (1, 7, None, "N 1 / N 2", "N 7 / N 8"),  # still to play
    }
    # no draw (or a draw that does not fit): the finished-knockout guess, else unplaced
    rows = t.draw_rows(9, sides, entries=lambda: None)
    assert [r["round"] for r in rows] == [0, 0, 0]
    assert all(r["round"] == 0 for r in t.draw_rows(9, sides, entries=lambda: [["X Y"], ["Z W"]]))


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

