"""User accounts: sign in with an email and a password (the email verified once by a link) or
with Google; "me" (a player) and favourites, kept in data/accounts.sqlite. Nothing about a
visitor is stored in the browser but the session cookie.

On only with ACCOUNTS_URL (the site's public address, e.g. https://tennis-radar.ch), and only
for requests to that hostname: the old address served by the same app has no accounts.

    RESEND_API_KEY, MAIL_FROM   the emails (verify the address, reset the password; Resend);
                                without a key the link is printed to the log (local testing)
    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
                                "Sign in with Google" (redirect URI <ACCOUNTS_URL>/api/auth/google/callback)

One account per email: Google and the password with the same address are the same account.
Passwords are stored as scrypt hashes; tokens (sessions, links) as SHA-256: a copy of the
database signs nobody in.
"""

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode, urlparse

import requests

SESSION_DAYS = 365  # renewed on use (see Accounts.renew)
# how long the emailed links work: verify the address, reset the password
LINK_HOURS = {"verify": 24, "reset": 1}
PASSWORD_MIN = 8
MAX_FAVOURITES = 200
COOKIE = "session"
STATE_COOKIE = "oauth_state"
# a visit starts after this long without a request to /api/me ("results since your last visit")
VISIT_GAP_HOURS = 6
GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
RESEND_URL = "https://api.resend.com/emails"
# scrypt: 16 MiB of memory per hash
SCRYPT = {"n": 2**14, "r": 8, "p": 1}

LinkKind = Literal["verify", "reset"]

SCHEMA = """
create table if not exists users (
    id integer primary key,
    email text not null unique,
    google_sub text unique,
    player_id integer,
    created real not null,
    last_seen real,
    prev_seen real,
    password text,  -- scrypt hash, None: none (Google only)
    verified integer not null default 0  -- the email is the user's (link clicked, or Google)
);
create table if not exists sessions (
    token text primary key,  -- sha256 of the cookie value
    user_id integer not null references users (id) on delete cascade,
    expires real not null
);
create table if not exists links (
    token text primary key,  -- sha256 of the emailed token
    user_id integer not null references users (id) on delete cascade,
    kind text not null,  -- verify, reset
    next text not null,
    expires real not null,
    password text  -- verify: the password chosen at sign-up, the account's once verified
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


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **SCRYPT)
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${salt.hex()}${h.hex()}"


def check_password(password: str, stored: str | None) -> bool:
    if not stored:
        hash_password(password)  # the same time whether the account has a password or not
        return False
    _, n, r, p, salt, h = stored.split("$")
    got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
    return hmac.compare_digest(got.hex(), h)


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

    # --- signing up and in ---------------------------------------------------------------------

    def sign_up(self, email: str, password: str, next_: str) -> tuple[int, str | None]:
        """The user of `email` (created if new) and, unless it is verified already, the token of
        a link that verifies it. The password goes with that link: the account gets it only
        when the email's owner clicks (someone else's sign-up with this address cannot set it)."""
        with self.lock, self._connect() as db:
            db.execute("insert or ignore into users (email, created) values (?, ?)", (email, time.time()))
            u = db.execute("select id, verified from users where email = ?", (email,)).fetchone()
            if u["verified"]:
                return u["id"], None
            token = self._link(db, u["id"], "verify", next_, hash_password(password))
            return u["id"], token

    def check_login(self, email: str, password: str) -> tuple[int, bool] | None:
        """(user, verified) when the password is right (for an unverified account: the one of a
        pending sign-up); None otherwise."""
        with self.lock, self._connect() as db:
            u = db.execute("select id, password, verified from users where email = ?", (email,)).fetchone()
            if u is None or u["verified"]:
                return (u["id"], True) if check_password(password, u["password"] if u else None) else None
            pending = db.execute(
                "select password from links where user_id = ? and kind = 'verify' and expires > ?", (u["id"], time.time())
            ).fetchall()
        return (u["id"], False) if any(check_password(password, p["password"]) for p in pending) else None

    def user_by_email(self, email: str) -> int | None:
        rows = self._query("select id from users where email = ?", (email,))
        return rows[0]["id"] if rows else None

    def email_of(self, uid: int) -> str:
        return self._query("select email from users where id = ?", (uid,))[0]["email"]

    def reset_link(self, uid: int) -> str:
        """The token of a link that resets the user's password."""
        with self.lock, self._connect() as db:
            return self._link(db, uid, "reset", "/", None)

    @staticmethod
    def _link(db: sqlite3.Connection, uid: int, kind: LinkKind, next_: str, password: str | None) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        db.execute("delete from links where expires < ?", (now,))
        db.execute(
            "insert into links values (?, ?, ?, ?, ?, ?)",
            (_hash(token), uid, kind, safe_next(next_), now + LINK_HOURS[kind] * 3600, password),
        )
        return token

    def use_link(self, token: str, kind: LinkKind, password: str | None = None) -> tuple[int, str] | None:
        """Use a link (once): the address is verified and the account gets the password (a
        verify link's, or with a reset the new `password`, which also ends every session and the
        other links). The user and the path to return to; None for an unknown, used or expired
        link."""
        with self.lock, self._connect() as db:
            row = db.execute(
                "delete from links where token = ? and kind = ? returning user_id, next, expires, password", (_hash(token), kind)
            ).fetchone()
            if row is None or row["expires"] < time.time():
                return None
            uid = row["user_id"]
            new = hash_password(password) if kind == "reset" else row["password"]
            db.execute("update users set verified = 1, password = ? where id = ?", (new, uid))
            db.execute("delete from links where user_id = ?", (uid,))
            if kind == "reset":
                db.execute("delete from sessions where user_id = ?", (uid,))
            return uid, row["next"]

    def google_user(self, sub: str, email: str) -> int:
        """The user of a Google account: by its id, else by its (verified) email, else new. The
        pending sign-ups of that email are dropped: whoever made them never proved the address
        was theirs."""
        with self.lock, self._connect() as db:
            row = db.execute("select id from users where google_sub = ?", (sub,)).fetchone()
            if row:
                return row["id"]
            db.execute("insert or ignore into users (email, created) values (?, ?)", (email, time.time()))
            db.execute("update users set verified = 1, google_sub = ? where email = ?", (sub, email))
            uid = db.execute("select id from users where email = ?", (email,)).fetchone()["id"]
            db.execute("delete from links where user_id = ? and kind = 'verify'", (uid,))
            return uid

    def set_password(self, uid: int, password: str) -> None:
        self._query("update users set password = ? where id = ?", (hash_password(password), uid))

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
        """The user's email, player, favourites (in the order added), whether they have a
        password, and `since`: when their previous visit ended (None before a second visit)."""
        with self.lock, self._connect() as db:
            u = db.execute("select * from users where id = ?", (uid,)).fetchone()
            favs = [r["player_id"] for r in db.execute("select player_id from favourites where user_id = ? order by added", (uid,))]
        return {
            "email": u["email"], "player": u["player_id"], "favourites": favs, "since": u["prev_seen"],
            "password": u["password"] is not None, "google": u["google_sub"] is not None,
        }

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
        """The account and everything kept with it (sessions, links, favourites)."""
        self._query("delete from users where id = ?", (uid,))


