"""Unit tests for sizes.resolve_size - pure mapping, no network."""
from __future__ import annotations

import pytest

from aigpt.sizes import resolve_size


def test_known_aspects_map_to_native_dims():
    assert resolve_size("16:9") == "1920x1080"
    assert resolve_size("1:1") == "1024x1024"
    assert resolve_size("3:4") == "1024x1536"
    assert resolve_size("9:16") == "1080x1920"
    assert resolve_size("4:3") == "1440x1080"


def test_aspect_case_and_spacing_insensitive():
    assert resolve_size(" 16:9 ") == "1920x1080"
    assert resolve_size("4:3") == resolve_size("4 : 3")


def test_raw_resolution_passthrough():
    assert resolve_size("800x600") == "800x600"
    assert resolve_size("1920x1080") == "1920x1080"


def test_unknown_aspect_raises():
    with pytest.raises(ValueError):
        resolve_size("foo")
    with pytest.raises(ValueError):
        resolve_size("16x9")  # only WxH with >=2 digits each side is raw
