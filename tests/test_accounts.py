from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from swisstennis_ranking import accounts, web

SITE = "http://testserver"  # the TestClient's address
SAME = {"Origin": SITE}


@pytest.fixture
def links(monkeypatch):
    sent = []
    monkeypatch.setattr(accounts, "send_link", lambda key, sender, to, url, lang: sent.append((to, url, lang)))
    return sent


@pytest.fixture
def client(tmp_path, monkeypatch, links):
    monkeypatch.setenv("ACCOUNTS_URL", SITE)
    api = web.create_app(tmp_path, None, None)
    app = api.state.app
    p = pd.DataFrame({"personId": [1, 2], "class": ["R3", "R7"], "on_list": True, "C": [6.5, 3.5], "W": [6.4, 3.4], "rank": [10, 900]})
    app.state = SimpleNamespace(
        p=p, number=pd.Series(p.index, index=p["personId"]),
        people=pd.DataFrame({"name": ["Ann", "Ben"], "gender": ["F", "M"], "age": ["30+", "14&U"]}, index=pd.Index([1, 2], name="personId")),
        official_class=pd.Series(["R4", "R7"], index=[1, 2]),
    )  # fmt: skip
    return TestClient(api)


def sign_in(client, links, email="Leo@Example.ch", next_="/clubs"):
    assert client.post("/api/auth/email", json={"email": email, "next": next_, "lang": "en"}, headers=SAME).status_code == 204
    to, url, lang = links[-1]
    assert (to, lang) == (email.lower(), "en") and url.startswith(f"{SITE}/login?token=")
    token = url.split("token=")[1]
    r = client.post("/api/auth/verify", json={"token": token}, headers=SAME)
    assert r.status_code == 200 and r.json() == {"next": next_}
    return token


def test_off_without_accounts_url_or_on_another_host(tmp_path, monkeypatch):
    assert TestClient(web.create_app(tmp_path, None, None)).get("/api/me").status_code == 404
    assert TestClient(web.create_app(tmp_path, None, None)).get("/api/config").json()["accounts"] is None
    monkeypatch.setenv("ACCOUNTS_URL", "https://tennis-radar.ch")
    other = TestClient(web.create_app(tmp_path, None, None))  # requests to testserver, the old address
    assert other.get("/api/me").status_code == 404
    assert other.post("/api/auth/email", json={"email": "a@b.ch"}, headers={"Origin": "https://tennis-radar.ch"}).status_code == 404


def test_email_link_signs_in_once(client, links):
    assert client.get("/api/config").json()["accounts"] == {"google": False}
    assert client.get("/api/me").json() == {"user": None}
    token = sign_in(client, links)
    assert client.cookies.get(accounts.COOKIE)
    assert client.get("/api/me").json() == {"user": {"email": "leo@example.ch", "me": None, "favourites": []}}
    # used up
    assert client.post("/api/auth/verify", json={"token": token}, headers=SAME).status_code == 400
    # signing in again (Google or a new link) with the same address: the same account
    client.put("/api/me/favourites/2", headers=SAME)
    sign_in(client, links, "leo@example.ch")
    assert client.get("/api/me").json()["user"]["favourites"] == [2]


def test_changes_need_the_sites_origin(client, links):
    assert client.post("/api/auth/email", json={"email": "a@b.ch"}).status_code == 403
    assert client.post("/api/auth/email", json={"email": "a@b.ch"}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/auth/email", json={"email": "not an email"}, headers=SAME).status_code == 400
    sign_in(client, links)
    assert client.put("/api/me", json={"player": 1}).status_code == 403
    assert not links[1:]


def test_me_favourites_logout_and_delete(client, links, tmp_path):
    assert client.put("/api/me", json={"player": 1}, headers=SAME).status_code == 401
    sign_in(client, links)
    assert client.put("/api/me", json={"player": 99}, headers=SAME).status_code == 404
    me = client.put("/api/me", json={"player": 2}, headers=SAME).json()["me"]
    # what the filters start from: today's class R7 (R4-R9), the junior age U14
    assert me == {"id": 2, "name": "Ben", "gender": "M", "age": "14&U", "class": "R7", "group": "R4-R9", "juniorAge": "U14"}
    assert client.put("/api/me/favourites/1", headers=SAME).status_code == 204
    assert client.put("/api/me/favourites/1", headers=SAME).status_code == 204  # twice: once
    assert client.put("/api/me/favourites/99", headers=SAME).status_code == 404
    assert client.get("/api/me").json()["user"]["favourites"] == [1]
    assert client.delete("/api/me/favourites/1", headers=SAME).status_code == 204
    assert client.get("/api/me").json()["user"] == {"email": "leo@example.ch", "me": me, "favourites": []}

    cookie = client.cookies.get(accounts.COOKIE)
    assert client.post("/api/auth/logout", headers=SAME).status_code == 204
    client.cookies.set(accounts.COOKIE, cookie)  # the old cookie no longer works
    assert client.get("/api/me").json() == {"user": None}
    client.cookies.clear()

    sign_in(client, links)
    assert client.delete("/api/me", headers=SAME).status_code == 204
    sign_in(client, links)  # a new, empty account
    assert client.get("/api/me").json()["user"]["me"] is None


def test_google_callback(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setattr(accounts, "google_identity", lambda cid, secret, base, code: ("sub-1", "leo@example.ch"))
    r = client.get("/api/auth/google?next=/tournaments", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith(accounts.GOOGLE_AUTH)
    state = r.headers["location"].split("state=")[1].split("&")[0]
    # a wrong state (another browser's sign-in) is refused
    r = client.get("/api/auth/google/callback?code=x&state=forged", follow_redirects=False)
    assert r.headers["location"] == "/login?error=google"
    r = client.get("/api/auth/google", follow_redirects=False)  # a fresh state cookie
    state = r.headers["location"].split("state=")[1].split("&")[0]
    r = client.get(f"/api/auth/google/callback?code=x&state={state}", follow_redirects=False)
    assert r.headers["location"] == "/"
    assert client.get("/api/me").json()["user"]["email"] == "leo@example.ch"


def test_safe_next():
    assert accounts.safe_next("/player/1?x=2") == "/player/1?x=2"
    for bad in (None, "", "https://evil.example", "//evil.example", "/\\evil.example"):
        assert accounts.safe_next(bad) == "/"
