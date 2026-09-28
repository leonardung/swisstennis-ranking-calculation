import numpy as np
import pandas as pd

from swisstennis_ranking import highlights

TODAY = pd.Timestamp("2026-09-28")


def _matches(rows):
    """rows: (personId, adversaryPersonId, date, code, sets as ((mine, theirs), ...))."""
    out = []
    for pid, opp, date, code, sets in rows:
        r = {"personId": pid, "adversaryPersonId": opp, "date": pd.Timestamp(date), "playerWinnerCode": code,
             "encounterId": np.nan, "source": 410, "tournamentName": "T", "adversaryFirstname": "A", "adversaryLastname": "B"}
        sets = list(sets) + [(-1, -1)] * (3 - len(sets))
        for i, (a, b) in enumerate(sets, 1):
            r[f"playerSet{i}WonGames"], r[f"adversarySet{i}WonGames"] = a, b
        out.append(r)
    m = pd.DataFrame(out)
    return m.assign(adversaryPersonId=m["adversaryPersonId"].astype("Int64"))


def test_leaderboards_count_tiebreaks_three_sets_and_the_longest_streak():
    won, lost = ((6, 4), (6, 4)), ((4, 6), (4, 6))
    m = _matches([
        (1, 2, "2026-01-01", "S", ((7, 6), (3, 6), (10, 8))),  # tiebreak won, three sets won
        (1, 2, "2026-01-02", "S", won),
        (1, 2, "2026-01-03", "1", ()),  # walkover: skipped, does not break the run
        (1, 2, "2026-01-04", "N", ((6, 7), (6, 3), (4, 6))),  # tiebreak lost, three sets lost
        (1, 2, "2026-02-01", "S", won),
        (1, 2, "2026-02-02", "W", ((6, 1), (2, 0))),  # retirement counts in the run
        (1, 2, "2019-01-01", "S", ((7, 6), (7, 6))),  # older than five years
        (2, 1, "2026-01-01", "N", lost),
    ])
    b = highlights.leaderboards(m, TODAY)
    r = b.loc[1]
    # tiebreaks: 7-6 sets and the match tiebreak
    assert (r["tb_won"], r["tb_lost"], r["three_won"], r["three_lost"]) == (2, 1, 1, 1)
    assert (r["bagels"], r["other_sets"]) == (0, 2 + 2 + 3 + 2 + 2)  # no match tiebreak, no old match
    assert r["streak"] == 2 and r["start"] == pd.Timestamp("2026-02-01") and r["ongoing"]
    assert (r["mtb_points"], r["mtb_mine"], r["mtb_theirs"]) == (18, 10, 8)
    assert r["mtb_date"] == pd.Timestamp("2026-01-01") and pd.isna(b.loc[2, "mtb_points"])
    assert pd.isna(b.loc[2, "streak"])


def test_upsets_are_wins_against_a_higher_category_on_the_list_in_force():
    m = _matches([
        (1, 2, "2026-09-22", "S", ((6, 4), (6, 4))),  # R6 beats R3
        (3, 2, "2026-09-23", "S", ((6, 4), (6, 4))),  # R2 beats R3: no upset
        (1, 2, "2026-09-01", "S", ((6, 4), (6, 4))),  # before the start
        (4, 2, "2026-09-24", "S", ((6, 4), (6, 4))),  # R9 at the floor (no results yet) beats R3
    ])
    history = pd.DataFrame({"personId": [1, 2, 3, 4], "date": pd.to_datetime(["2026-04-01"] * 4),
                            "classification": ["R6", "R3", "R2", "R9"], "W": [5.0, 8.0, 9.0, 0.729],
                            "C": [5.2, 8.1, 9.3, 0.744]})
    u = highlights.upsets(m, history, pd.Timestamp("2026-09-21"), TODAY)
    assert u["personId"].tolist() == [1] and u["levels"].tolist() == [3] and u["score"].tolist() == ["6-4 6-4"]


def test_movers_rank_categories_gained_then_value_gained_only_up_and_rated():
    before = pd.DataFrame({"class": ["R5", "R5", "R5", "R5", "R9"], "rank": [1, 2, 3, 4, 5], "C": [5.0, 5.0, 5.0, 5.0, 0.75]}, index=[1, 2, 3, 4, 5])
    after = pd.DataFrame({"class": ["R4", "R3", "R5", "R6", "R6"], "rank": [1, 2, 3, 4, 5], "C": [6.0, 5.5, 5.2, 4.0, 4.0]}, index=[1, 2, 3, 4, 5])
    d = highlights.movers(before, after)
    assert d.index.tolist() == [2, 1, 3]
    assert d["levels"].tolist() == [2, 1, 0]


def test_placed_players_are_read_from_the_cache(tmp_path):
    cache = tmp_path / "placed_2026-04-01.parquet"
    pd.DataFrame({"personId": [3, 5]}).to_parquet(cache)
    # no data needed: the reproduction is not run again
    assert highlights.placed_players(None, None, None, pd.Timestamp("2026-04-01"), cache).tolist() == [3, 5]


def test_board_matches_are_the_counted_ones():
    won = ((6, 4), (6, 4))
    m = _matches([
        (1, 2, "2026-01-01", "S", ((7, 6), (3, 6), (10, 8))),
        (1, 2, "2026-01-02", "N", ((0, 6), (6, 0), (4, 6))),
        (1, 2, "2026-02-01", "S", won),
        (1, 2, "2026-02-02", "S", won),
    ])
    dates = lambda key: [d.day for d in highlights.board_matches(m, TODAY, key)["date"]]  # noqa: E731
    assert dates("tiebreaks") == [1] and dates("three_sets") == [1, 2]
    assert dates("bagels") == [2] and dates("streak") == [1, 2]  # 1 and 2 February


def test_record_counts_played_matches_in_the_period():
    won = ((6, 4), (6, 4))
    m = _matches([
        (1, 2, "2026-04-01", "S", won),
        (1, 2, "2026-05-01", "N", won),
        (1, 2, "2026-05-02", "1", ()),  # walkover: left out
        (1, 2, "2026-10-01", "S", won),  # the end day is out
    ])
    assert highlights.record(m, pd.Timestamp("2026-04-01"), pd.Timestamp("2026-10-01")).loc[1].tolist() == [1, 1]


def test_board_matches_of_the_longest_match_tiebreak():
    m = _matches([
        (1, 2, "2026-01-01", "S", ((6, 4), (3, 6), (10, 8))),
        (1, 2, "2026-02-01", "N", ((4, 6), (6, 3), (12, 14))),  # longer, but lost: the opponent's
        (1, 2, "2026-03-01", "S", ((6, 4), (6, 4))),
    ])
    assert list(highlights.board_matches(m, TODAY, "longest_super_tiebreak")["date"]) == [pd.Timestamp("2026-01-01")]
