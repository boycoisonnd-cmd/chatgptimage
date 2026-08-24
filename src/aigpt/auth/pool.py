"""Multi-account image-quota pool: select / probe / decrement / persist.

Proactively probes ``get_user_info`` for quota and sticks to one account until it
hits 0, then advances; persisted hints skip known-dead accounts. Tokens never logged.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from aigpt.auth import reset_at, store
from aigpt.auth.probe import default_probe, probe_account

ProbeFn = Callable[[dict[str, Any]], dict[str, Any]]
RefreshFn = Callable[..., str]

# Skip an account this long after a refresh error (upstream-style backoff).
_REFRESH_ERROR_BACKOFF = 5 * 60


class NoQuotaError(RuntimeError):
    """Every account is out of image quota. Carries the soonest reset epoch."""

    def __init__(self, message: str, restore_at_epoch: float | None = None):
        super().__init__(message)
        self.restore_at_epoch = restore_at_epoch


class AccountPool:
    """Holds the account list and picks a quota-bearing account per image."""

    def __init__(
        self,
        probe_fn: ProbeFn | None = None,
        refresh_fn: RefreshFn | None = None,
        now_fn: Callable[[], float] = time.time,
    ):
        self._probe_fn = probe_fn or default_probe
        self._refresh_fn = refresh_fn  # per-account token refresh (wired phase 03)
        self._now = now_fn
        self._accounts = store.load_accounts()
        self._active_token: str | None = None
        # Guards select/probe/on_result against concurrent MCP tool calls racing
        # the pool singleton (mutate + save_accounts are not atomic together).
        self._lock = threading.RLock()

    def reload_accounts(self) -> None:
        """Re-read the account store (login/logout happen outside the pool).

        The pool only loads accounts at init; the API server hosts it for a long
        time, so a panel login or logout would otherwise be invisible to
        select()/status() until restart. The active token is cleared if the
        account behind it was removed.
        """
        with self._lock:
            self._accounts = store.load_accounts()
            if self._active_token and self._find(self._active_token) is None:
                self._active_token = None

    def _find(self, token: str) -> dict[str, Any] | None:
        # Match by access_token OR stable refresh_token: after a rotation the
        # engine reports the NEW access_token while the pool may still hold the
        # OLD one as active - the shared refresh_token keeps the lookup alive.
        for a in self._accounts:
            if str(a.get("access_token") or "") == token:
                return a
            if token and str(a.get("refresh_token") or "") == token:
                return a
        return None

    def _remaining(self, acc: dict[str, Any]) -> int | None:
        q = acc.get("last_quota")
        return int(q) if isinstance(q, (int, float)) and not isinstance(q, bool) else None

    def _hint_alive(self, acc: dict[str, Any]) -> bool:
        """Cheap liveness from persisted hints (no network)."""
        now = self._now()
        rea = acc.get("refresh_error_at")
        if rea and now - rea < _REFRESH_ERROR_BACKOFF:
            return False  # recent refresh error -> short backoff (upstream-style)
        ra = acc.get("restore_at")
        return not ra or (reset_at.to_epoch(ra, now) or now) <= now

    def _stickable(self, acc: dict[str, Any]) -> bool:
        rem = self._remaining(acc)
        return isinstance(rem, int) and rem > 0 and self._hint_alive(acc)

    def select(self) -> str:
        """Return a token with image quota (stick-until-exhausted); raise NoQuotaError when dry."""
        with self._lock:
            if self._active_token:
                acc = self._find(self._active_token)
                if acc is not None and self._stickable(acc):
                    return self._active_token
            if not self._accounts:
                raise NoQuotaError("No accounts logged in - run `aigpt login`.")
            # Prefer hint-alive accounts; if none, probe all (a stale hint may lie).
            alive = [a for a in self._accounts if self._hint_alive(a)]
            invalid = 0
            for acc in (alive or list(self._accounts)):
                try:
                    res = self.probe(acc)
                except Exception:
                    invalid += 1  # probe failed: invalid/revoked token (or flaky backend)
                    continue
                if res["unknown"] or (res["remaining"] or 0) > 0:
                    self._active_token = str(acc.get("access_token") or "")
                    return self._active_token
            self._active_token = None
            soonest = self._soonest_restore()
            msg = reset_at.exhaustion_message(len(self._accounts), soonest, invalid)
            raise NoQuotaError(msg, soonest)

    def current_token(self) -> str:
        """Active token, selecting one if needed (enhance shares this account)."""
        return self._active_token or self.select()

    def on_result(self, token: str, ok: bool) -> None:
        """Decrement local quota on success only (no cooldown on failure)."""
        with self._lock:
            acc = self._find(token)
            if acc is None or not ok:
                return
            rem = self._remaining(acc)
            if rem is None:
                return  # unknown quota: cannot decrement; re-probed each select
            acc["last_quota"] = max(0, rem - 1)
            if acc["last_quota"] == 0:
                acc["restore_at"] = reset_at.to_iso(reset_at.fallback(self._now()))
                if self._active_token == token:
                    self._active_token = None
            store.save_accounts(self._accounts)

    # Engine adapter - bound into the vendored shim via set_pool_provider.
    def account_for(self, token: str) -> dict[str, Any]:
        """Token -> minimal account dict so the engine can log the email."""
        acc = self._find(token)
        return {"email": (acc or {}).get("email") or "", "access_token": token}

    def refresh_token(self, token: str) -> str:
        """Best-effort refresh of that account's token; return new or same."""
        acc = self._find(token)
        if acc is None or not self._refresh_fn:
            return token
        try:
            return self._refresh_fn(acc) or token
        except Exception:
            return token

    def disable_token(self, token: str) -> None:
        """Bench an account whose token went invalid so select() skips it."""
        with self._lock:
            acc = self._find(token)
            if acc is None:
                return
            acc["restore_at"] = reset_at.to_iso(reset_at.fallback(self._now()))
            if self._active_token == token:
                self._active_token = None
            store.save_accounts(self._accounts)

    def probe(self, acc: dict[str, Any]) -> dict[str, Any]:
        """Probe get_user_info; update + persist the account (backfilling identity);
        return ``{remaining, unknown, restore_at_epoch}``."""
        with self._lock:
            info = probe_account(acc, self._probe_fn, self._refresh_fn)
            unknown = bool(info.get("image_quota_unknown"))
            q = info.get("quota")
            remaining = int(q) if isinstance(q, (int, float)) and not isinstance(q, bool) else None
            now = self._now()
            for k in ("email", "user_id", "type"):
                if info.get(k):
                    acc[k] = info[k]
            acc["probed_at"] = now
            # Informational backend refill time (display only; separate from restore_at).
            be = reset_at.to_epoch(info.get("restore_at"), now)
            acc["quota_reset_at"] = reset_at.to_iso(be) if be else None
            if unknown:
                acc["last_quota"] = None        # unknown counter -> try optimistically
                acc.pop("restore_at", None)
            else:
                acc["last_quota"] = remaining or 0
                if (remaining or 0) <= 0:
                    epoch = reset_at.to_epoch(info.get("restore_at"), now) or reset_at.fallback(now)
                    acc["restore_at"] = reset_at.to_iso(epoch)
                else:
                    acc.pop("restore_at", None)
            store.save_accounts(self._accounts)
            return {"remaining": remaining, "unknown": unknown,
                    "restore_at_epoch": reset_at.to_epoch(acc.get("restore_at"), now)}

    def is_exhausted(self) -> bool:
        """True when no account is (hint-)available - used by the deck layer."""
        return not self._accounts or not any(self._hint_alive(a) for a in self._accounts)

    def status(self, probe: bool = False) -> list[dict[str, Any]]:
        """Per-account summary. probe=True live-probes each first (CLI `accounts`);
        probe=False uses hints only - cheap, no network (MCP login_status)."""
        with self._lock:
            return self._status_unlocked(probe)

    def _status_unlocked(self, probe: bool) -> list[dict[str, Any]]:
        failed: set[str] = set()
        if probe:
            for acc in list(self._accounts):
                try:
                    self.probe(acc)
                except Exception:
                    failed.add(str(acc.get("access_token") or ""))
        return [
            {
                "email": a.get("email") or "",
                "user_id": a.get("user_id") or "",
                "type": a.get("type") or "free",
                "remaining": self._remaining(a),
                "restore_at": a.get("restore_at"),
                "quota_reset_at": a.get("quota_reset_at"),
                "alive": self._hint_alive(a),
                "probe_failed": str(a.get("access_token") or "") in failed,
            }
            for a in self._accounts
        ]

    def _soonest_restore(self) -> float | None:
        now = self._now()
        epochs = [
            e for a in self._accounts
            if (e := reset_at.to_epoch(a.get("restore_at"), now)) is not None
        ]
        return min(epochs) if epochs else None
