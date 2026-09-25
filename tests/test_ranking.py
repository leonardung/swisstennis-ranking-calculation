import math

import numpy as np
import pandas as pd
import pytest

from swisstennis_ranking import evaluate, ranking


def dcl_formula(w0, wins, losses):
    """DCL Art. 5 al. 6-7, written out literally for one player."""
    pos = math.log(sum(math.exp(w) for w in wins) + math.exp(w0))
    neg = math.log(sum(math.exp(-w) for w in losses) + math.exp(-w0))
    return (pos - neg) / 2, (pos + neg) / 6


def test_single_pass_matches_formula():
    w0 = np.array([5.0, 3.0])
    player = np.array([0, 0, 0, 1])
    opp_w = np.array([6.0, 4.0, 7.5, 2.0])
    win = np.array([True, True, False, False])
    W, R = ranking.single_pass(w0, player, opp_w, win)
    assert (W[0], R[0]) == pytest.approx(dcl_formula(5.0, [6.0, 4.0], [7.5]))
    assert (W[1], R[1]) == pytest.approx(dcl_formula(3.0, [], [2.0]))


def test_no_matches_keeps_w0():
    W, R = ranking.compute(np.array([4.2]), *(np.array([], dtype=t) for t in (int, int, float, bool)))
    assert W[0] == pytest.approx(4.2) and R[0] == pytest.approx(0.0)


@pytest.mark.parametrize("n_matches, discarded", [(5, 0), (6, 1), (13, 2), (30, 4)])
def test_discarded_losses(n_matches, discarded):
    opp_w = np.arange(n_matches, dtype=float)  # all losses, weakest opponents first
    counted = ranking.counted_losses(np.zeros(n_matches, int), opp_w, np.zeros(n_matches, bool), 1)
    assert list(counted) == [False] * discarded + [True] * (n_matches - discarded)


def test_discard_only_losses_and_weakest_opponent():
    player = np.array([0] * 6)
    opp_w = np.array([1.0, 2.0, 5.0, 3.0, 4.0, 6.0])
    win = np.array([True, True, False, False, True, True])
    counted = ranking.counted_losses(player, opp_w, win, 1)
    assert list(counted) == [False, False, True, False, False, False]  # loss vs 3.0 dropped


def test_passes_use_opponent_previous_values():
    # Two players who only play each other: a hand-rolled 5-pass loop must agree.
    w0 = np.array([5.0, 4.0])
    player, opp, win = np.array([0, 1]), np.array([1, 0]), np.array([True, False])
    W_expected = w0.copy()
    for _ in range(5):
        a = dcl_formula(w0[0], [W_expected[1]], [])
        b = dcl_formula(w0[1], [], [W_expected[0]])
        W_expected = np.array([a[0], b[0]])
    W, R = ranking.compute(w0, player, opp, np.full(2, np.nan), win)
    np.testing.assert_allclose(W, W_expected)
    assert R[0] == pytest.approx(a[1])


@pytest.mark.parametrize(
    "date, official",
    [("2025-04-15", "2025-10-01"), ("2025-10-20", "2026-04-01"), ("2026-01-05", "2026-04-01"), ("2025-04-01", "2025-04-01")],
)
def test_next_list(date, official):
    assert evaluate.next_list(pd.Timestamp(date)) == pd.Timestamp(official)


def small_league():
    history = pd.DataFrame(
        {
            "personId": [1, 2, 3, 1, 2, 3],
            "date": pd.to_datetime(["2024-10-01"] * 3 + ["2025-04-01"] * 3),
            "firstname": ["a", "b", "c"] * 2,
            "lastname": ["x", "y", "z"] * 2,
            "classification": ["R3", "R4", "R5"] * 2,
            "rank": [1, 2, 3] * 2,
            "W": [6.0, 5.0, 4.0, 6.1, 4.9, 4.0],
            "C": [0.0, 0.0, 0.0, 7.0, 5.5, 4.0],
            "games": [0] * 6,
        }
    )
    matches = pd.DataFrame(
        {
            "personId": [1, 2, 1, 2],
            "date": pd.to_datetime(["2024-06-01", "2024-06-01", "2025-05-01", "2025-05-01"]),
            "adversaryPersonId": pd.array([2, 1, 99, 1], dtype="Int64"),
            "playerWinnerCode": ["S", "N", "S", "S"],
            "matchNotConsidered": [False] * 4,
            "encounterId": [10, 10, 11, 12],
            "adversaryValue": [5.0, 6.0, 3.0, 6.0],
        }
    )
    players = pd.DataFrame({"personId": [1, 2, 3], "gender": ["M", "M", "M"]})
    return history, matches, players