# --- emails (Resend) ---------------------------------------------------------------------------

# kind -> language -> subject, text, button, note
MAILS = {
    "verify": {
        "fr": ("Vérifie ton email pour Tennis Radar", "Bienvenue sur Tennis Radar ! Confirme ton adresse email pour activer ton compte :",
               "Vérifier mon email", "Le lien est valable 24 heures. Si tu n'as pas créé de compte, ignore cet email."),
        "en": ("Verify your email for Tennis Radar", "Welcome to Tennis Radar! Confirm your email address to activate your account:",
               "Verify my email", "The link works for 24 hours. If you did not create an account, ignore this email."),
    },
    "reset": {
        "fr": ("Ton mot de passe Tennis Radar", "Pour choisir un nouveau mot de passe, clique sur ce lien :",
               "Choisir un nouveau mot de passe", "Le lien est valable une heure. Si tu n'as rien demandé, ignore cet email : ton mot de passe ne change pas."),
        "en": ("Your Tennis Radar password", "To choose a new password, click this link:",
               "Choose a new password", "The link works for one hour. If you did not ask for it, ignore this email: your password stays the same."),
    },
    # someone signed up with the address of an existing account
    "exists": {
        "fr": ("Ton compte Tennis Radar", "Tu as déjà un compte Tennis Radar avec cette adresse. Mot de passe oublié ? Choisis-en un nouveau :",
               "Choisir un nouveau mot de passe", "Le lien est valable une heure. Si tu n'as rien demandé, ignore cet email."),
        "en": ("Your Tennis Radar account", "You already have a Tennis Radar account with this address. Forgot your password? Choose a new one:",
               "Choose a new password", "The link works for one hour. If you did not ask for it, ignore this email."),
    },
}


def send_mail(api_key: str | None, sender: str, to: str, kind: str, url: str, lang: str) -> None:
    langs = MAILS[kind]
    subject, intro, button, note = langs.get(lang, langs["fr"])
    if not api_key:  # local testing
        print(f"{kind} link for {to}: {url}", flush=True)
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
