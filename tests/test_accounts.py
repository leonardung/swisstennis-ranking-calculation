from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from swisstennis_ranking import accounts, web

SITE = "http://testserver"  # the TestClient's address
SAME = {"Origin": SITE}
PW = "correct horse"


@pytest.fixture
def mails(monkeypatch):
    sent = []
    monkeypatch.setattr(accounts, "send_mail", lambda key, sender, to, kind, url, lang: sent.append((to, kind, url, lang)))
    return sent


@pytest.fixture
def client(tmp_path, monkeypatch, mails):
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


def token_of(mail) -> str:
    return mail[2].split("token=")[1]


def sign_up(client, mails, email="Leo@Example.ch", password=PW, next_="/clubs"):
    """Sign up and click the verification link: signed in."""
    r = client.post("/api/auth/signup", json={"email": email, "password": password, "next": next_, "lang": "en"}, headers=SAME)
    assert r.status_code == 204
    to, kind, url, lang = mails[-1]
    assert (to, kind, lang) == (email.lower(), "verify", "en") and url.startswith(f"{SITE}/login?token=")
    r = client.post("/api/auth/verify", json={"token": token_of(mails[-1])}, headers=SAME)
    assert r.status_code == 200 and r.json() == {"next": next_}


def log_in(client, email="leo@example.ch", password=PW):
    return client.post("/api/auth/login", json={"email": email, "password": password}, headers=SAME)


def test_off_without_accounts_url_or_on_another_host(tmp_path, monkeypatch):
    assert TestClient(web.create_app(tmp_path, None, None)).get("/api/me").status_code == 404
    assert TestClient(web.create_app(tmp_path, None, None)).get("/api/config").json()["accounts"] is None
    monkeypatch.setenv("ACCOUNTS_URL", "https://tennis-radar.ch")
    other = TestClient(web.create_app(tmp_path, None, None))  # requests to testserver, the old address
    assert other.get("/api/me").status_code == 404
    assert other.post("/api/auth/login", json={"email": "a@b.ch", "password": PW}, headers={"Origin": "https://tennis-radar.ch"}).status_code == 404


def test_sign_up_verify_then_password(client, mails):
    assert client.get("/api/config").json()["accounts"] == {"google": False}
    assert client.get("/api/me").json() == {"user": None}
    r = client.post("/api/auth/signup", json={"email": "leo@example.ch", "password": PW}, headers=SAME)
    assert r.status_code == 204
    # not verified yet: the password does not sign in, the verification email comes again
    assert log_in(client).status_code == 403
    assert [m[1] for m in mails] == ["verify", "verify"]
    assert log_in(client, password="wrong password").status_code == 401
    assert client.post("/api/auth/verify", json={"token": token_of(mails[0])}, headers=SAME).status_code == 200
    assert client.get("/api/me").json() == {"user": {
        "email": "leo@example.ch", "me": None, "favourites": [], "hasPassword": True, "google": False,
    }}
    # every link is used up by the verification
    assert client.post("/api/auth/verify", json={"token": token_of(mails[1])}, headers=SAME).status_code == 400
    client.cookies.clear()
    assert log_in(client, "LEO@example.ch").status_code == 200
    assert client.get("/api/me").json()["user"]["email"] == "leo@example.ch"
    assert log_in(client, password="wrong password").status_code == 401
    assert log_in(client, "nobody@example.ch").status_code == 401


def test_short_password_and_bad_email(client, mails):
    assert client.post("/api/auth/signup", json={"email": "a@b.ch", "password": "short"}, headers=SAME).status_code == 422
    assert client.post("/api/auth/signup", json={"email": "not an email", "password": PW}, headers=SAME).status_code == 400
    assert not mails


def test_someone_elses_sign_up_cannot_set_the_password(client, mails):
    # an attacker signs up with the victim's address, then the victim does
    client.post("/api/auth/signup", json={"email": "v@example.ch", "password": "attacker's pw"}, headers=SAME)
    client.post("/api/auth/signup", json={"email": "v@example.ch", "password": "victim's pw"}, headers=SAME)
    # the victim clicks the link of their own sign-up: their password, the attacker's is gone
    client.post("/api/auth/verify", json={"token": token_of(mails[1])}, headers=SAME)
    client.cookies.clear()
    assert log_in(client, "v@example.ch", "attacker's pw").status_code == 401
    assert log_in(client, "v@example.ch", "victim's pw").status_code == 200
    # a sign-up with a verified address: an email to the owner (reset link), the account unchanged
    client.cookies.clear()
    client.post("/api/auth/signup", json={"email": "v@example.ch", "password": "attacker's pw"}, headers=SAME)
    assert mails[-1][1] == "exists" and "/reset?token=" in mails[-1][2]
    assert log_in(client, "v@example.ch", "attacker's pw").status_code == 401


