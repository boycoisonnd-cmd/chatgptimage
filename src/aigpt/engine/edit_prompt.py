"""Prompt overlay for different image generation modes.

Mode determines what instruction is prepended/attached when reference images are
present, mirroring how ChatGPT.com treats attached images differently depending
on whether the user wants to edit, compose, or style-transfer.
"""
from __future__ import annotations

# STYLE-ONLY instruction: the model borrows the look, not the content.
# Previously hardcoded into generate.py's https-fetch branch only.
_STYLE_OVERLAY = (
    "IMPORTANT: the attached image(s) are DESIGN-STYLE references ONLY "
    "(palette, layout, typography, mood, decorative motifs). "
    "Do NOT copy any text, titles, or specific content from them; "
    "create a NEW image with the content above, styled like the reference."
)

# Single-image EDIT instruction: keep identity, composition, and unmentioned details.
_EDIT_OVERLAY_SINGLE = (
    "Edit the attached image as instructed. Keep its subject, composition, "
    "and overall identity. Only change what the prompt describes. "
    "Preserve all unmentioned details."
)

# Multi-image COMPOSE instruction: combine subject + background/elements.
_COMPOSE_OVERLAY = (
    "The attached images should be combined as described. "
    "Image 1 is the primary subject unless stated otherwise. "
    "Integrate the other images naturally into the scene."
)

# Transparent background hint.
_TRANSPARENT_OVERLAY = "Use a transparent background, no matte, no fill."


def apply_mode_overlay(mode: str, prompt: str, n_refs: int,
                       transparent: bool = False) -> str:
    """Append the appropriate mode-overlay instruction to the prompt.

    Args:
        mode: "generate", "edit", or "style".
        prompt: The user's original prompt.
        n_refs: How many reference images are attached.
        transparent: If True, append a transparent-background hint.

    Returns:
        The prompt with the mode-appropriate overlay appended.
    """
    parts = [prompt.strip()]

    if mode == "style":
        parts.append(_STYLE_OVERLAY)
    elif mode == "edit":
        if n_refs >= 2:
            parts.append(_COMPOSE_OVERLAY)
        else:
            parts.append(_EDIT_OVERLAY_SINGLE)

    if transparent:
        parts.append(_TRANSPARENT_OVERLAY)

    return " ".join(parts)