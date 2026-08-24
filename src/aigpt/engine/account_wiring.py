"""Wire the multi-account AccountPool into the vendored engine (import for side effects).

Imported by generate.py before any token is requested. On import it:
  - registers the pool's callables on the vendored shim (set_pool_provider), and
  - forces SEQUENTIAL generation (design decision #7) - the unlocked pool
    singleton must never be raced by the engine's parallel n>1 branch.
Exposes get_pool() (lazy) and pool_exhausted_reset() for the engine + deck layer.
Keeps the vendored shim free of any aigpt import (we pass plain callables).
"""
from __future__ import annotations

# Importing _vendor_path puts the vendored `services.*` on sys.path AND sets the
# vendored config's required auth-key env (single source: see _vendor_path).
# MUST precede all vendored imports (sys.path side effect).
import aigpt._vendor_path  # noqa: F401
from aigpt.auth import tokens
from aigpt.auth.pool import AccountPool
from services.account_service import set_pool_provider
from services.config import config as _vendor_config

_pool: AccountPool | None = None


def get_pool() -> AccountPool:
    """Process-wide account pool (lazy: store is read on first real use)."""
    global _pool
    if _pool is None:
        _pool = AccountPool(refresh_fn=tokens.refresh_for)
    return _pool


def pool_exhausted_reset() -> tuple[bool, str | None]:
    """(all accounts exhausted?, soonest reset ISO or None) for partial-deck handling.
    Asks the pool directly - robust vs string-matching the engine's wrapped error."""
    pool = get_pool()
    if not pool.is_exhausted():
        return False, None
    resets = [r["restore_at"] for r in pool.status() if r.get("restore_at")]
    return True, (min(resets) if resets else None)


set_pool_provider(
    select=lambda: get_pool().select(),
    on_result=lambda token, ok: get_pool().on_result(token, ok),
    account_lookup=lambda token: get_pool().account_for(token),
    text_token=lambda: get_pool().current_token(),
    refresh=lambda token: get_pool().refresh_token(token),
    remove=lambda token: get_pool().disable_token(token),
)

# Force sequential generation (decision #7): the unlocked pool singleton must not
# be raced by the engine's parallel n>1 branch. Runtime patch, no _vendor edit.
_vendor_config.data["image_parallel_generation"] = False

# I2I (especially 2–4 refs) routinely exceeds the vendor default 120s poll.
# ChatGPT itself can sit in queue; 120s then surfaces as an image-generation
# timeout even when the conversation exists. Bump at runtime — no _vendor /
# config.json edit.
_vendor_config.data["image_poll_timeout_secs"] = 300
