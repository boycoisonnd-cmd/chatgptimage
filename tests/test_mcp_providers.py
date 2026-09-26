"""MCP provider selection and Antigravity path persistence tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from aigpt import server


def test_provider_selection_preserves_chatgpt_compatibility(monkeypatch):
    monkeypatch.delenv("AIGPT_MCP_PROVIDER", raising=False)
    assert server._configured_provider("auto") == "chatgpt"
    monkeypatch.setenv("AIGPT_MCP_PROVIDER", "antigravity")
    assert server._configured_provider("auto") == "antigravity"
    assert server._configured_provider("chatgpt") == "chatgpt"
    with pytest.raises(ValueError, match="provider"):
        server._configured_provider("invalid")


def test_antigravity_mcp_generation_saves_absolute_paths(monkeypatch, tmp_path):
    import aigpt.antigravity.service as antigravity_service

    captured = {}

    class FakeService:
        def generate(self, payload):
            captured.update(payload)
            return {
                "images": [("image/png", b"fake-png")],
                "account_email": "ag@example.com",
                "model": "gemini- image-gen",
                "resolution": "2K",
            }

    monkeypatch.setattr(antigravity_service, "get_service", lambda: FakeService())
    result = server.generate_image(
        "a test image", provider="antigravity", model="gemini- image-gen",
        resolution="2K", ref_images=["data:image/png;base64,ZmFrZQ=="],
        out_dir=str(tmp_path),
    )

    assert result["provider"] == "antigravity"
    assert result["resolution"] == "2K"
    assert result["account_email"] == "ag@example.com"
    assert captured["ref_images"] == ["data:image/png;base64,ZmFrZQ=="]
    path = Path(result["paths"][0])
    assert path.is_absolute()
    assert path.read_bytes() == b"fake-png"


def test_antigravity_rejects_chatgpt_conversation(monkeypatch):
    with pytest.raises(ValueError, match="only supported by ChatGPT"):
        server.generate_image("edit", provider="antigravity", conversation_id="conv-1")
