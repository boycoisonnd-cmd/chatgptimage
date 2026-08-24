"""Unit tests for probe_account's refresh-on-invalid-token policy (M8)."""
from __future__ import annotations

import pytest

from aigpt.auth.probe import probe_account


def test_no_refresh_no_force_when_probe_ok():
    acc = {"access_token": "t1"}
    calls = []

    def probe_fn(a):
        calls.append("probe")
        return {"quota": 5}

    out = probe_account(acc, probe_fn, refresh_fn=lambda a, **kw: "rotated")
    assert out["quota"] == 5
    assert calls == ["probe"]  # exactly one probe, no refresh churn


def test_refresh_and_retry_once_on_invalid_token():
    class _Invalid(Exception):
        pass

    # Same class name as the vendored error would not matter - we fake it via
    # the real isinstance path by patching the lazily-imported exception.
    import aigpt._vendor_path  # noqa: F401
    from services.openai_backend_api import InvalidAccessTokenError

    acc = {"access_token": "t1", "refresh_token": "r1"}
    calls = []
    forced = []

    def probe_fn(a):
        calls.append(a["access_token"])
        if len(calls) == 1:
            raise InvalidAccessTokenError("bad token")
        return {"quota": 3}

    def refresh_fn(a, force=False):
        forced.append(force)
        return "t2"

    out = probe_account(acc, probe_fn, refresh_fn)
    assert out["quota"] == 3
    assert calls == ["t1", "t2"]  # retried with the fresh token
    assert forced == [True]
    assert acc["access_token"] == "t2"


def test_rejects_other_errors_without_refresh():
    acc = {"access_token": "t1"}

    def probe_fn(a):
        raise RuntimeError("network down")

    with pytest.raises(RuntimeError):
        probe_account(acc, probe_fn, refresh_fn=lambda a, **kw: "rotated")


def test_no_refresh_fn_reraises_invalid():
    import aigpt._vendor_path  # noqa: F401
    from services.openai_backend_api import InvalidAccessTokenError

    def probe_fn(a):
        raise InvalidAccessTokenError("bad")

    with pytest.raises(InvalidAccessTokenError):
        probe_account({"access_token": "t1"}, probe_fn, refresh_fn=None)
