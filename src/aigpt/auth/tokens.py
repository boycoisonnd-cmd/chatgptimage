"""Token refresh against OpenAI's OAuth endpoint (per-account, via the v2 store)."""
from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any

from curl_cffi import requests

_CLIENT_ID = "app_2SKx67EdpoN0G6j64rFvigXD"
_TOKEN_URL = "https://auth.openai.com/oauth/token"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36")
# Pool gate (refresh_for): proactively refresh when the access token expires within
# this window, by its JWT `exp` - mirrors upstream's _ACCESS_TOKEN_REFRESH_SKEW_SECONDS.
_ACCESS_TOKEN_REFRESH_SKEW = 24 * 3600


def _config_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    return Path(base) / "aigpt"


def _refresh_request(refresh_token: str) -> dict[str, str]:
    resp = requests.post(
        _TOKEN_URL,
        headers={"Accept": "application/json",
                 "Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": _UA},
        data={"grant_type": "refresh_token",
              "refresh_token": refresh_token,
              "client_id": _CLIENT_ID},
        timeout=60,
    )
    data = resp.json() if resp.text else {}
    if resp.status_code != 200 or not data.get("access_token"):
        raise RuntimeError(f"token refresh failed (HTTP {resp.status_code})")
    return data


def _jwt_exp(token: str) -> int:
    """Best-effort decode of a JWT access token's `exp` claim (0 if unknown)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return int(json.loads(base64.urlsafe_b64decode(payload)).get("exp") or 0)
    except Exception:
        return 0


def refresh_for(account: dict[str, Any], force: bool = False) -> str:
    """Refresh ONE pool account's access_token via its refresh_token.

    Proactive like upstream: refreshes when the access token expires within
    _ACCESS_TOKEN_REFRESH_SKEW (by its JWT `exp`) or when forced; otherwise returns
    the token unchanged. On a refresh failure it stamps `refresh_error_at` (so the
    pool backs that account off briefly) and re-raises. Persists via the v2 store.
    """
    from aigpt.auth import store  # local import avoids a store<->tokens cycle

    tok = str(account.get("access_token") or "")
    if not account.get("refresh_token"):
        return tok
    exp = _jwt_exp(tok)
    near_expiry = exp > 0 and (exp - time.time()) <= _ACCESS_TOKEN_REFRESH_SKEW
    if not force and not near_expiry:
        return tok
    try:
        new = _refresh_request(account["refresh_token"])
    except Exception:
        account["refresh_error_at"] = time.time()
        store.upsert_account(account)
        raise
    account.update({"access_token": new.get("access_token") or tok,
                    "saved_at": time.time(), "refresh_error_at": None})
    if new.get("refresh_token"):
        account["refresh_token"] = new["refresh_token"]
    if new.get("id_token"):
        account["id_token"] = new["id_token"]
    store.upsert_account(account)
    return account["access_token"]
