"""FastMCP stdio server exposing aigpt image tools."""
from __future__ import annotations

import os
import re

from mcp.server.fastmcp import FastMCP

from aigpt.types import Style, Thinking

_MAX_IMAGES = 4
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _validate_n(n: int) -> int:
    if not (1 <= int(n) <= _MAX_IMAGES):
        raise ValueError(f"n must be between 1 and {_MAX_IMAGES}, got {n}")
    return int(n)


def _validate_brand_colors(colors: list[str] | None) -> list[str] | None:
    if not colors:
        return colors
    for c in colors:
        if not _HEX_RE.match(str(c)):
            raise ValueError(f"brand_colors entries must be #RRGGBB hex, got {c!r}")
    return list(colors)

mcp = FastMCP("ai-image-gpt")


@mcp.tool()
def login_status() -> dict:
    """Check logged-in ChatGPT accounts. Cheap + hint-based (no network probe):
    returns {authed, accounts:[{email, type, alive, restore_at}], ready_count}.
    ready_count is from persisted hints; for live quota run the CLI `aigpt accounts`."""
    from aigpt.auth.pool import AccountPool
    rows = AccountPool().status()  # hints only, no network
    return {
        "authed": len(rows) > 0,
        "accounts": [{"email": r["email"], "type": r["type"],
                      "alive": r["alive"], "restore_at": r["restore_at"]} for r in rows],
        "ready_count": sum(1 for r in rows if r["alive"]),
    }


@mcp.tool()
def generate_image(prompt: str, aspect: str = "16:9", n: int = 1,
                   out_dir: str = "out", enhance: bool = True,
                   style: Style = "auto", thinking: Thinking = "auto",
                   brand_colors: list[str] | None = None,
                   reserve_corner: str | None = None) -> dict:
    """Generate image(s) from a text prompt at the given aspect ratio
    (16:9, 1:1, 3:4, 4:3, 9:16, or WxH). Generates ONCE and returns the exact
    ABSOLUTE file path(s) saved, e.g. {"paths": ["C:/.../img-....png"]}. Callers
    should use the returned path directly and never re-generate to "find" the file.

    Images are saved into out_dir (created if missing) as img-<timestamp>-<i>.png.

    When enhance is True (default), the prompt is auto-expanded via the ChatGPT
    text path before drawing. style='slide' = clean editorial look; style='fintech'
    = light-blue dashboard look; style='auto' is the general default.

    thinking sets reasoning effort: 'auto' (ChatGPT default) or 'standard'/
    'extended'/'max' (increasing). Higher effort improves rendered-text fidelity
    (e.g. Vietnamese diacritics) at the cost of speed.

    brand_colors (list of hex like ['#10B981']) forces a palette; reserve_corner
    (e.g. 'top-left') keeps a corner clear for a logo and bans model-drawn
    logos/text. With enhance=False these still apply via the offline template."""
    from aigpt.engine.generate import generate_image as _gen
    paths = _gen(prompt, aspect=aspect, n=_validate_n(n), out_dir=out_dir,
                 enhance=enhance, style=style, thinking=thinking,
                 brand_colors=_validate_brand_colors(brand_colors),
                 reserve_corner=reserve_corner)
    return {"paths": [os.path.abspath(p) for p in paths]}


def main() -> None:
    from aigpt.console import force_utf8
    force_utf8()  # UTF-8 stderr so non-ASCII progress lines / paths never crash a cp1252 console
    mcp.run()


if __name__ == "__main__":
    main()
