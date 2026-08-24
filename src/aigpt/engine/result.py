"""Result object for image generation.

Used to carry generated file paths plus the conversation id the engine returns
(so callers can, after a follow-up spike, continue a thread). Kept immutable.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GenerateResult:
    """Paths saved (absolute PNGs) + the upstream conversation id (may be empty)."""

    paths: tuple[str, ...]
    conversation_id: str = ""

    @classmethod
    def from_list(cls, paths: list[str], conversation_id: str = "") -> GenerateResult:
        return cls(tuple(paths), conversation_id)
