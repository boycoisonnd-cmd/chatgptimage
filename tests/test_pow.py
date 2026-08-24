"""Unit tests for the vendored proof-of-work helper (pure sha3_512 loop).

Difficulty is expressed as a hex string; the first `len//2` digest bytes must
be <= the target. A trivial difficulty (00..01) keeps the solve fast.
"""
from __future__ import annotations

import hashlib

import pybase64

import aigpt._vendor_path  # noqa: F401
from utils.pow import _pow_generate, build_pow_config


def test_pow_solves_trivial_difficulty():
    config = build_pow_config("test-ua", script_sources=["https://chatgpt.com/x.js"], data_build="")
    answer, solved = _pow_generate("seed-123", "01", config, limit=200_000)
    assert solved is True
    assert answer  # base64 payload


def test_pow_digest_matches_difficulty():
    config = build_pow_config("test-ua", script_sources=["https://chatgpt.com/x.js"])
    difficulty = "0001"  # first 2 bytes must be <= 0x0001
    answer, solved = _pow_generate("seed-xyz", difficulty, config, limit=200_000)
    assert solved
    _decode_answer(answer, config)  # round-trips through the same splices
    digest = hashlib.sha3_512(b"seed-xyz" + answer.encode()).digest()
    assert digest[:2] <= bytes.fromhex(difficulty)


def test_pow_returns_fallback_when_unsolvable():
    config = build_pow_config("test-ua", script_sources=["https://chatgpt.com/x.js"])
    answer, solved = _pow_generate("s", "0000", config, limit=2)  # impossible target
    assert solved is False
    assert answer.startswith("wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D")  # fallback prefix


def test_pow_deterministic_payload_shape():
    config = build_pow_config("test-ua", script_sources=["https://chatgpt.com/x.js"])
    answer, solved = _pow_generate("k", "01", config, limit=200_000)
    assert solved
    assert _decode_answer(answer, config)  # round-trips through the same splices


def _decode_answer(answer: str, config: list) -> bytes:
    """Reconstruct the JSON the loop signed (mirrors _pow_generate's splices)."""
    static_1 = __import__("json").dumps(config[:3], separators=(",", ":"), ensure_ascii=False)[:-1] + ","
    static_2 = "," + __import__("json").dumps(config[4:9], separators=(",", ":"), ensure_ascii=False)[1:-1] + ","
    static_3 = "," + __import__("json").dumps(config[10:], separators=(",", ":"), ensure_ascii=False)[1:]
    # The answer is base64 of (static_1 + i + static_2 + (i>>1) + static_3); we
    # can't recover i from the answer alone, so just assert the shape parses.
    decoded = pybase64.b64decode(answer)
    assert decoded.startswith(static_1.encode())
    assert static_2.encode() in decoded
    assert static_3.encode() in decoded
    return decoded
