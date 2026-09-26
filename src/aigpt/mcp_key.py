"""Optional local API key for authenticated MCP stdio sessions.

The MCP server normally uses the local OAuth account pools and does not need a
key. When a client requires an explicit credential, the extension can create
one here. Only a SHA-256 digest is persisted; the plaintext is returned once
to the caller that created it.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from aigpt.auth import tokens

CURRENT_VERSION = 1
_LOCK = threading.RLock()
_PREFIX = "aigpt_"


def key_path() -> Path:
    return tokens._config_dir() / "mcp_api_key.json"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _load() -> dict[str, Any] | None:
    try:
        raw = json.loads(key_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or not raw.get("key_hash"):
        return None
    return raw


def status() -> dict[str, Any]:
    with _LOCK:
        row = _load()
    if not row:
        return {"configured": False, "prefix": "", "created_at": ""}
    return {
        "configured": True,
        "prefix": str(row.get("prefix") or ""),
        "created_at": str(row.get("created_at") or ""),
    }


def create() -> tuple[str, dict[str, Any]]:
    """Rotate the key and return (plaintext, public status)."""
    value = _PREFIX + secrets.token_urlsafe(32)
    now = int(time.time())
    row = {
        "version": CURRENT_VERSION,
        "key_hash": _digest(value),
        "prefix": value[:14],
        "created_at": str(now),
    }
    with _LOCK:
        path = key_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".tmp.{secrets.token_hex(4)}")
        tmp.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    return value, {"configured": True, "prefix": row["prefix"], "created_at": row["created_at"]}


def revoke() -> bool:
    with _LOCK:
        path = key_path()
        if not path.exists():
            return False
        try:
            path.unlink()
        except OSError:
            return False
        return True


def verify(value: str | None) -> bool:
    candidate = str(value or "").strip()
    if not candidate:
        return False
    with _LOCK:
        row = _load()
    return bool(row and secrets.compare_digest(str(row.get("key_hash") or ""), _digest(candidate)))