def test_forgot_password(client, mails):
    sign_up(client, mails)
    client.cookies.clear()
    assert client.post("/api/auth/forgot", json={"email": "nobody@example.ch"}, headers=SAME).status_code == 204
    assert len(mails) == 1  # nothing sent, the same answer
    assert client.post("/api/auth/forgot", json={"email": "Leo@example.ch", "lang": "en"}, headers=SAME).status_code == 204
    assert mails[-1][1] == "reset" and mails[-1][2].startswith(f"{SITE}/reset?token=")
    other = TestClient(client.app)  # another browser, signed in with the old password
    assert log_in(other).status_code == 200
    r = client.post("/api/auth/reset", json={"token": token_of(mails[-1]), "password": "new password"}, headers=SAME)
    assert r.status_code == 200
    assert client.get("/api/me").json()["user"]["email"] == "leo@example.ch"
    assert other.get("/api/me").json() == {"user": None}  # signed out everywhere else
    assert log_in(other).status_code == 401
    assert log_in(other, password="new password").status_code == 200
    assert client.post("/api/auth/reset", json={"token": token_of(mails[-1]), "password": "again pw"}, headers=SAME).status_code == 400


def test_changes_need_the_sites_origin(client, mails):
    assert client.post("/api/auth/signup", json={"email": "a@b.ch", "password": PW}).status_code == 403
    assert client.post("/api/auth/signup", json={"email": "a@b.ch", "password": PW}, headers={"Origin": "https://evil.example"}).status_code == 403
    sign_up(client, mails)
    assert client.put("/api/me", json={"player": 1}).status_code == 403
    assert client.put("/api/me/password", json={"password": "other password"}).status_code == 403
    assert len(mails) == 1


def test_me_favourites_password_logout_and_delete(client, mails):
    assert client.put("/api/me", json={"player": 1}, headers=SAME).status_code == 401
    sign_up(client, mails)
    assert client.put("/api/me", json={"player": 99}, headers=SAME).status_code == 404
    me = client.put("/api/me", json={"player": 2}, headers=SAME).json()["me"]
    # what the filters start from: today's class R7 (R4-R9), the junior age U14
    assert me == {
        "id": 2, "name": "Ben", "gender": "M", "age": "14&U", "class": "R7", "group": "R4-R9", "juniorAge": "U14",
        "officialClass": "R7", "openTo": {"matchType": "1,3,5", "age": None, "event": "R7"},  # no tournaments here
    }  # fmt: skip
    assert client.put("/api/me/favourites/1", headers=SAME).status_code == 204
    assert client.put("/api/me/favourites/1", headers=SAME).status_code == 204  # twice: once
    assert client.put("/api/me/favourites/99", headers=SAME).status_code == 404
    assert client.get("/api/me").json()["user"]["favourites"] == [1]
    assert client.delete("/api/me/favourites/1", headers=SAME).status_code == 204
    assert client.get("/api/me").json()["user"]["favourites"] == []
    assert client.get("/api/me").json()["user"]["me"] == me

    assert client.put("/api/me/password", json={"password": "changed pw"}, headers=SAME).status_code == 204
    cookie = client.cookies.get(accounts.COOKIE)
    assert client.post("/api/auth/logout", headers=SAME).status_code == 204
    client.cookies.set(accounts.COOKIE, cookie)  # the old cookie no longer works
    assert client.get("/api/me").json() == {"user": None}
    client.cookies.clear()
    assert log_in(client).status_code == 401
    assert log_in(client, password="changed pw").status_code == 200

    assert client.delete("/api/me", headers=SAME).status_code == 204
    assert log_in(client, password="changed pw").status_code == 401
    sign_up(client, mails)  # a new, empty account
    assert client.get("/api/me").json()["user"]["me"] is None


def test_google_callback(client, mails, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setattr(accounts, "google_identity", lambda cid, secret, base, code: ("sub-1", "leo@example.ch"))
    # a pending sign-up of the address (not verified): Google drops it
    client.post("/api/auth/signup", json={"email": "leo@example.ch", "password": "someone's pw"}, headers=SAME)
    r = client.get("/api/auth/google?next=/tournaments", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith(accounts.GOOGLE_AUTH)
    # a wrong state (another browser's sign-in) is refused
    r = client.get("/api/auth/google/callback?code=x&state=forged", follow_redirects=False)
    assert r.headers["location"] == "/login?error=google"
    r = client.get("/api/auth/google?next=/tournaments", follow_redirects=False)  # a fresh state cookie
    state = r.headers["location"].split("state=")[1].split("&")[0]
    r = client.get(f"/api/auth/google/callback?code=x&state={state}", follow_redirects=False)
    assert r.headers["location"] == "/tournaments"
    user = client.get("/api/me").json()["user"]
    assert (user["email"], user["hasPassword"], user["google"]) == ("leo@example.ch", False, True)
    assert client.post("/api/auth/verify", json={"token": token_of(mails[0])}, headers=SAME).status_code == 400
    client.cookies.clear()
    assert log_in(client, password="someone's pw").status_code == 401


def test_passwords_are_hashed():
    h = accounts.hash_password(PW)
    assert PW not in h and h.startswith("scrypt$")
    assert accounts.check_password(PW, h) and not accounts.check_password("other", h)
    assert not accounts.check_password(PW, None)


def test_safe_next():
    assert accounts.safe_next("/player/1?x=2") == "/player/1?x=2"
    for bad in (None, "", "https://evil.example", "//evil.example", "/\\evil.example"):
        assert accounts.safe_next(bad) == "/"
