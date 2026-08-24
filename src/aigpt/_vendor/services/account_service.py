"""Account shim replacing chatgpt2api's multi-account pool.

LOCAL file - never re-vendored (listed in NOTICE). The vendored engine talks to
``account_service`` for token selection, per-image result feedback, account
lookup, and refresh. We back those calls with INJECTED callables wired at runtime
from our code (``set_pool_provider`` from aigpt.engine, or the single-account
``set_token_provider`` adapter), so this file imports NOTHING from aigpt and the
vendored engine stays byte-identical to upstream.

Every method the engine calls is defined EXPLICITLY (not just via the
``__getattr__`` no-op): tests/test_vendor_contract.py fails loudly if a vendored
call would silently fall through and return None.
"""
from __future__ import annotations

import base64
import binascii
import json
from typing import Any, Callable, Optional

# Injected provider callables (wired at runtime; None until login is configured).
_select: Optional[Callable[[], str]] = None               # -> access_token (may raise)
_on_result: Optional[Callable[[str, bool], None]] = None  # (token, ok) -> None
_account_lookup: Optional[Callable[[str], dict]] = None   # token -> account dict
_text_token: Optional[Callable[[], str]] = None           # -> access_token (text path)
_refresh: Optional[Callable[[str], str]] = None           # token -> (new) token
_remove: Optional[Callable[[str], None]] = None           # token -> None


def set_pool_provider(
    *,
    select: Callable[[], str],
    on_result: Callable[[str, bool], None],
    account_lookup: Callable[[str], dict],
    text_token: Callable[[], str],
    refresh: Callable[[str], str],
    remove: Callable[[str], None],
) -> None:
    """Wire the multi-account pool's callables into the shim."""
    global _select, _on_result, _account_lookup, _text_token, _refresh, _remove
    _select = select
    _on_result = on_result
    _account_lookup = account_lookup
    _text_token = text_token
    _refresh = refresh
    _remove = remove


def set_token_provider(get_token: Callable[[], str],
                       refresh: Callable[[bool], str]) -> None:
    """Back-compat single-account adapter -> a one-token pool provider.

    Kept so standalone callers (and tests/test_vendor_contract) that expect the
    original hook keep working; a single logged-in account is just a 1-token pool.
    """
    set_pool_provider(
        select=get_token,
        on_result=lambda token, ok: None,
        account_lookup=lambda token: {"access_token": token},
        text_token=get_token,
        refresh=lambda token: refresh(True),
        remove=lambda token: None,
    )


def _require(fn: Optional[Callable[[], str]]) -> Callable[[], str]:
    if fn is None:
        raise RuntimeError("aigpt: not logged in. Run `aigpt login` first.")
    return fn


class _AccountService:
    # --- image path: token selection ----------------------------------------
    def get_available_access_token(self, *args: Any, **kwargs: Any) -> str:
        # Pool-selection kwargs (plan_type/source_type/plan_types) are ignored:
        # aigpt's pool does its own quota-based selection.
        return _require(_select)()

    # --- text path: shares the active image account -------------------------
    def get_text_access_token(self, *args: Any, **kwargs: Any) -> str:
        return _require(_text_token)()

    # --- account lookup (engine reads .get("email"/...) defensively) --------
    def get_account(self, token: str) -> dict[str, Any]:
        if _account_lookup is not None:
            return _account_lookup(token) or {"access_token": token}
        return {"access_token": token}

    def _decode_jwt_payload(self, token: str) -> dict[str, Any]:
        # Must return a dict - engine calls .get(...) on the result.
        try:
            parts = token.split(".")
            if len(parts) < 2:
                return {}
            payload_b64 = parts[1]
            padding = "=" * (-len(payload_b64) % 4)
            decoded = base64.urlsafe_b64decode(payload_b64 + padding)
            data = json.loads(decoded)
            return data if isinstance(data, dict) else {}
        except (ValueError, binascii.Error, json.JSONDecodeError):
            return {}

    # --- token refresh (engine compares old vs returned token) --------------
    def refresh_access_token(self, access_token: str, *, force: bool = False,
                             event: str = "refresh") -> str:
        if _refresh is None:
            return access_token
        return _refresh(access_token) or access_token

    # --- per-image result -> pool decrements quota on success ---------------
    def mark_image_result(self, access_token: str, success: bool,
                          *args: Any, **kwargs: Any) -> None:
        if _on_result is not None:
            _on_result(access_token, bool(success))
        return None

    # --- pure side-effect calls --------------------------------------------
    def mark_text_used(self, *args: Any, **kwargs: Any) -> None:
        return None

    def remove_invalid_token(self, token: str, *args: Any, **kwargs: Any) -> None:
        if _remove is not None:
            _remove(token)
        return None

    # --- catch-all for off-path pool/registration helpers -------------------
    def __getattr__(self, name: str):
        def _noop(*a: Any, **k: Any):
            return None
        return _noop


account_service = _AccountService()
