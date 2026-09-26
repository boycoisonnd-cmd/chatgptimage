"""FastMCP stdio server exposing ChatGPT and Antigravity image tools."""
from __future__ import annotations

import os
import re
import secrets
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from aigpt.types import Mode, Provider, Quality, Resolution, Style, Thinking

_MAX_IMAGES = 4
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_MIME_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


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


def _configured_provider(value: Provider) -> str:
    """Resolve provider while keeping old MCP calls ChatGPT-compatible."""
    requested = str(value or "auto").strip().lower()
    if requested == "auto":
        requested = os.environ.get("AIGPT_MCP_PROVIDER", "chatgpt").strip().lower()
    if requested not in {"chatgpt", "antigravity"}:
        raise ValueError("provider must be chatgpt or antigravity")
    return requested


def _save_antigravity_images(images: list[tuple[str, bytes]], out_dir: str) -> list[str]:
    """Persist Antigravity inline images as absolute paths for MCP clients."""
    target = Path(str(out_dir or "out")).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    target = target.resolve()
    stamp = int(time.time() * 1000)
    paths: list[str] = []
    for index, (mime, data) in enumerate(images):
        ext = _MIME_EXT.get(str(mime).lower(), ".png")
        path = target / f"img-{stamp}-{index}-{secrets.token_hex(3)}{ext}"
        path.write_bytes(data)
        paths.append(str(path.resolve()))
    return paths

mcp = FastMCP("ai-image-gpt")


@mcp.tool()
def login_status() -> dict:
    """Check both local account pools without returning credentials.

    The legacy ChatGPT fields remain at the top level. The ``providers`` map
    adds Antigravity status; this only reads persisted hints and never returns
    access or refresh tokens.
    """
    from aigpt.auth.pool import AccountPool
    rows = AccountPool().status()  # hints only, no network
    chatgpt = {
        "authed": len(rows) > 0,
        "accounts": [{"email": r["email"], "type": r["type"],
                      "alive": r["alive"], "restore_at": r["restore_at"]} for r in rows],
        "ready_count": sum(1 for r in rows if r["alive"]),
    }
    from aigpt.antigravity import store as antigravity_store
    antigravity_rows = antigravity_store.load()
    antigravity = {
        "authed": len(antigravity_rows) > 0,
        "accounts": [{"email": str(r.get("email") or ""),
                      "status": str(r.get("status") or "unknown"),
                      "models": [m.get("id") for m in (r.get("models") or [])
                                 if isinstance(m, dict) and m.get("id")]}
                     for r in antigravity_rows],
        "ready_count": sum(1 for r in antigravity_rows
                            if str(r.get("status") or "") == "available"),
    }
    return {**chatgpt, "antigravity": antigravity,
            "providers": {"chatgpt": chatgpt, "antigravity": antigravity}}


