"""Versioned multi-account auth store (auth.json v2).

Owns the on-disk schema ``{version, accounts: [...]}`` and the one-time migration
from the legacy single-account format (a flat dict with ``access_token`` at the
top level). Pure data I/O - selection / probe / quota logic lives in pool.py
(phase 02), not here.

Path + perms mirror the single-account store: we reuse ``tokens._config_dir()``
so the file location has ONE source of truth, and apply best-effort chmod 600.
Token values are never logged.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from aigpt.auth import tokens

CURRENT_VERSION = 2

# Documented account record shape. The hint fields (last_quota, restore_at,
# probed_at) are OPTIONAL - absent on a freshly migrated / just-logged-in
# account, and filled later by the first probe (pool.py) or login.
ACCOUNT_FIELDS = (
    "user_id",        # dedup key - stable (access_token rotates on refresh)
    "email",
    "type",           # plan: free / plus / pro / team ...
    "access_token",
    "refresh_token",
    "id_token",
    "saved_at",
    "last_quota",       # hint: last-known remaining image quota
    "restore_at",       # internal bench marker: account unusable until this time
    "probed_at",        # hint: when we last probed get_user_info
    "quota_reset_at",   # display: backend image_gen refill time (informational)
    "refresh_error_at", # backoff: when a token refresh last failed (skip briefly)
)


def _auth_path() -> Path:
    """Single source of truth for the auth file location (shared with tokens)."""
    return tokens._config_dir() / "auth.json"


def _read_raw() -> dict[str, Any] | None:
    """Read + parse auth.json. Return None on missing/corrupt - never raise."""
    p = _auth_path()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _migrate_if_legacy(raw: dict[str, Any] | None) -> tuple[dict[str, Any], bool]:
    """Normalize any on-disk shape to v2. Return ``(v2_dict, changed)``.

    - Already v2 (has a truthy ``version``) -> normalized, ``changed=False``.
    - Legacy flat (``access_token`` present, no ``version``) -> wrapped as one
      account, ``changed=True``. Legacy never stored ``user_id`` -> "".
    - Missing / unknown shape -> empty v2, ``changed=False`` (do NOT clobber an
      unexpected file on a pure read; an explicit save will overwrite later).
    """
    if not isinstance(raw, dict):
        return {"version": CURRENT_VERSION, "accounts": []}, False
    if raw.get("version"):
        accounts = raw.get("accounts")
        accounts = accounts if isinstance(accounts, list) else []
        return {"version": CURRENT_VERSION, "accounts": accounts}, False
    if raw.get("access_token"):
        account = {k: v for k, v in raw.items() if k != "version"}
        account.setdefault("user_id", "")
        return {"version": CURRENT_VERSION, "accounts": [account]}, True
    return {"version": CURRENT_VERSION, "accounts": []}, False


def load_accounts() -> list[dict[str, Any]]:
    """Return the account list, upgrading a legacy file in-place exactly once."""
    raw = _read_raw()
    v2, changed = _migrate_if_legacy(raw)
    if changed:
        save_accounts(v2["accounts"])  # persist the upgraded shape
    return v2["accounts"]


def save_accounts(accounts: list[dict[str, Any]]) -> None:
    """Write the v2 file atomically (temp + os.replace), indent=2 utf-8, chmod 600.

    The temp-then-rename avoids a torn auth.json if the process dies (or, before
    the sequential-generation guard, raced) mid-write.
    """
    p = _auth_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": CURRENT_VERSION, "accounts": list(accounts)}
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, p)  # atomic on the same filesystem


def upsert_account(acc: dict[str, Any]) -> None:
    """Insert or replace an account, deduped by ``user_id`` then ``refresh_token``.

    ``user_id`` is the stable identity but is empty until the first probe (login
    may skip it). ``refresh_token`` survives access-token rotation, so it is the
    reliable fallback key: a login/re-login whose probe failed must never append
    a duplicate row next to the rotated record (the access_token changed, the
    refresh_token did not). ``access_token`` is NOT used as a dedup key - it
    rotates, so a fresh login after rotation would miss. Existing fields are
    merged (keeps hint fields when a re-login payload omits them); ``saved_at``
    is refreshed.
    """
    accounts = load_accounts()
    acc = {**acc, "saved_at": time.time()}
    uid = str(acc.get("user_id") or "").strip()
    rtok = str(acc.get("refresh_token") or "").strip()
    for i, existing in enumerate(accounts):
        same_uid = bool(uid) and str(existing.get("user_id") or "").strip() == uid
        same_rtok = bool(rtok) and \
            str(existing.get("refresh_token") or "").strip() == rtok
        if same_uid or same_rtok:
            accounts[i] = {**existing, **acc}
            save_accounts(accounts)
            return
    accounts.append(acc)
    save_accounts(accounts)


def remove_account(selector: str) -> int:
    """Remove accounts whose ``user_id`` OR ``email`` equals ``selector``.

    Return the number removed.
    """
    sel = str(selector or "").strip()
    if not sel:
        return 0
    accounts = load_accounts()
    kept = [
        a for a in accounts
        if str(a.get("user_id") or "").strip() != sel
        and str(a.get("email") or "").strip() != sel
    ]
    removed = len(accounts) - len(kept)
    if removed:
        save_accounts(kept)
    return removed


def remove_all() -> None:
    """Clear every stored account."""
    save_accounts([])
