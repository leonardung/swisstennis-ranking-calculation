import json

import numpy as np
import pandas as pd
import pytest

from swisstennis_ranking import evaluate, ranking, stats, web


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
                            "classification": ["R3", "R2"], "rank": [300, 150], "W": [6.5, 7.5]})
    d = stats.player_stats(stats.with_categories(stats.describe(m), history, 1))
    assert d["matches"] == {"played": 4, "won": 3, "lost": 1, "walkovers_won": 1, "walkovers_lost": 0,
                            "retired_won": 0, "retired_lost": 0}
    assert d["match_tiebreak"] == {"won": 1, "lost": 0} and d["deciding_set"] == {"won": 0, "lost": 1}
    assert d["tiebreaks"] == {"won": 1, "lost": 1} and d["sets_7_5"] == {"won": 1, "lost": 0}
    assert d["games"] == {"won": 7 + 3 + 6 + 6 + 4 + 6 + 7, "lost": 6 + 6 + 0 + 7 + 6 + 4 + 5}  # no match tiebreak
    assert d["after_first_set"]["lost_first"] == {"played": 0, "won": 0}
    assert d["by_relation"] == [{"relation": "higher", "won": 3, "lost": 1}]
    assert d["streak"]["longest_win"] == 2 and d["streak"]["current_type"] == "win"


def test_player_stats_without_tournament_name_is_valid_json():
    # foreign results often come without a tournament name
    m = _results([((6, 4), (6, 4), (-1, -1))], ["S"]).assign(tournamentName=np.nan)
    history = pd.DataFrame({"personId": [1, 2], "date": pd.to_datetime(["2026-04-01"] * 2),
                            "classification": ["R3", "R2"], "rank": [300, 150], "W": [6.5, 7.5]})
    d = stats.player_stats(stats.with_categories(stats.describe(m), history, 1))
    json.dumps(d, allow_nan=False)


