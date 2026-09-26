from __future__ import annotations

import json

from aigpt import mcp_key
from aigpt.auth import tokens


def test_mcp_api_key_is_one_time_and_hash_only(tmp_path, monkeypatch):
    monkeypatch.setattr(tokens, "_config_dir", lambda: tmp_path)

    assert mcp_key.status()["configured"] is False
    value, public = mcp_key.create()

    assert value.startswith("aigpt_")
    assert public["configured"] is True
    assert public["prefix"] == value[:14]
    assert mcp_key.verify(value) is True
    assert mcp_key.verify(value + "wrong") is False

    raw = json.loads((tmp_path / "mcp_api_key.json").read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert raw["key_hash"] != value
    assert value not in json.dumps(raw)

    assert mcp_key.revoke() is True
    assert mcp_key.verify(value) is False
    assert mcp_key.status()["configured"] is False
    assert mcp_key.revoke() is False
