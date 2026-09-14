"""Resolve reference images for image-to-image generation.

Accepts either:
- `https://` URLs (public images, fetched over https via curl_cffi)
- `data:image/{png,jpeg,webp};base64,...` URLs (local uploads from the extension)

Local file paths are REJECTED (H4): a URL parameter must never double as an
arbitrary local file read. The CLI converts local files to data: URLs in-process.

Cap on the number of references mirrors the engine's `n` cap (1-4); the vendored
backend would accept more, but exposing it invites quota abuse.
"""
from __future__ import annotations

import base64
from io import BytesIO
from typing import NamedTuple
from urllib.parse import urlparse

from curl_cffi import requests as curl_requests
from PIL import Image

_MAX_REFS = 4
_SUPPORTED = ("png", "jpeg", "webp")
_MIME = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp"}
_DATA_URL_RE_PREFIX = "data:image/"
# Reference images above this decoded size get re-encoded smaller before they
# leave the process (4 edit refs at 2 MiB each ≈ 11 MiB of base64 JSON, inside
# the API body cap). _MAX_DIM keeps the pixel budget sane for the model.
_MAX_REF_BYTES = 2 * 1024 * 1024
_MAX_REF_DIM = 2048
_COMPRESS_QUALITIES = (85, 60, 40)


class RefImage(NamedTuple):
    """A decoded reference image ready for encode_images()."""

    data: bytes
    mime: str
    name: str


def _maybe_compress(data: bytes, mime: str) -> tuple[bytes, str, str]:
    """Re-encode an oversized reference image to stay under _MAX_REF_BYTES.

    Returns (data, mime, name). Small images pass through untouched; large ones
    are downscaled to _MAX_REF_DIM and re-encoded (WebP keeps transparency,
    JPEG otherwise), stepping quality down until the byte budget fits.
    """
    if len(data) <= _MAX_REF_BYTES:
        return data, mime, "ref." + mime.split("/")[1]
    try:
        with Image.open(BytesIO(data)) as image:
            image.load()
            if _MAX_REF_DIM < max(image.size):
                image.thumbnail((_MAX_REF_DIM, _MAX_REF_DIM), Image.LANCZOS)
            has_alpha = image.mode in ("RGBA", "LA") or (
                image.mode == "P" and "transparency" in image.info
            )
            out_mime = "image/webp" if has_alpha else "image/jpeg"
            out_ext = "webp" if has_alpha else "jpg"
            if not has_alpha and image.mode != "RGB":
                image = image.convert("RGB")
            for quality in _COMPRESS_QUALITIES:
                buf = BytesIO()
                image.save(buf, format=out_mime.split("/")[1], quality=quality)
                out = buf.getvalue()
                if len(out) <= _MAX_REF_BYTES:
                    return out, out_mime, "ref." + out_ext
            # Last resort: accept the smallest quality even if still oversized.
            return out, out_mime, "ref." + out_ext
    except Exception:
        # Un-decodable bytes: pass through and let the upstream fail loudly.
        return data, mime, "ref." + mime.split("/")[1]


def _decode_data_url(ref_image: str) -> RefImage:
    """Decode a `data:image/<kind>;base64,<b64>` URL into bytes+mime."""
    if not ref_image.startswith("data:"):
        raise ValueError(
            f"ref_image must be an https:// URL or data:image URL, "
            f"got {ref_image!r} (local file paths are not supported)")
    if not ref_image.startswith(_DATA_URL_RE_PREFIX):
        raise ValueError(
            "ref_image data: URL must be base64 png/jpeg/webp")
    kind, _, b64 = ref_image[len("data:"):].partition(";base64,")
    if kind.startswith("image/"):
        kind = kind.removeprefix("image/")
    if kind not in _SUPPORTED or not b64:
        raise ValueError(
            "ref_image data: URL must be base64 png/jpeg/webp")
    try:
        data = base64.b64decode(b64, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid ref_image data URL: {exc}") from exc
    if not data:
        raise ValueError("ref_image data: URL is empty")
    return RefImage(*_maybe_compress(data, _MIME[kind]))


def _fetch_https(ref_image: str) -> RefImage:
    """Fetch a public https image and return its bytes + mime."""
    parsed = urlparse(ref_image)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(
            f"ref_image must be an https:// URL or data:image URL, "
            f"got {ref_image!r} (local file paths are not supported)"
        )
    try:
        resp = curl_requests.get(ref_image, timeout=30,
                                 headers={"User-Agent": "aigpt-mcp/0.1"})
        resp.raise_for_status()
    except Exception as exc:
        raise RuntimeError(
            f"failed to fetch ref_image {ref_image!r}: {exc}") from exc
    data = resp.content
    ctype = str(resp.headers.get("Content-Type") or "")
    if ctype.startswith("image/jpeg"):
        mime = "image/jpeg"
    elif ctype.startswith("image/png"):
        mime = "image/png"
    elif ctype.startswith("image/webp"):
        mime = "image/webp"
    else:
        mime = "image/jpeg"  # engine default
    return RefImage(*_maybe_compress(data, mime))


def load_ref_images(ref_images: list[str]) -> list[RefImage]:
    """Resolve a list of reference images (https or data: URLs).

    Raises ValueError on too many refs, a local path, an unsupported scheme,
    or a malformed data: URL. Never reads a local file.
    """
    if len(ref_images) > _MAX_REFS:
        raise ValueError(f"too many ref_images: {len(ref_images)} > {_MAX_REFS}")
    out: list[RefImage] = []
    for ref in ref_images:
        if not isinstance(ref, str) or not ref.strip():
            raise ValueError("each ref_image must be a non-empty string")
        if ref.startswith(_DATA_URL_RE_PREFIX):
            out.append(_decode_data_url(ref))
        else:
            out.append(_fetch_https(ref))
    return out
