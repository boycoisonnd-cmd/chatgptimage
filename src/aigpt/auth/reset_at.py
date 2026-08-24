"""Parse / format image-quota reset times.

Upstream's ``get_user_info`` returns ``reset_after`` as a RAW, unparsed value
whose exact shape is not guaranteed (ISO-8601 timestamp, epoch seconds, or a
relative duration). We normalize everything to an epoch float, and store/display
it as an ISO-8601 UTC string. Anything unparseable falls back to "now + 24h" so
a bad value can only DELAY a re-probe, never skip an account forever (the probe
remains the source of truth and overrides the hint).
"""
from __future__ import annotations

from datetime import UTC, datetime

# A value at/above this is read as an absolute epoch; below it, as a duration.
_PLAUSIBLE_EPOCH_FLOOR = 1_000_000_000  # ~2001-09-09
# A real image-quota reset is never this far out; a result beyond it means we
# misparsed an unknown upstream format (e.g. milliseconds read as seconds).
_MAX_FUTURE = 7 * 24 * 3600  # 7 days


def _num_to_epoch(value: float, now: float) -> float:
    """Big number -> absolute epoch; small number -> duration-from-now."""
    if value >= _PLAUSIBLE_EPOCH_FLOOR:
        return value
    return now + max(0.0, value)


def _parse(raw: object, now: float) -> float | None:
    """Parse a reset value to epoch seconds, or None if empty/unparseable."""
    if raw is None or isinstance(raw, bool):  # bool is an int subclass - reject
        return None
    if isinstance(raw, (int, float)):
        return _num_to_epoch(float(raw), now)
    s = str(raw).strip()
    if not s:
        return None
    try:  # numeric string -> epoch or duration
        return _num_to_epoch(float(s), now)
    except ValueError:
        pass
    try:  # ISO-8601 (fromisoformat accepts a trailing Z since Python 3.11)
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def to_epoch(raw: object, now: float) -> float | None:
    """Best-effort parse to epoch seconds; None for empty/unparseable input.

    Clamps an implausibly-far result (a misparsed unknown upstream format, e.g. a
    millisecond value read as seconds) to now+24h, so a bad reset_after can never
    bench an account for years - the probe stays the source of truth and corrects it.
    """
    epoch = _parse(raw, now)
    if epoch is None:
        return None
    if epoch > now + _MAX_FUTURE:
        return now + 24 * 3600.0
    return epoch


def to_iso(epoch: float) -> str:
    """Epoch seconds -> ISO-8601 UTC string (storage + display form)."""
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat()


def fallback(now: float, hours: float = 24.0) -> float:
    """Default reset epoch when upstream gave us nothing parseable."""
    return now + hours * 3600.0


def exhaustion_message(n: int, soonest: float | None, invalid: int = 0) -> str:
    """All-accounts-failed error text, distinguishing the two causes: out of image
    quota (wait for the reset) vs invalid/revoked tokens (re-login)."""
    if invalid and not soonest:
        return f"All {n} account(s) have invalid tokens - run `aigpt login` again."
    msg = f"All {n} account(s) are out of image quota."
    if soonest:
        msg += f" Soonest reset at {to_iso(soonest)}."
    if invalid:
        msg += f" ({invalid} had invalid tokens - try `aigpt login`.)"
    return msg
