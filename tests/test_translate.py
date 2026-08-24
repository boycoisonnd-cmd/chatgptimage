"""Sanitize vendor engine error messages — strip CJK."""
from __future__ import annotations

from aigpt.engine.translate import sanitize_engine_error


def test_all_english_passthrough():
    assert sanitize_engine_error("image generation failed", fallback="x") == "image generation failed"


def test_all_chinese_returns_fallback():
    got = sanitize_engine_error("ChatGPT 生图超时（已等待 120 秒）", fallback="image timed out")
    assert got == "image timed out"


def test_mixed_keeps_english_fragments():
    got = sanitize_engine_error("token invalidated 令牌无效", fallback="x")
    assert "token invalidated" in got
    assert "令牌" not in got


def test_empty_returns_fallback():
    assert sanitize_engine_error("", fallback="fallback") == "fallback"


def test_none_returns_fallback():
    assert sanitize_engine_error(None, fallback="fallback") == "fallback"


def test_disconnected_ascii_parts_joined():
    """Chinese text with two separate English words returns both."""
    got = sanitize_engine_error("图片 request failed: 网络错误", fallback="x")
    # "request failed" should be extracted
    assert "request failed" in got


def test_no_meaningful_ascii_returns_fallback():
    """Short ASCII fragments like 'a' or 'ok' are not meaningful."""
    got = sanitize_engine_error("发生错误", fallback="generic error")
    assert got == "generic error"