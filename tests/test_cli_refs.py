"""CLI --ref handling: local file -> data: URL, https passthrough, errors."""
from __future__ import annotations

import pytest

from aigpt.cli import _local_to_data_url, _resolve_refs


def test_local_png_to_data_url(tmp_path):
    img = tmp_path / "photo.png"
    img.write_bytes(b"\x89PNG fake")
    url = _local_to_data_url(str(img))
    assert url.startswith("data:image/png;base64,")


def test_local_jpeg_and_webp(tmp_path):
    for name, mime in (("a.jpg", "image/jpeg"), ("b.webp", "image/webp")):
        img = tmp_path / name
        img.write_bytes(b"x")
        url = _local_to_data_url(str(img))
        assert url.startswith("data:" + mime + ";base64,")


def test_local_unsupported_type_rejected(tmp_path):
    f = tmp_path / "x.gif"
    f.write_bytes(b"x")
    with pytest.raises(ValueError, match="png/jpeg/webp"):
        _local_to_data_url(str(f))


def test_local_missing_file_rejected(tmp_path):
    with pytest.raises(FileNotFoundError, match="not found"):
        _local_to_data_url(str(tmp_path / "nope.png"))


def test_resolve_refs_mixed(tmp_path):
    img = tmp_path / "photo.png"
    img.write_bytes(b"\x89PNG fake")
    out = _resolve_refs(["https://example.com/a.png", str(img)])
    assert out[0] == "https://example.com/a.png"
    assert out[1].startswith("data:image/png;base64,")


def test_resolve_refs_too_many_exits(tmp_path):
    with pytest.raises(SystemExit) as e:
        _resolve_refs([f"https://example.com/{i}.png" for i in range(5)])
    assert e.value.code == 2


def test_resolve_refs_missing_file_exits(tmp_path):
    with pytest.raises(SystemExit) as e:
        _resolve_refs([str(tmp_path / "missing.png")])
    assert e.value.code == 2
