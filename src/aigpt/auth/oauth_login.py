"""Terminal OAuth (PKCE) login against OpenAI, two-step (non-blocking).

Step 1 (build_and_stash): create authorize URL + persist the PKCE verifier.
Step 2 (complete): read the stashed verifier, exchange the code for tokens.
"""
from __future__ import annotations

import json
import os
import secrets
import uuid
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from curl_cffi import requests

import aigpt._vendor_path  # noqa: F401  MUST precede vendored imports (sys.path)
from aigpt.auth import tokens as _tokens
from utils.pkce import generate_pkce  # vendored

_AUTH_BASE = "https://auth.openai.com"
_PLATFORM = "https://platform.openai.com"
_CLIENT_ID = "app_2SKx67EdpoN0G6j64rFvigXD"
_REDIRECT = f"{_PLATFORM}/auth/callback"
_AUDIENCE = "https://api.openai.com/v1"
_AUTH0_CLIENT = "eyJuYW1lIjoiYXV0aDAtc3BhLWpzIiwidmVyc2lvbiI6IjEuMjEuMCJ9"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36")


def _pending_path() -> Path:
    return _tokens._config_dir() / "login_pending.json"


def build_authorize_url(email_hint: str = "") -> tuple[str, str, str]:
    """Return (authorize_url, code_verifier, state)."""
    verifier, challenge = generate_pkce()
    state = secrets.token_urlsafe(16)
    nonce = secrets.token_urlsafe(32)
    params = {
        "issuer": _AUTH_BASE, "client_id": _CLIENT_ID, "audience": _AUDIENCE,
        "redirect_uri": _REDIRECT, "device_id": str(uuid.uuid4()),
        "screen_hint": "login_or_signup", "max_age": "0",
        "scope": "openid profile email offline_access",
        "response_type": "code", "response_mode": "query",
        "state": state, "nonce": nonce,
        "code_challenge": challenge, "code_challenge_method": "S256",
        "auth0Client": _AUTH0_CLIENT,
    }
    if email_hint.strip():
        params["login_hint"] = email_hint.strip()
    return f"{_AUTH_BASE}/api/accounts/authorize?{urlencode(params)}", verifier, state


def extract_code(callback: str, expected_state: str | None = None) -> str:
    """Pull the auth code out of a callback URL or bare code paste.

    When the callback is a URL, its ``state`` is verified against the stashed
    value (anti-CSRF, M1) before any code is used.
    """
    raw = callback.strip()
    if raw.startswith("http"):
        qs = parse_qs(urlparse(raw).query)
        if expected_state:
            got = (qs.get("state") or [""])[0].strip()
            if not got or got != expected_state:
                raise RuntimeError(
                    "callback state mismatch - login aborted "
                    "(stale or forged callback URL?)"
                )
        code = (qs.get("code") or [""])[0].strip()
        if not code:
            raise RuntimeError("callback URL has no ?code= param")
        return code
    # Bare-code paste: cannot be state-verified (no URL to inspect). Accepted as
    # a convenience fallback - the verifier + code still bind via PKCE, so a
    # stolen code without the verifier is useless.
    return raw  # user pasted bare code


def exchange_code(code: str, verifier: str) -> dict[str, str]:
    resp = requests.post(
        f"{_AUTH_BASE}/api/accounts/oauth/token",
        headers={"Content-Type": "application/json", "User-Agent": _UA,
                 "origin": _PLATFORM, "referer": f"{_PLATFORM}/"},
        json={"client_id": _CLIENT_ID, "code_verifier": verifier,
              "grant_type": "authorization_code", "code": code,
              "redirect_uri": _REDIRECT},
        timeout=60,
    )
    data = resp.json() if resp.text else {}
    if resp.status_code != 200 or not data.get("access_token"):
        detail = data.get("error_description") or data.get("error") or resp.text[:200]
        raise RuntimeError(f"token exchange failed (HTTP {resp.status_code}): {detail}")
    if not data.get("refresh_token"):
        raise RuntimeError("no refresh_token returned (scope missing offline_access?)")
    return {"access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "id_token": data.get("id_token", "")}


def build_and_stash(email_hint: str = "", open_browser: bool = True) -> str:
    """Step 1: build authorize URL, persist verifier + state. Returns the URL.

    ``open_browser=False`` skips webbrowser.open - the caller (e.g. the API
    server's /login/start) hands the URL to the extension panel, which opens
    the tab itself (an API process must not pop a browser window).
    """
    url, verifier, state = build_authorize_url(email_hint)
    p = _pending_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"code_verifier": verifier, "state": state}), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    return url


def discard_pending() -> None:
    """Remove the pending login file (stale verifier). Idempotent, never raises."""
    try:
        _pending_path().unlink()
    except OSError:
        pass


def _account_from_tokens(toks: dict[str, str]) -> dict:
    """Build an account record, probing get_user_info once for user_id/email/type.

    Best-effort: if the probe fails (network), still return a usable account with
    an empty user_id - the first real run's probe backfills it. Never aborts login.
    """
    account = {
        "access_token": toks["access_token"],
        "refresh_token": toks["refresh_token"],
        "id_token": toks.get("id_token", ""),
        "user_id": "",
        "refresh_error_at": None,  # fresh login clears any stale refresh-error backoff
    }
    try:
        from services.openai_backend_api import OpenAIBackendAPI
        info = OpenAIBackendAPI(access_token=toks["access_token"]).get_user_info()
        account["user_id"] = info.get("user_id") or ""
        account["email"] = info.get("email") or ""
        account["type"] = info.get("type") or "free"
        q = info.get("quota")
        if isinstance(q, (int, float)) and not info.get("image_quota_unknown"):
            account["last_quota"] = int(q)  # seed the hint for the first run
    except Exception:
        pass
    return account


def complete(callback: str) -> dict:
    """Step 2: read stashed verifier + state, exchange code, UPSERT the account
    into the pool (deduped by user_id / refresh_token). Returns the account
    record. The pending file is always removed (M1: try/finally)."""
    p = _pending_path()
    if not p.exists():
        raise RuntimeError("no pending login — run `aigpt login` first")
    try:
        pending = json.loads(p.read_text(encoding="utf-8"))
        verifier = pending.get("code_verifier", "")
        state = pending.get("state", "")
        if not verifier:
            raise RuntimeError("pending login is corrupt — run `aigpt login` again")
        code = extract_code(callback, expected_state=state or None)
        toks = exchange_code(code, verifier)
        account = _account_from_tokens(toks)
        from aigpt.auth import store
        store.upsert_account(account)
        return account
    finally:
        try:
            p.unlink()
        except OSError:
            pass
