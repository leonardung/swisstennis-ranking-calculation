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

