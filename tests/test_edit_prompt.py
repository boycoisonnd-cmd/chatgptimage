"""Prompt overlay per mode: generate / edit (single+compose) / style / transparent."""
from __future__ import annotations

from aigpt.engine.edit_prompt import apply_mode_overlay


def test_generate_no_overlay():
    out = apply_mode_overlay("generate", "a cat", 0)
    assert out == "a cat"


def test_edit_single_overlay_preserves_identity():
    out = apply_mode_overlay("edit", "make background purple", 1)
    assert "make background purple" in out
    assert "subject" in out
    assert "DESIGN-STYLE" not in out
    assert "composition" in out


def test_edit_multi_compose_overlay():
    out = apply_mode_overlay("edit", "put person into scene", 2)
    assert "put person into scene" in out
    assert "primary subject" in out


def test_edit_multi_compose_requires_two_or_more():
    # 2+ refs → compose; 1 ref → single edit
    assert "primary subject" in apply_mode_overlay("edit", "x", 2)
    assert "primary subject" not in apply_mode_overlay("edit", "x", 1)


def test_style_overlay_injected():
    out = apply_mode_overlay("style", "slide about coffee", 1)
    assert "DESIGN-STYLE" in out
    assert "Do NOT copy any text" in out


def test_transparent_hint_appended():
    out = apply_mode_overlay("edit", "logo", 1, transparent=True)
    assert "transparent background" in out
    no_trans = apply_mode_overlay("edit", "logo", 1)
    assert "transparent background" not in no_trans


def test_edit_keeps_instruction_intact():
    out = apply_mode_overlay("edit", "change the shirt to red", 1)
    assert "change the shirt to red" in out
