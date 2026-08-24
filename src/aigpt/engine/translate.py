"""Sanitize vendor engine error messages — strip CJK so user-facing surfaces
never show Chinese text (e.g. "ChatGPT 生图超时"). The vendored engine is
byte-identical per VENDOR_REV, so we intercept at the boundary.

Only the aigpt layer calls this; _vendor/ is never touched.
"""
from __future__ import annotations

import re

# Matches any CJK Unified Ideograph (U+4E00–U+9FFF).
_CJK_RE = re.compile(r"[一-鿿]+")


def sanitize_engine_error(text: str, *, fallback: str) -> str:
    """Strip CJK from a vendor error message.

    If the text contains no CJK, return it unchanged.
    If it contains CJK, extract any meaningful English/ASCII fragments
    (e.g. "token invalidated", "upstream connection timed out").
    If nothing English remains, return *fallback*.
    """
    if not text:
        return fallback
    if not _CJK_RE.search(text):
        return text
    # Collect ASCII fragments that look like English phrases (≥2 words),
    # not single tokens like "ChatGPT", "HTTP", "config.json".
    fragments = re.findall(r"[A-Za-z][A-Za-z0-9'_-]*\s+[A-Za-z][A-Za-z0-9'_-]*(?:\s+[A-Za-z][A-Za-z0-9'_-]*)*", text)
    meaningful = [f.strip() for f in fragments if len(f.strip()) >= 5]
    if meaningful:
        return " ".join(meaningful)
    return fallback