"""Local, versioned Antigravity credential storage.

This file deliberately does not share ChatGPT's auth.json: Antigravity uses a
different OAuth provider and has different account/quota metadata.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any

from aigpt.auth import tokens

CURRENT_VERSION = 1
_LOCK = threading.RLock()


def auth_path() -> Path:
    return tokens._config_dir() / "antigravity_auth.json"


def load() -> list[dict[str, Any]]:
    with _LOCK:
        try:
            raw = json.loads(auth_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(raw, dict):
            return []
        accounts = raw.get("accounts")
        return [dict(a) for a in accounts if isinstance(a, dict)] if isinstance(accounts, list) else []


def save(accounts: list[dict[str, Any]]) -> None:
    with _LOCK:
        path = auth_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".tmp.{secrets.token_hex(4)}")
        tmp.write_text(
            json.dumps({"version": CURRENT_VERSION, "accounts": accounts}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)


def upsert(account: dict[str, Any]) -> None:
    email = str(account.get("email") or "").strip().lower()
    refresh = str(account.get("refresh_token") or "").strip()
    accounts = load()
    for i, old in enumerate(accounts):
        same_email = email and str(old.get("email") or "").strip().lower() == email
        same_refresh = refresh and str(old.get("refresh_token") or "").strip() == refresh
        if same_email or same_refresh:
            accounts[i] = {**old, **account}
            save(accounts)
            return
    accounts.append(dict(account))
    save(accounts)


def remove(selector: str) -> int:
    key = str(selector or "").strip().lower()
    accounts = load()
    kept = [a for a in accounts if str(a.get("email") or "").strip().lower() != key]
    removed = len(accounts) - len(kept)
    if removed:
        save(kept)
    return removed
