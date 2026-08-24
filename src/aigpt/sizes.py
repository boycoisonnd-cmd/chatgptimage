"""Map a friendly aspect ratio to the size string ChatGPT honors.
ChatGPT normalizes to its own native dims; the RATIO is what matters."""
from __future__ import annotations

import re

_ASPECTS = {
    "16:9": "1920x1080",
    "1:1": "1024x1024",
    "3:4": "1024x1536",
    "9:16": "1080x1920",
    "4:3": "1440x1080",
}
_RAW = re.compile(r"^\d{2,5}x\d{2,5}$")

# Source-aspect support: ratio is matched to the nearest built-in.
# A tolerance of 5% avoids jumping between e.g. 1:1 and 4:3 for near-square uploads.
_RATIO_TOLERANCE = 0.05


def aspect_from_pixels(width: int, height: int) -> str:
    """Return the nearest built-in aspect for a pixel dimension (e.g. 1920x1080 -> "16:9")."""
    if width <= 0 or height <= 0:
        raise ValueError(f"invalid dimensions {width}x{height}")
    ratio = width / height
    best, best_dist = "16:9", float("inf")
    for a, size in _ASPECTS.items():
        aw, ah = (int(x) for x in size.split("x"))
        a_ratio = aw / ah
        dist = abs(a_ratio - ratio)
        if dist < best_dist:
            best, best_dist = a, dist
    if best_dist <= _RATIO_TOLERANCE:
        return best
    return f"{width}x{height}"


def resolve_size(aspect: str, refs: list[tuple[int, int]] | None = None) -> str:
    a = aspect.strip().lower().replace(" ", "")
    if a in _ASPECTS:
        return _ASPECTS[a]
    if a == "source":
        if not refs:
            return _ASPECTS["16:9"]  # no source image -> fall back to 16:9
        return _ASPECTS[aspect_from_pixels(refs[0][0], refs[0][1])]
    if _RAW.match(a):
        return a
    raise ValueError(f"unknown aspect {aspect!r}; use one of {list(_ASPECTS)} or WxH")
