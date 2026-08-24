"""Unit tests for the versioned auth store (auth.json v2).

Disk isolation via APPDATA monkeypatch (tokens._config_dir reads it). No network.
"""
from __future__ import annotations

import json

import pytest

from aigpt.auth import store, tokens


@pytest.fixture
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(tokens, "_config_dir", lambda: tmp_path)
    return tmp_path


def test_empty_store_loads_empty(iso):
    assert store.load_accounts() == []


def test_upsert_dedups_by_user_id(iso):
    store.upsert_account({"user_id": "u1", "email": "a@b", "access_token": "t1"})
    store.upsert_account({"user_id": "u1", "email": "a@c", "access_token": "t2"})
    rows = store.load_accounts()
    assert len(rows) == 1
    assert rows[0]["email"] == "a@c"  # later payload wins, merged
    assert rows[0]["access_token"] == "t2"


def test_upsert_falls_back_to_refresh_token_when_no_user_id(iso):
    store.upsert_account({"access_token": "t1", "refresh_token": "r1"})
    store.upsert_account({"access_token": "t1", "refresh_token": "r1"})
    assert len(store.load_accounts()) == 1
    store.upsert_account({"access_token": "t2", "refresh_token": "r2"})
    assert len(store.load_accounts()) == 2


def test_upsert_survives_access_token_rotation_no_duplicate(iso):
    # Regression for H2: refresh_for() rotates access_token; a re-login with an
    # empty user_id (probe failed at login) must still merge onto the SAME row
    # via the stable refresh_token - never append a duplicate.
    store.upsert_account({"user_id": "", "access_token": "old-tok", "refresh_token": "r1"})
    store.upsert_account({"user_id": "", "access_token": "new-tok", "refresh_token": "r1"})
    rows = store.load_accounts()
    assert len(rows) == 1
    assert rows[0]["access_token"] == "new-tok"  # later payload wins
    assert rows[0]["refresh_token"] == "r1"


def test_save_is_v2_and_atomic(iso):
    store.save_accounts([{"access_token": "t1"}])
    raw = json.loads((iso / "auth.json").read_text(encoding="utf-8"))
    assert raw["version"] == 2
    assert len(raw["accounts"]) == 1
    assert not (iso / "auth.json.tmp").exists()  # temp cleaned up


def test_legacy_flat_migrates_once(iso):
    p = iso / "auth.json"
    p.write_text(json.dumps({"access_token": "legacy", "email": "old@x"}), encoding="utf-8")
    rows = store.load_accounts()
    assert len(rows) == 1
    assert rows[0]["access_token"] == "legacy"
    assert rows[0]["user_id"] == ""  # legacy never stored it
    # migrated shape persisted
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw["version"] == 2


def test_corrupt_file_returns_empty_without_raise(iso):
    (iso / "auth.json").write_text("{not json", encoding="utf-8")
    assert store.load_accounts() == []


def test_remove_account_by_user_id_or_email(iso):
    store.upsert_account({"user_id": "u1", "email": "a@b", "access_token": "t1"})
    store.upsert_account({"user_id": "u2", "email": "c@d", "access_token": "t2"})
    assert store.remove_account("a@b") == 1
    assert store.remove_account("u2") == 1
    assert store.load_accounts() == []


def test_remove_all(iso):
    store.upsert_account({"user_id": "u1", "access_token": "t1"})
    store.remove_all()
    assert store.load_accounts() == []
