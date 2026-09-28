import numpy as np
import pandas as pd
import xgboost as xgb

from swisstennis_ranking import winprob


def _res(rows):
    """rows: (personId, opp, date, win); both sides of each match."""
    out = []
    for a, b, date, win in rows:
        out += [(a, b, pd.Timestamp(date), 4, int(win)), (b, a, pd.Timestamp(date), 4, int(not win))]
    return pd.DataFrame(out, columns=["personId", "opp", "date", "type", "win"])


HISTORY = pd.DataFrame({
    "personId": [1, 1, 2],
    "date": pd.to_datetime(["2026-04-01", "2026-10-01", "2026-04-01"]),
    "classification": ["R3", "R2", "R5"],
    "rank": [10.0, 5.0, 50.0],
    "W": [7.0, 8.0, 5.0],
    "C": [7.5, 8.5, 5.5],
})
BORN = winprob.people(pd.DataFrame({"personId": [1, 2], "licenceNumber": ["903.96.276.0", "100.08.319.0"], "gender": ["M", "M"]}))


def test_features_use_only_what_is_known_before_the_match():
    res = _res([
        (1, 2, "2026-01-10", True),
        (1, 2, "2026-06-01", False),
        (1, 3, "2026-06-10", True),  # the match itself: not counted
        (1, 3, "2026-06-10", True),  # same day: not counted either
    ])
    q = pd.DataFrame({"personId": [1], "opp": [2], "date": [pd.Timestamp("2026-06-10")], "type": [4]})
    f = winprob.features(q, res, HISTORY, BORN).iloc[0]
    # the list in force (April), not the October one
    assert (f["a_W"], f["b_W"], f["dW"], f["dcls"]) == (7.0, 5.0, 2.0, -2.0)
    assert (f["a_career_n"], f["a_w90"], f["a_n90"], f["a_w365"], f["a_n365"]) == (2, 0, 1, 1, 2)
    assert f["a_days_since"] == 9 and (f["h2h_w"], f["h2h_l"]) == (1, 1)
    # licence 903.96.276.0: born 1996; 100.08.319.0: 2008
    assert (f["a_age"], f["b_age"], f["female"]) == (30, 18, 0)


def test_folds_are_the_same_from_both_sides():
    q = pd.DataFrame({"personId": [1, 2, 5, 9], "opp": [2, 1, 9, 5], "date": pd.to_datetime(["2026-01-01"] * 2 + ["2026-02-01"] * 2)})
    f = winprob.fold(q)
    assert f[0] == f[1] and f[2] == f[3]


def test_chances_of_both_players_add_up_to_one():
    rng = np.random.default_rng(0)
    X = pd.DataFrame(rng.normal(size=(300, len(winprob.FEATURES))), columns=winprob.FEATURES).astype("float32")
    y = (X["dW"] + rng.normal(size=300) > 0).astype(int)
    model = winprob.Model.__new__(winprob.Model)
    model.boosters = [xgb.train({"objective": "binary:logistic"}, xgb.DMatrix(X, y), 5) for _ in range(2)]
    res = _res([(1, 2, "2026-01-10", True)])
    q = pd.DataFrame({"personId": [1], "opp": [2], "date": [pd.Timestamp("2026-06-10")], "type": [4]})
    for played in (True, False):
        a = model.chance(q, res, HISTORY, BORN, played)
        b = model.chance(q.rename(columns={"personId": "opp", "opp": "personId"}), res, HISTORY, BORN, played)
        assert 0 < a[0] < 1 and abs(a[0] + b[0] - 1) < 1e-6