def test_evaluate_end_to_end():
    history, matches, players = small_league()
    period = evaluate.build_period(history, matches, players, pd.Timestamp("2025-04-01"))
    assert (period.start, period.end) == (pd.Timestamp("2024-04-01"), pd.Timestamp("2025-04-01"))
    assert len(period.matches) == 2  # matches after the window are excluded

    p = evaluate.evaluate(period).set_index("personId")
    W1, W2 = 6.0, 5.0
    for _ in range(5):
        W1, W2 = dcl_formula(6.0, [W2], [])[0], dcl_formula(5.0, [], [W1])[0]
    assert p.loc[1, "W"] == pytest.approx(W1, abs=1e-3)
    assert p.loc[3, "W"] == pytest.approx(4.0) and p.loc[3, "n_matches"] == 0
    assert list(p["rank"]) == [1, 2, 3]
    # Reproduce mode reads the category bounds off the published list.
    published = p.assign(C=p["C_pub"], W=p["W_pub"])
    assert list(evaluate.categories(published, evaluate.published_cuts(published))[1]) == ["R3", "R4", "R5"]

    # The interpolation points are the previous categories' means (6, 5, 4 -> R3, R4, R5), so
    # predicting without the published list gives the same values here.
    predicted = evaluate.evaluate(period, predict=True).set_index("personId")
    np.testing.assert_allclose(predicted["W"], p["W"])
    assert list(predicted["class"]) == ["N1"] * 3  # quota: top 10 are N1


def test_predict_unpublished_list():
    history, matches, players = small_league()
    period = evaluate.build_period(history, matches, players, pd.Timestamp("2025-10-01"))
    assert not period.published and period.end == pd.Timestamp("2025-10-01")
    with pytest.raises(ValueError):
        evaluate.evaluate(period)
    p = evaluate.evaluate(period, predict=True).set_index("personId")
    assert p["on_list"].all()  # the licensed players
    assert p.loc[1, "n_matches"] == 1  # the May 2025 win against an unknown opponent


def test_start_values_minimum_for_active_players():
    p = pd.DataFrame(
        {
            "gender": ["M", "M", "M", "M"],
            "W5_prev": [0.75, 0.75, 5.0, np.nan],
            "n_matches": [0, 3, 3, 2],
        }
    )
    table = {"M": (np.array([0.75, 4.0, 6.0]), np.array([0.75, 3.4, 5.2]))}
    w0 = evaluate.start_values(p, table)
    assert list(w0.round(3)) == [0.75, 1.0, 4.3, 1.0]


def test_fixed_opponent_value_is_used_in_every_pass():
    w0 = np.array([5.0, 4.0])
    player, opp, win = np.array([0, 1]), np.array([1, 0]), np.array([True, False])
    fixed = np.array([np.nan, 7.0])  # player 1 has an assigned value
    W, _ = ranking.compute(w0, player, opp, np.full(2, np.nan), win, fixed=fixed)
    assert W[0] == pytest.approx(dcl_formula(5.0, [7.0], [])[0])


def test_categories_by_quota_with_ties_and_foreigners():
    n = 12
    p = pd.DataFrame(
        {
            "gender": ["M"] * n,
            "C": [20.0 - i for i in range(10)] + [10.0, 10.0],  # two tied at rank 11
            "W": [15.0] * 10 + [9.0, 9.0],
            "foreign": [False] * n,
            "classified": [False] * n,
            "on_list": [True] * n,
        }
    )
    p.loc[0, "foreign"] = True  # best player is a foreigner: no quota slot
    rank, cat = evaluate.categories(p)
    assert rank[0] == 1 and rank[1] == 1  # the foreigner shares rank 1 with the best Swiss
    assert list(rank[10:]) == [10, 10] and list(cat[10:]) == ["N1", "N1"]


def test_new_player_values_are_category_means():
    p = pd.DataFrame(
        {
            "gender": ["M", "M", "M", "F"],
            "class_pub": ["R5", "R5", "R5", "R5"],
            "W5_prev": [4.0, 4.2, np.nan, 3.0],
            "W_pub": [4.1, 4.3, 9.9, 3.5],
            "C_pub": [4.5, 4.7, 9.9, 3.9],
        }
    )
    v = evaluate.new_player_values(p)
    assert v.loc[("M", "R5")].tolist() == pytest.approx([4.2, 4.6])
