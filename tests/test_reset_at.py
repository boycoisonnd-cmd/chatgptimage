"""Unit tests for reset_at time parsing (pure logic)."""
from __future__ import annotations

from aigpt.auth import reset_at

NOW = 1_800_000_000.0


def test_absolute_epoch_passthrough():
    assert reset_at.to_epoch(1_700_000_123, NOW) == 1_700_000_123


def test_duration_added_to_now():
    assert reset_at.to_epoch(3600, NOW) == NOW + 3600


def test_numeric_string_parsed():
    assert reset_at.to_epoch("3600", NOW) == NOW + 3600
    assert reset_at.to_epoch(str(1_700_000_000), NOW) == 1_700_000_000


def test_iso8601_parsed():
    assert reset_at.to_epoch("2027-01-01T00:00:00Z", NOW) == 1_798_761_600.0


def test_implausible_future_clamped_to_24h():
    clamped = reset_at.to_epoch(NOW + 999_999_999, NOW)
    assert clamped <= NOW + 24 * 3600
    assert clamped > NOW


def test_empty_and_unparseable_return_none():
    assert reset_at.to_epoch(None, NOW) is None
    assert reset_at.to_epoch("", NOW) is None
    assert reset_at.to_epoch("not-a-time", NOW) is None
    assert reset_at.to_epoch(True, NOW) is None  # bool is an int subclass - reject


def test_to_iso_roundtrip():
    assert reset_at.to_iso(NOW) == "2027-01-15T08:00:00+00:00"


def test_fallback():
    assert reset_at.fallback(NOW) == NOW + 24 * 3600
    assert reset_at.fallback(NOW, hours=2) == NOW + 7200


def test_exhaustion_message():
    msg = reset_at.exhaustion_message(2, NOW + 3600, invalid=0)
    assert "out of image quota" in msg
    assert "2027-01-15T09:00:00" in msg  # NOW + 3600 formatted
    assert "invalid tokens" in reset_at.exhaustion_message(1, None, invalid=1)
