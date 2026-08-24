"""Account probe helpers - the only place the pool reaches the network.

Kept separate from pool.py so the pool's selection logic stays pure and
unit-testable (tests inject a stub probe_fn). Token values are never logged.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

ProbeFn = Callable[[dict[str, Any]], dict[str, Any]]
RefreshFn = Callable[..., str]


def default_probe(account: dict[str, Any]) -> dict[str, Any]:
    """Call get_user_info with the account's access_token (lazy vendor import)."""
    import aigpt._vendor_path  # noqa: F401  (side effect: _vendor on sys.path)
    from services.openai_backend_api import OpenAIBackendAPI

    return OpenAIBackendAPI(access_token=account["access_token"]).get_user_info()


def _is_invalid_token_error(exc: Exception) -> bool:
    """True when the backend says this access_token is invalid/revoked.

    Imported lazily so probe.py stays importable before the vendored engine is
    on sys.path; isinstance keeps the check robust vs upstream renames.
    """
    import aigpt._vendor_path  # noqa: F401  (side effect: _vendor on sys.path)
    from services.openai_backend_api import InvalidAccessTokenError

    return isinstance(exc, InvalidAccessTokenError)


def probe_account(account: dict[str, Any], probe_fn: ProbeFn,
                  refresh_fn: RefreshFn | None = None) -> dict[str, Any]:
    """Call probe_fn; on an invalid-token rejection, force-refresh + retry ONCE.
    Returns the raw info dict; re-raises if it still fails (the pool's select()
    then skips that account).

    M8: no proactive refresh up-front - it rotated the access_token on EVERY
    probe (even healthy ones), which chained into the H2 duplicate-account bug
    when the login-time user_id was empty. Refresh now only happens after the
    backend actually rejects the token.
    """
    try:
        return probe_fn(account)
    except Exception as exc:
        # Token expired / rejected: force a refresh (bypass freshness gate), retry once.
        if not refresh_fn or not _is_invalid_token_error(exc):
            raise
        account["access_token"] = refresh_fn(account, force=True) or account["access_token"]
        return probe_fn(account)
