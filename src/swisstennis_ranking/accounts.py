"""User accounts: sign in with an emailed link or with Google, "me" (a player) and favourites,
kept in data/accounts.sqlite. Nothing about a visitor is stored in the browser but the session
cookie.

On only with ACCOUNTS_URL (the site's public address, e.g. https://tennis-radar.ch), and only
for requests to that hostname: the old address served by the same app has no accounts.

    RESEND_API_KEY, MAIL_FROM   the sign-in emails (Resend); without a key the link is printed
                                to the log (local testing)
    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
                                "Sign in with Google" (redirect URI <ACCOUNTS_URL>/api/auth/google/callback)

One account per email: Google and the emailed link with the same address are the same account.
Tokens (sessions, links) are stored hashed: a copy of the database signs nobody in.
"""

import base64
import hashlib
import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse

import requests

SESSION_DAYS = 365  # renewed on use (see Accounts.renew)
LINK_MINUTES = 15
MAX_FAVOURITES = 200
COOKIE = "session"
STATE_COOKIE = "oauth_state"
# a visit starts after this long without a request to /api/me ("results since your last visit")
VISIT_GAP_HOURS = 6
GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
RESEND_URL = "https://api.resend.com/emails"

SCHEMA = """
create table if not exists users (
    id integer primary key,
    email text not null unique,
    google_sub text unique,
    player_id integer,
    created real not null,
    last_seen real,
    prev_seen real
);
create table if not exists sessions (
    token text primary key,  -- sha256 of the cookie value
    user_id integer not null references users (id) on delete cascade,
    expires real not null
);
create table if not exists links (
    token text primary key,  -- sha256 of the emailed token
    email text not null,
    next text not null,
    expires real not null
);
create table if not exists favourites (
    user_id integer not null references users (id) on delete cascade,
    player_id integer not null,
    added real not null,
    primary key (user_id, player_id)
);
"""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def safe_next(path: str | None) -> str:
    """A path on this site to return to after signing in (never another site)."""
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path:
        return "/"
    return path[:300]


