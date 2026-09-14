"""URL-strict reference image resolution (H4) + multi-image cap."""
from __future__ import annotations

import base64
import io
from unittest.mock import patch

import pytest
from PIL import Image

from aigpt.engine.refs import (
    _MAX_REF_BYTES,
    _MAX_REF_DIM,
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


# ------------------------------------------------------------------ compression

def _noisy_png_bytes(width: int, height: int) -> bytes:
    """Return raw random pixels packed as PNG - PNG noise is near-incompressible."""
    import random

    rng = random.Random(1234)
    raw = bytes(rng.getrandbits(8) for _ in range(width * height * 3))
    buf = io.BytesIO()
    Image.frombytes("RGB", (width, height), raw).save(buf, format="PNG")
    return buf.getvalue()


def test_compress_reduces_oversized_jpeg():
    import random

    rng = random.Random(7)
    raw = bytes(rng.getrandbits(8) for _ in range(3200 * 3200 * 3))
    buf = io.BytesIO()
    Image.frombytes("RGB", (3200, 3200), raw).save(buf, format="PNG")
    big = buf.getvalue()
    assert len(big) > _MAX_REF_BYTES
    refs = load_ref_images([_data_url(base64.b64encode(big).decode())])
    ref = refs[0]
    assert len(ref.data) < len(big)
    assert len(ref.data) <= _MAX_REF_BYTES
    assert ref.mime == "image/jpeg"
    with Image.open(io.BytesIO(ref.data)) as out:
        assert max(out.size) <= _MAX_REF_DIM


def test_compress_keeps_alpha_as_webp():
    import random

    rng = random.Random(11)
    raw = bytes(rng.getrandbits(8) for _ in range(3000 * 3000 * 4))
    buf = io.BytesIO()
    Image.frombytes("RGBA", (3000, 3000), raw).save(buf, format="PNG")
    big = buf.getvalue()
    assert len(big) > _MAX_REF_BYTES
    refs = load_ref_images([_data_url(base64.b64encode(big).decode())])
    ref = refs[0]
    assert ref.mime == "image/webp"
    with Image.open(io.BytesIO(ref.data)) as out:
        assert out.mode in ("RGBA", "LA") or "transparency" in out.info


def test_compress_passes_small_images_through():
    small = _noisy_png_bytes(100, 100)
    assert len(small) <= _MAX_REF_BYTES
    refs = load_ref_images([_data_url(base64.b64encode(small).decode())])
    ref = refs[0]
    assert ref.data == small
    assert ref.mime == "image/png"


def test_compress_gracefully_passes_undecodable():
    junk = b"\x89PNG\r\n\x1a\n" + b"junk" * (1024 * 1024)  # not a real PNG
    refs = load_ref_images([_data_url(base64.b64encode(junk).decode())])
    assert refs[0].data == junk
    assert refs[0].mime == "image/png"