def test_analytics_config_needs_script_and_website(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    client = TestClient(web.create_app(tmp_path, None, None))  # no lifespan: no data loaded
    monkeypatch.setenv("UMAMI_SCRIPT_URL", "https://stats.example.com/radar.js")
    assert client.get("/api/config").json() == {"analytics": None}
    monkeypatch.setenv("UMAMI_WEBSITE_ID", "abc")
    assert client.get("/api/config").json() == {
        "analytics": {"script": "https://stats.example.com/radar.js", "website_id": "abc"}
    }


def test_ui_paths_get_index_but_api_paths_404(tmp_path):
    from fastapi.testclient import TestClient

    (tmp_path / "index.html").write_text("<html>ui</html>")
    (tmp_path / "app.js").write_text("js")
    client = TestClient(web.create_app(tmp_path, tmp_path, None))
    assert client.get("/player/134639/stats").text == "<html>ui</html>"
    assert client.get("/app.js").text == "js"
    assert client.get("/api/nothing").status_code == 404


def test_feedback_is_stored_rate_limited_and_drops_bots(tmp_path):
    import json

    from fastapi.testclient import TestClient

    client = TestClient(web.create_app(tmp_path, None, None))
    msg = {"message": "Great app", "name": "Leo", "page": "/player/1"}
    assert client.post("/api/feedback", json={**msg, "website": "spam.example"}).status_code == 204
    assert not (tmp_path / "feedback.jsonl").exists()
    for _ in range(web.FEEDBACK_PER_HOUR):
        assert client.post("/api/feedback", json=msg).status_code == 204
    assert client.post("/api/feedback", json=msg).status_code == 429
    assert client.post("/api/feedback", json={"message": ""}).status_code == 422
    lines = (tmp_path / "feedback.jsonl").read_text().splitlines()
    assert len(lines) == web.FEEDBACK_PER_HOUR
    assert json.loads(lines[0]) | {"time": None} == {**msg, "email": None, "time": None}


def test_matches_of_a_statistic():
    m = _results(
        [((7, 6), (3, 6), (10, 8)), ((6, 0), (6, 7), (4, 6)), ((-1, -1), (-1, -1), (-1, -1)), ((6, 4), (7, 5), (-1, -1))],
        ["S", "N", "1", "S"],
    ).assign(date=pd.to_datetime(["2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04"]))
    history = pd.DataFrame({"personId": [1, 2], "date": pd.to_datetime(["2026-04-01"] * 2),
                            "classification": ["R3", "R2"], "rank": [300, 150], "W": [6.5, 7.5]})
    m = stats.with_categories(stats.describe(m), history, 1)
    dates = lambda key, value=None: [r["date"] for r in stats.match_list(stats.matches_of(m, key, value))]  # noqa: E731
    assert dates("tiebreaks") == ["2026-05-02", "2026-05-01"]
    assert dates("bagels") == ["2026-05-02"] and dates("walkovers") == ["2026-05-03"]
    assert dates("streak", "win") == ["2026-05-04", "2026-05-03"] and dates("streak", "loss") == ["2026-05-02"]
    assert dates("by_relation", "higher") == dates("matches")
    with pytest.raises(KeyError):
        stats.matches_of(m, "nothing")


def test_career_and_ranks_at_the_time():
    m = _results(
        [((6, 4), (6, 4), (-1, -1)), ((6, 0), (6, 7), (4, 6)), ((6, 1), (6, 1), (-1, -1)), ((-1, -1), (-1, -1), (-1, -1))],
        ["S", "N", "S", "1"],
    ).assign(date=pd.to_datetime(["2025-05-01", "2025-06-01", "2026-05-01", "2026-06-01"]))
    history = pd.DataFrame({"personId": [1, 2, 1, 2], "date": pd.to_datetime(["2025-04-01"] * 2 + ["2026-04-01"] * 2),
                            "classification": ["R5", "R4", "R3", "R2"], "rank": [900, 700, 300, 150],
                            "W": [4.5, 5.5, 6.5, 7.5]})
    m = stats.with_categories(stats.describe(m), history, 1)
    c = stats.career(m)
    assert (c["matches"], c["won"], c["lost"], c["first_year"], c["last_year"], c["seasons"]) == (4, 3, 1, 2025, 2026, 2)
    # the walkover does not count as a win
    assert [(w["date"], w["opponent"]["rank"]) for w in c["best_wins"]] == [("2026-05-01", 150), ("2025-05-01", 700)]
    assert [(w["opponent"]["class"], w["date"]) for w in c["first_wins"]] == [("R2", "2026-05-01"), ("R4", "2025-05-01")]
    first = stats.match_list(m)[-1]
    assert first["own"] == {"class": "R5", "rank": 900} and first["opponent"]["rank"] == 700


def _league_app(tmp_path):
    """An App on a small league: men 1-4 with results against each other (both sides stored),
    woman 5; today's list is the monthly list of 2025-06-01."""
    ids = [1, 2, 3, 4, 5]
    history = pd.DataFrame({
        "personId": ids, "date": pd.to_datetime(["2025-04-01"] * 5), "firstname": list("abcde"),
        "lastname": list("vwxyz"), "classification": ["R3", "R4", "R5", "R6", "R4"], "rank": [1, 2, 3, 4, 1],
        "W": [6.0, 5.0, 4.0, 3.0, 5.0], "C": [6.0, 5.0, 4.0, 3.0, 5.0], "games": [0] * 5,
    })
    games = [(1, 2, "2025-01-10"), (2, 3, "2025-02-10"), (3, 1, "2025-03-10"), (1, 4, "2025-05-01")]
    rows = []
    for winner, loser, day in games:
        for pid, opp, code in ((winner, loser, "S"), (loser, winner, "N")):
            rows.append({"personId": pid, "date": pd.Timestamp(day), "adversaryPersonId": opp, "playerWinnerCode": code,
                         "matchNotConsidered": False, "encounterId": np.nan, "adversaryValue": 0.0})
    matches = pd.DataFrame(rows).assign(adversaryPersonId=lambda m: m["adversaryPersonId"].astype("Int64"))
    players = pd.DataFrame({"personId": ids, "gender": ["M"] * 4 + ["F"]})
    today = pd.Timestamp("2025-06-01")
    period = evaluate.build_period(history, matches, players, today)
    p, calc = evaluate.evaluate_detailed(period, predict=True)
    people = pd.DataFrame({"name": list("ABCDE"), "gender": players["gender"].to_numpy()}, index=pd.Index(ids, name="personId"))
    app = web.App(tmp_path, None)
    app.state = web.State(
        today=today, updated=None, history=history, matches=matches, match_rows=None, period=period, p=p, rows=calc,
        player_rows=calc.groupby("player").indices, number=pd.Series(p.index, index=p["personId"]), people=people,
        official_date=today, official_class=pd.Series(dtype=object), highlights={}, chance=lambda q: None,
        match_chance=None,
    )
    return app, matches


def test_predict_removes_played_matches_and_adds_matches_against_players(tmp_path):
    from fastapi import HTTPException

    app, matches = _league_app(tmp_path)
    before = app.predict(1, add="win:4")["before"]
    # 1 lost to 3: without that loss (and 3's win) player 1's C goes up
    loss = matches.index[(matches["personId"] == 1) & (matches["adversaryPersonId"] == 3)][0]
    out = app.predict(1, remove=str(loss))
    assert out["removed"] == 1 and out["after"]["C"] > before["C"]
    # a win against a given player: the same full calculation as the simulator
    out = app.predict(1, add="win:2")
    assert out["after"] == app.simulate(1, 2)["player"]["win"] and out["after"]["C"] > before["C"]
    both = app.predict(1, remove=str(loss), add="win:2,loss:4")
    assert both["removed"] == 1 and both["added"] == 2
    for kw in ({"add": "win:5"}, {"add": "win:99"}, {"add": "win:1"}, {"add": "draw:2"}, {"remove": str(loss - 1)},  # the opponent's side
               {"remove": "x"}, {}, {"add": ",".join(["win:2"] * (web.PREDICT_MAX_ADD + 1))}):
        with pytest.raises(HTTPException) as e:
            app.predict(1, **kw)
        assert e.value.status_code == 400, kw
