"""URL-strict reference image resolution (H4) + multi-image cap."""
from __future__ import annotations

import base64
from unittest.mock import patch

import pytest

from aigpt.engine.refs import (
    _MAX_REFS,
    load_ref_images,
)


def _data_url(b64: str, kind: str = "png") -> str:
    return f"data:image/{kind};base64,{b64}"


def test_data_url_decodes():
    b64 = base64.b64encode(b"\x89PNG fake").decode()
    refs = load_ref_images([_data_url(b64)])
    assert len(refs) == 1
    assert refs[0].data == b"\x89PNG fake"
    assert refs[0].mime == "image/png"


def test_data_url_jpeg_and_webp():
    for kind, mime in (("jpeg", "image/jpeg"), ("webp", "image/webp")):
        b64 = base64.b64encode(b"data").decode()
        refs = load_ref_images([_data_url(b64, kind)])
        assert refs[0].mime == mime


def test_data_url_rejects_non_image():
    with pytest.raises(ValueError, match="data:image URL"):
        load_ref_images(["data:text/html;base64,PGI+"])


def test_data_url_rejects_bad_base64():
    with pytest.raises(ValueError, match="invalid ref_image data URL"):
        load_ref_images([_data_url("not!!base64")])


def test_data_url_rejects_empty():
    with pytest.raises(ValueError, match="png/jpeg/webp"):
        load_ref_images([_data_url("")])


def test_local_path_rejected():
    with pytest.raises(ValueError, match="https:// URL"):
        load_ref_images(["C:/Users/me/photo.png"])


def test_http_and_file_urls_rejected():
    for bad in ("http://example.com/img.png", "file:///C:/photo.png",
                "ftp://example.com/img.png"):
        with pytest.raises(ValueError, match="https:// URL"):
            load_ref_images([bad])


def test_empty_or_blank_rejected():
    with pytest.raises(ValueError, match="non-empty"):
        load_ref_images([""])
    with pytest.raises(ValueError, match="non-empty"):
        load_ref_images(["  "])
    with pytest.raises(ValueError, match="non-empty"):
        load_ref_images([42])


def test_too_many_refs_rejected():
    refs = [_data_url(base64.b64encode(b"x").decode()) for _ in range(_MAX_REFS)]
    refs.append(_data_url(base64.b64encode(b"y").decode()))
    with pytest.raises(ValueError, match="too many ref_images"):
        load_ref_images(refs)


def test_mixed_https_and_data():
    from typing import ClassVar

    class FakeResp:
        content = b"\x89PNG\r\n\x1a\nfakedata"
        headers: ClassVar[dict[str, str]] = {"Content-Type": "image/png"}

        def raise_for_status(self):
            return None

    with patch("aigpt.engine.refs.curl_requests.get", return_value=FakeResp()):
        refs = load_ref_images([
            "https://example.com/a.png",
            _data_url(base64.b64encode(b"local").decode()),
        ])
    assert len(refs) == 2
    assert refs[0].mime == "image/png"
    assert refs[0].data.startswith(b"\x89PNG")
    assert refs[1].data == b"local"


def test_fetch_failure_raises():
    class Boom:
        def raise_for_status(self):
            raise RuntimeError("boom")

    with patch("aigpt.engine.refs.curl_requests.get",
               return_value=Boom()) as mock_get, pytest.raises(
                   RuntimeError, match="failed to fetch"):
        load_ref_images(["https://example.com/a.png"])
    mock_get.assert_called_once_with("https://example.com/a.png", timeout=30,
                                     headers={"User-Agent": "aigpt-mcp/0.1"})


def test_mime_defaults_to_jpeg_for_unknown():
    from typing import ClassVar

    class FakeResp:
        content = b"x"
        headers: ClassVar[dict[str, str]] = {"Content-Type": "application/octet-stream"}

        def raise_for_status(self):
            return None

    with patch("aigpt.engine.refs.curl_requests.get", return_value=FakeResp()):
        refs = load_ref_images(["https://example.com/img"])
    assert refs[0].mime == "image/jpeg"
