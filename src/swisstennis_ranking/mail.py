"""Send email from a personal Outlook.com / Hotmail mailbox through Microsoft Graph.

Microsoft no longer accepts passwords (even app passwords) for SMTP on personal accounts, so this
uses OAuth: an app registration (MS_CLIENT_ID) and a one-time device-code login,

    swisstennis-ranking mail-login     open the link, enter the code, sign in, accept "send mail"
    swisstennis-ranking mail-test      send a short test email to FEEDBACK_MAIL_TO

The login is kept in data/auth/msmail-cache.json (a refresh token). Every send renews it; it
expires only after about 90 days without any send, then run `mail-login` again.
"""

import re
import threading
from pathlib import Path

import msal
import requests

AUTHORITY = "https://login.microsoftonline.com/consumers"  # personal Microsoft accounts
SCOPES = ["Mail.Send"]  # msal adds offline_access (refresh token) itself
_lock = threading.Lock()  # the token cache file is read and rewritten on each send
EMAIL = re.compile(r"[^@\s<>\"]+@[^@\s<>\"]+\.[^@\s<>\"]+")


def cache_path(data_dir: Path) -> Path:
    return data_dir / "auth" / "msmail-cache.json"


def _app(client_id: str, cache_file: Path) -> tuple[msal.PublicClientApplication, msal.SerializableTokenCache]:
    cache = msal.SerializableTokenCache()
    if cache_file.exists():
        cache.deserialize(cache_file.read_text())
    return msal.PublicClientApplication(client_id, authority=AUTHORITY, token_cache=cache), cache


def _save(cache: msal.SerializableTokenCache, cache_file: Path) -> None:
    if cache.has_state_changed:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.touch(mode=0o600)
        cache_file.write_text(cache.serialize())


def login(client_id: str, cache_file: Path) -> str:
    """Device-code sign-in (prints the link and code, waits); the account's address."""
    app, cache = _app(client_id, cache_file)
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise SystemExit(f"Could not start the login: {flow.get('error_description', flow)}")
    print(flow["message"], flush=True)
    result = app.acquire_token_by_device_flow(flow)
    _save(cache, cache_file)
    if "access_token" not in result:
        raise SystemExit(f"Login failed: {result.get('error_description', result)}")
    return result.get("id_token_claims", {}).get("preferred_username", "?")


def _token(client_id: str, cache_file: Path) -> str:
    app, cache = _app(client_id, cache_file)
    accounts = app.get_accounts()
    result = app.acquire_token_silent(SCOPES, account=accounts[0]) if accounts else None
    _save(cache, cache_file)
    if not result or "access_token" not in result:
        raise RuntimeError("Microsoft login missing or expired: run `swisstennis-ranking mail-login`")
    return result["access_token"]


def send(client_id: str, cache_file: Path, to: str, subject: str, body_html: str, reply_to: str | None = None) -> None:
    """`to`: one address or several separated by commas; `reply_to` is used only when it looks
    like an address."""
    message = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": body_html},
        "toRecipients": [{"emailAddress": {"address": a.strip()}} for a in to.split(",") if a.strip()],
    }
    if reply_to and EMAIL.fullmatch(reply_to.strip()):
        message["replyTo"] = [{"emailAddress": {"address": reply_to.strip()}}]
    with _lock:
        token = _token(client_id, cache_file)
    response = requests.post(
        "https://graph.microsoft.com/v1.0/me/sendMail",
        timeout=30,
        headers={"Authorization": f"Bearer {token}"},
        json={"message": message, "saveToSentItems": False},
    )
    if response.status_code != 202:
        raise RuntimeError(f"Graph sendMail failed: {response.status_code} {response.text[:300]}")
