import numpy as np
import pandas as pd
import pytest

from swisstennis_ranking import ranking, stats, web


def test_first_and_last_list_of_a_result():
    # a result of 31 March counts on the 1 April list; one of 1 April only from 1 October
    assert web.first_list(pd.Timestamp("2026-03-31 18:00")) == pd.Timestamp("2026-04-01")
    assert web.first_list(pd.Timestamp("2026-04-01 10:00")) == pd.Timestamp("2026-10-01")


def test_match_deltas_are_values_with_minus_without():
    opp_w, win = np.array([8.0, 7.0, 9.0]), np.array([True, False, True])
    dW, dC = web.match_deltas(7.5, opp_w, win)
    W, R = ranking.single_pass(np.array([7.5]), np.zeros(2, int), opp_w[[1, 2]], win[[1, 2]])
    W_all, R_all = ranking.single_pass(np.array([7.5]), np.zeros(3, int), opp_w, win)
    assert dW[0] == pytest.approx(W_all[0] - W[0])
    assert dC[0] == pytest.approx(W_all[0] + R_all[0] - W[0] - R[0])
    assert dW[0] > 0 and dW[1] < 0


def _results(scores, codes):
    rows = []
    for (s1, s2, s3), code in zip(scores, codes):
        r = {"personId": 1, "date": pd.Timestamp("2026-05-01"), "tournamentName": "T", "adversaryPersonId": 2,
             "adversaryFirstname": "A", "adversaryLastname": "B", "playerWinnerCode": code, "source": 410,
             "adversaryValue": 5.0, "encounterId": np.nan}
        for i, s in enumerate((s1, s2, s3), 1):
            r[f"playerSet{i}WonGames"], r[f"adversarySet{i}WonGames"] = s
        rows.append(r)
    m = pd.DataFrame(rows)
    return m.assign(adversaryPersonId=m["adversaryPersonId"].astype("Int64"))


def test_player_stats_sets_tiebreaks_and_walkovers():
    m = _results(
        [((7, 6), (3, 6), (10, 8)), ((6, 0), (6, 7), (4, 6)), ((-1, -1), (-1, -1), (-1, -1)), ((6, 4), (7, 5), (-1, -1))],
        ["S", "N", "1", "S"],
    )
    history = pd.DataFrame({"personId": [1, 2], "date": pd.to_datetime(["2026-04-01"] * 2),
                            "classification": ["R3", "R2"], "W": [6.5, 7.5]})
    d = stats.player_stats(stats.with_categories(stats.describe(m), history, 1))
    assert d["matches"] == {"played": 4, "won": 3, "lost": 1, "walkovers_won": 1, "walkovers_lost": 0,
                            "retired_won": 0, "retired_lost": 0}
    assert d["match_tiebreak"] == {"won": 1, "lost": 0} and d["deciding_set"] == {"won": 0, "lost": 1}
    assert d["tiebreaks"] == {"won": 1, "lost": 1} and d["sets_7_5"] == {"won": 1, "lost": 0}
    assert d["games"] == {"won": 7 + 3 + 6 + 6 + 4 + 6 + 7, "lost": 6 + 6 + 0 + 7 + 6 + 4 + 5}  # no match tiebreak
    assert d["after_first_set"]["lost_first"] == {"played": 0, "won": 0}
    assert d["by_relation"] == [{"relation": "higher", "won": 3, "lost": 1}]
    assert d["streak"]["longest_win"] == 2 and d["streak"]["current_type"] == "win"


def test_analytics_config_needs_script_and_website(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    client = TestClient(web.create_app(tmp_path, None, None))  # no lifespan: no data loaded
    monkeypatch.setenv("UMAMI_SCRIPT_URL", "https://stats.example.com/radar.js")
    assert client.get("/api/config").json() == {"analytics": None}
    monkeypatch.setenv("UMAMI_WEBSITE_ID", "abc")
    assert client.get("/api/config").json() == {
        "analytics": {"script": "https://stats.example.com/radar.js", "website_id": "abc"}
    }
