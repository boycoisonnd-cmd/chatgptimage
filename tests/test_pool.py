"""Unit tests for the multi-account quota pool state machine.

Uses injected probe/now functions - zero network, zero disk.
"""
from __future__ import annotations

import pytest

from aigpt.auth import reset_at, tokens
from aigpt.auth.pool import AccountPool, NoQuotaError


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    """pool.probe() persists via store.save_accounts() -> keep real APPDATA clean."""
    monkeypatch.setattr(tokens, "_config_dir", lambda: tmp_path)


def _acc(token: str, quota, **extra) -> dict:
    acc = {"access_token": token, "last_quota": quota, **extra}
    return acc


def _alive_probe(acc: dict) -> dict:
    """Probe reports whatever the account's last_quota says.

    Format mirrors OpenAIBackendAPI.get_user_info: `quota` + `image_quota_unknown`
    (pool.probe reads exactly those keys).
    """
    q = acc.get("last_quota")
    if not isinstance(q, (int, float)):
        return {"quota": None, "image_quota_unknown": True, "restore_at": None}
    return {"quota": int(q), "image_quota_unknown": False, "restore_at": None}


def test_select_returns_active_token_while_stickable():
    pool = AccountPool(probe_fn=_alive_probe)
    pool._accounts = [_acc("tok1", 5), _acc("tok2", 1)]
    assert pool.select() == "tok1"
    assert pool.current_token() == "tok1"  # stick until exhausted


def test_select_advances_when_active_exhausted():
    pool = AccountPool(probe_fn=_alive_probe)
    pool._accounts = [_acc("tok1", 0, restore_at="2099-01-01T00:00:00+00:00"),
                      _acc("tok2", 3)]
    assert pool.select() == "tok2"


def test_select_prefers_hint_alive_account():
    pool = AccountPool(probe_fn=_alive_probe, now_fn=lambda: 1_800_000_000.0)
    # tok1 benched (future restore_at), tok2 alive -> probe order picks tok2.
    pool._accounts = [_acc("tok1", 9, restore_at="2999-01-01T00:00:00+00:00"),
                      _acc("tok2", 2)]
    assert pool.select() == "tok2"


def test_select_all_dry_raises_noquota_with_restore():
    pool = AccountPool(probe_fn=_alive_probe, now_fn=lambda: 1_800_000_000.0)
    pool._accounts = [_acc("tok1", 0, restore_at=reset_at.to_iso(1_800_000_000.0 + 3600)),
                      _acc("tok2", 0, restore_at=reset_at.to_iso(1_800_000_000.0 + 7200))]
    with pytest.raises(NoQuotaError) as exc:
        pool.select()
    # Both accounts are hint-benched (future restore_at) -> probe fallback:
    # probe's info carries no restore_at, so each account is benched until
    # now + 24h (reset_at.fallback). Soonest = now + 24h.
    assert exc.value.restore_at_epoch == 1_800_000_000.0 + 24 * 3600


def test_select_no_accounts_raises():
    pool = AccountPool(probe_fn=_alive_probe)
    pool._accounts = []
    with pytest.raises(NoQuotaError):
        pool.select()


def test_select_probe_failure_skips_account():
    def flaky_probe(acc: dict) -> dict:
        raise RuntimeError("invalid token")

    pool = AccountPool(probe_fn=flaky_probe)
    pool._accounts = [_acc("bad", 5)]
    with pytest.raises(NoQuotaError):
        pool.select()


def test_on_result_decrements_and_benches_at_zero():
    pool = AccountPool(probe_fn=_alive_probe, now_fn=lambda: 1_800_000_000.0)
    pool._accounts = [_acc("tok1", 2)]
    pool._active_token = "tok1"

    pool.on_result("tok1", ok=True)
    assert pool._accounts[0]["last_quota"] == 1

    pool.on_result("tok1", ok=True)
    assert pool._accounts[0]["last_quota"] == 0
    assert pool._accounts[0].get("restore_at")  # benched
    assert pool._active_token is None  # unstuck


def test_on_result_failure_does_not_decrement():
    pool = AccountPool(probe_fn=_alive_probe)
    pool._accounts = [_acc("tok1", 3)]
    pool.on_result("tok1", ok=False)
    assert pool._accounts[0]["last_quota"] == 3


def test_disable_token_benches_account():
    pool = AccountPool(probe_fn=_alive_probe, now_fn=lambda: 1_800_000_000.0)
    pool._accounts = [_acc("tok1", 7)]
    pool._active_token = "tok1"
    pool.disable_token("tok1")
    assert pool._accounts[0].get("restore_at")
    assert pool._active_token is None


def test_refresh_error_backoff_hides_account():
    pool = AccountPool(probe_fn=_alive_probe, now_fn=lambda: 1_800_000_000.0)
    now = 1_800_000_000.0
    pool._accounts = [_acc("tok1", 5, refresh_error_at=now - 60)]  # within 5-min backoff
    assert not pool._hint_alive(pool._accounts[0])

    pool._accounts = [_acc("tok1", 5, refresh_error_at=now - 400)]  # backoff elapsed
    assert pool._hint_alive(pool._accounts[0])


def test_status_hint_only_marks_restore():
    pool = AccountPool(probe_fn=_alive_probe, now_fn=lambda: 1_800_000_000.0)
    pool._accounts = [_acc("tok1", 0, restore_at="2099-01-01T00:00:00+00:00", email="a@b")]
    rows = pool.status()
    assert rows[0]["email"] == "a@b"
    assert rows[0]["remaining"] == 0
    assert rows[0]["alive"] is False


def test_is_exhausted():
    pool = AccountPool(probe_fn=_alive_probe, now_fn=lambda: 1_800_000_000.0)
    pool._accounts = [_acc("tok1", 0, restore_at="2099-01-01T00:00:00+00:00")]
    assert pool.is_exhausted()
    pool._accounts = [_acc("tok1", 2)]
    assert not pool.is_exhausted()


# ------------------------------------------------------------ reload_accounts

def test_reload_accounts_picks_up_new_account(iso, monkeypatch):
    from aigpt.auth import store
    pool = AccountPool(probe_fn=_alive_probe)
    pool._accounts = []  # simulate a long-lived pool that never saw the login
    store.upsert_account({"user_id": "u1", "email": "a@b", "access_token": "t1"})
    pool.reload_accounts()
    assert len(pool._accounts) == 1
    assert pool._accounts[0]["email"] == "a@b"


def test_reload_accounts_clears_stale_active_token(iso, monkeypatch):
    from aigpt.auth import store
    pool = AccountPool(probe_fn=_alive_probe)
    pool._accounts = [_acc("tok1", 5, email="a@b")]
    pool._active_token = "tok1"
    store.upsert_account({"user_id": "u2", "email": "c@d", "access_token": "tok2"})
    pool.reload_accounts()
    assert pool._active_token is None  # tok1 no longer in the store


def test_reload_accounts_keeps_active_token_when_still_present(iso, monkeypatch):
    from aigpt.auth import store
    pool = AccountPool(probe_fn=_alive_probe)
    pool._accounts = [_acc("tok1", 5, email="a@b")]
    pool._active_token = "tok1"
    store.upsert_account({"user_id": "u1", "email": "a@b", "access_token": "tok1"})
    pool.reload_accounts()
    assert pool._active_token == "tok1"