@mcp.tool()
def generate_image(prompt: str, aspect: str = "16:9", n: int = 1,
                   out_dir: str = "out", enhance: bool = True,
                   style: Style = "auto", thinking: Thinking = "auto",
                   ref_image: str | None = None,
                   ref_images: list[str] | None = None,
                   mode: Mode | None = None,
                   brand_colors: list[str] | None = None,
                   reserve_corner: str | None = None,
                   quality: Quality = "auto",
                   transparent: bool = False,
                   conversation_id: str | None = None,
                   provider: Provider = "auto", model: str = "",
                   resolution: Resolution = "2K") -> dict:
    """Generate or edit image(s), returning the exact ABSOLUTE file path(s)
    saved, e.g. {"paths": ["C:/.../img-....png"]}. Callers should use the
    returned path directly and never re-generate to "find" the file.

    Images are saved into out_dir (created if missing) as img-<timestamp>-<i>.png.

    mode discriminator (inferred when omitted; refs present -> "edit"):
      - "generate" (default): pure text-to-image. Rejects ref_images.
      - "edit": 1 reference = edit it in place (keep identity/layout); 2-4
        references = compose them. enhance defaults to False so the edit
        instruction is not rewritten into a text-to-image prompt.
      - "style": match the reference's design style (palette/layout/type/mood);
        content is NOT copied. Best for look-alike generations.

    ref_image is an alias for a single ref_images[0]. ref_images entries are
    https:// URLs (public images) or base64 data:image/png|jpeg|webp URLs (local
    uploads). Local file paths are NOT accepted here; the CLI reads local files.

    aspect: '16:9', '1:1', '3:4', '4:3', '9:16', custom 'WxH', or 'source' to
    derive the ratio from the first reference image (default for edit).

    thinking sets reasoning effort: 'auto' (ChatGPT default) or 'standard'/
    'extended'/'max' (increasing). Higher effort improves rendered-text fidelity
    (e.g. Vietnamese diacritics) at the cost of speed.

    brand_colors (list of hex like ['#10B981']) forces a palette; reserve_corner
    (e.g. 'top-left') keeps a corner clear for a logo and bans model-drawn
    logos/text. With enhance=False these still apply via the offline template.

    conversation_id continues an earlier generation in the same conversation
    (Phase 3 follow-up). Pass the conversation_id returned by a previous call
    to refine it ("make it blue"). Requires n=1. If the backend rejects it,
    the call automatically falls back to a fresh generation — but only when
    reference images are attached; without refs it raises a clear error
    instead of spending quota on a meaningless image.

    provider is ``chatgpt`` or ``antigravity``. When omitted, the server uses
    ChatGPT for backwards compatibility unless AIGPT_MCP_PROVIDER is set.
    Antigravity uses model/resolution and its own OAuth account pool; it does
    not receive ChatGPT-only thinking, style, enhance, or conversation fields.
    """
    selected_provider = _configured_provider(provider)
    if selected_provider == "antigravity":
        if conversation_id:
            raise ValueError("conversation_id is only supported by ChatGPT")
        refs = list(ref_images or [])
        if ref_image:
            refs.insert(0, ref_image)
        if len(refs) > _MAX_IMAGES:
            raise ValueError(f"ref_images must contain at most {_MAX_IMAGES} images")
        from aigpt.antigravity.service import get_service
        result = get_service().generate({
            "prompt": prompt,
            "model": model,
            "aspect": aspect,
            "resolution": resolution,
            "n": _validate_n(n),
            "ref_images": refs,
        })
        return {
            "paths": _save_antigravity_images(result["images"], out_dir),
            "conversation_id": "",
            "provider": "antigravity",
            "model": result.get("model", model),
            "resolution": result.get("resolution", resolution),
            "account_email": result.get("account_email", ""),
        }

    from aigpt.engine.generate import generate_image as _gen
    from aigpt.engine.translate import sanitize_engine_error
    try:
        res = _gen(prompt, aspect=aspect, n=_validate_n(n), out_dir=out_dir,
                   enhance=enhance, style=style, thinking=thinking,
                   ref_image=ref_image, ref_images=ref_images, mode=mode,
                   brand_colors=_validate_brand_colors(brand_colors),
                   reserve_corner=reserve_corner, quality=quality,
                   transparent=transparent, conversation_id=conversation_id)
    except ValueError:
        raise  # validation errors are already English, no CJK
    except Exception as exc:
        # Strip CJK from vendor/upstream messages so the MCP client never sees
        # e.g. "ChatGPT 生图超时…".
        raise RuntimeError(sanitize_engine_error(str(exc),
                                                 fallback="image generation failed")) from exc
    return {
        "paths": [os.path.abspath(p) for p in res.paths],
        "conversation_id": res.conversation_id,
        "provider": "chatgpt",
        "model": "GPT Image 2",
    }


@mcp.tool()
def list_image_models(provider: Provider = "auto") -> dict:
    """List image models available to the selected provider.

    ChatGPT currently exposes the fixed GPT Image 2 entry. Antigravity is
    queried through its own account pool and returns the union of discovered
    image models without exposing credentials.
    """
    selected_provider = _configured_provider(provider)
    if selected_provider == "chatgpt":
        return {"provider": "chatgpt", "models": [{
            "id": "GPT Image 2", "display_name": "GPT Image 2",
        }], "default_model": "GPT Image 2"}
    from aigpt.antigravity.service import get_service
    return {"provider": "antigravity", **get_service().models()}


def main() -> None:
    from aigpt.console import force_utf8
    force_utf8()  # UTF-8 stderr so non-ASCII progress lines / paths never crash a cp1252 console
    configured_key = os.environ.get("AIGPT_MCP_API_KEY", "").strip()
    if configured_key:
        from aigpt import mcp_key
        if not mcp_key.verify(configured_key):
            raise SystemExit("invalid AIGPT_MCP_API_KEY")
    mcp.run()


if __name__ == "__main__":
    main()