class Accounts:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        with self._connect() as db:
            db.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("pragma foreign_keys = on")
        return db

    def _query(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self.lock, self._connect() as db:
            return db.execute(sql, args).fetchall()

    # --- signing in ----------------------------------------------------------------------------

    def new_link(self, email: str, next_: str) -> str:
        """A one-time sign-in token for `email`, valid LINK_MINUTES."""
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self.lock, self._connect() as db:
            db.execute("delete from links where expires < ?", (now,))
            db.execute("insert into links values (?, ?, ?, ?)", (_hash(token), email, safe_next(next_), now + LINK_MINUTES * 60))
        return token

    def use_link(self, token: str) -> tuple[int, str] | None:
        """The user (created on first sign-in) and the path to return to; None for an unknown,
        used or expired link."""
        with self.lock, self._connect() as db:
            row = db.execute("delete from links where token = ? returning email, next, expires", (_hash(token),)).fetchone()
            if row is None or row["expires"] < time.time():
                return None
            return self._user_of(db, row["email"]), row["next"]

    def google_user(self, sub: str, email: str) -> int:
        """The user of a Google account: by its id, else by its (verified) email, else new."""
        with self.lock, self._connect() as db:
            row = db.execute("select id from users where google_sub = ?", (sub,)).fetchone()
            if row:
                return row["id"]
            uid = self._user_of(db, email)
            db.execute("update users set google_sub = ? where id = ?", (sub, uid))
            return uid

    @staticmethod
    def _user_of(db: sqlite3.Connection, email: str) -> int:
        db.execute("insert or ignore into users (email, created) values (?, ?)", (email, time.time()))
        return db.execute("select id from users where email = ?", (email,)).fetchone()["id"]

    def new_session(self, uid: int) -> str:
        token = secrets.token_urlsafe(32)
        with self.lock, self._connect() as db:
            db.execute("delete from sessions where expires < ?", (time.time(),))
            db.execute("insert into sessions values (?, ?, ?)", (_hash(token), uid, time.time() + SESSION_DAYS * 86400))
        return token

    def user_of_session(self, token: str | None) -> int | None:
        if not token:
            return None
        rows = self._query("select user_id from sessions where token = ? and expires > ?", (_hash(token), time.time()))
        return rows[0]["user_id"] if rows else None

    def renew(self, token: str) -> bool:
        """Push the session's end back to SESSION_DAYS from now, at most once a day; True when
        renewed (the cookie is sent again)."""
        full = time.time() + SESSION_DAYS * 86400
        with self.lock, self._connect() as db:
            n = db.execute("update sessions set expires = ? where token = ? and expires < ?", (full, _hash(token), full - 86400)).rowcount
        return n > 0

    def end_session(self, token: str | None) -> None:
        if token:
            self._query("delete from sessions where token = ?", (_hash(token),))

    # --- the account ---------------------------------------------------------------------------

    def visit(self, uid: int) -> None:
        """Count a request of the user: a new visit after VISIT_GAP_HOURS without any."""
        now = time.time()
        with self.lock, self._connect() as db:
            db.execute(
                "update users set prev_seen = case when last_seen is null or ? - last_seen > ? then last_seen else prev_seen end,"
                " last_seen = ? where id = ?",
                (now, VISIT_GAP_HOURS * 3600, now, uid),
            )

    def profile(self, uid: int) -> dict:
        """The user's email, player, favourites (in the order added) and `since`: when their
        previous visit ended (None before a second visit)."""
        with self.lock, self._connect() as db:
            u = db.execute("select * from users where id = ?", (uid,)).fetchone()
            favs = [r["player_id"] for r in db.execute("select player_id from favourites where user_id = ? order by added", (uid,))]
        return {"email": u["email"], "player": u["player_id"], "favourites": favs, "since": u["prev_seen"]}

    def set_player(self, uid: int, pid: int | None) -> None:
        self._query("update users set player_id = ? where id = ?", (pid, uid))

    def add_favourite(self, uid: int, pid: int) -> bool:
        """False when the user already has MAX_FAVOURITES."""
        with self.lock, self._connect() as db:
            n = db.execute("select count(*) from favourites where user_id = ?", (uid,)).fetchone()[0]
            if n >= MAX_FAVOURITES:
                return False
            db.execute("insert or ignore into favourites values (?, ?, ?)", (uid, pid, time.time()))
        return True

    def remove_favourite(self, uid: int, pid: int) -> None:
        self._query("delete from favourites where user_id = ? and player_id = ?", (uid, pid))

    def delete(self, uid: int) -> None:
        """The account, its sessions and favourites (and its pending links)."""
        with self.lock, self._connect() as db:
            email = db.execute("select email from users where id = ?", (uid,)).fetchone()
            if email:
                db.execute("delete from links where email = ?", (email["email"],))
            db.execute("delete from users where id = ?", (uid,))


# --- sign-in email (Resend) ------------------------------------------------------------------

MAIL_TEXT = {
    "fr": ("Ta connexion à Tennis Radar", "Clique sur ce lien pour te connecter à Tennis Radar :", "Se connecter",
           f"Le lien est valable {LINK_MINUTES} minutes et une seule fois. Si tu n'as rien demandé, ignore cet email."),
    "en": ("Sign in to Tennis Radar", "Click this link to sign in to Tennis Radar:", "Sign in",
           f"The link works once, for {LINK_MINUTES} minutes. If you did not ask for it, ignore this email."),
}


def send_link(api_key: str | None, sender: str, to: str, url: str, lang: str) -> None:
    subject, intro, button, note = MAIL_TEXT.get(lang, MAIL_TEXT["fr"])
    if not api_key:  # local testing
        print(f"sign-in link for {to}: {url}", flush=True)
        return
    body = (
        f"<p>{intro}</p><p><a href='{url}' style='display:inline-block;padding:10px 18px;background:#3b6e1f;"
        f"color:#fff;border-radius:6px;text-decoration:none'>{button}</a></p>"
        f"<p style='color:#666;font-size:13px'>{note}</p>"
    )
    r = requests.post(
        RESEND_URL,
        timeout=20,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"from": sender, "to": [to], "subject": subject, "html": body, "text": f"{intro}\n{url}\n\n{note}"},
    )
    if r.status_code >= 300:
        raise RuntimeError(f"Resend failed: {r.status_code} {r.text[:300]}")


# --- Google ------------------------------------------------------------------------------------


def google_redirect_uri(base_url: str) -> str:
    return base_url.rstrip("/") + "/api/auth/google/callback"


def google_auth_url(client_id: str, base_url: str, state: str) -> str:
    return GOOGLE_AUTH + "?" + urlencode({
        "client_id": client_id,
        "redirect_uri": google_redirect_uri(base_url),
        "response_type": "code",
        "scope": "openid email",
        "state": state,
        "prompt": "select_account",
    })


def google_identity(client_id: str, secret: str, base_url: str, code: str) -> tuple[str, str]:
    """(sub, email) of the Google account that signed in, from the code of the callback. The ID
    token comes straight from Google's token endpoint over TLS (authenticated with the client
    secret), so its signature needs no check; its audience and email do."""
    r = requests.post(GOOGLE_TOKEN, timeout=20, data={
        "code": code,
        "client_id": client_id,
        "client_secret": secret,
        "redirect_uri": google_redirect_uri(base_url),
        "grant_type": "authorization_code",
    })
    if r.status_code != 200:
        raise ValueError(f"Google token exchange failed: {r.status_code}")
    payload = r.json()["id_token"].split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    if claims.get("aud") != client_id or claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise ValueError("ID token not meant for this app")
    if not claims.get("email") or not claims.get("email_verified"):
        raise ValueError("Google account without a verified email")
    return claims["sub"], claims["email"].lower()


def hostname(url: str) -> str | None:
    return urlparse(url).hostname
