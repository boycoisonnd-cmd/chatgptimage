"""Drive the vendored ChatGPT image-gen engine with our single-account token.

Entry: generate_image(prompt, aspect, n, out_dir) -> list of saved PNG paths.
"""
from __future__ import annotations

import base64
import os
import sys
import time
from urllib.parse import urlparse

# Make the vendored `services.*` / `utils.*` importable; this also sets the
# vendored config's required auth-key env var (single source: see _vendor_path).
# MUST precede all vendored imports (sys.path side effect).
import aigpt._vendor_path  # noqa: F401
from aigpt.engine import image_thinking

# Importing this wires the multi-account pool into the vendored shim AND forces
# sequential generation (side effects on import); it also exposes the pool
# accessors used here (get_pool) and re-exported for the deck layer
# (pool_exhausted_reset).
from aigpt.engine.account_wiring import get_pool, pool_exhausted_reset  # noqa: F401
from aigpt.engine.enhance import enhance_prompt, template_enhance
from aigpt.sizes import resolve_size
from aigpt.types import Style, Thinking
from services.protocol.conversation import (
    ConversationRequest,
    encode_images,
    stream_image_outputs_with_pool,
)

_SLIDE_STYLES = ("slide", "fintech")


def _collect_saved(outputs, n: int, out_dir: str) -> tuple[list[str], str]:
    """Save up to `n` images from the engine output stream.

    The backend can return MORE variants than requested (n=1 has been seen to
    yield 2 results); cap at `n` so callers get exactly what they asked for.
    Returns (saved_paths, last_message).
    """
    saved: list[str] = []
    message = ""
    for output in outputs:
        if output.kind == "message":
            message = output.text or message
        elif output.kind == "result":
            for item in output.data:
                if len(saved) >= n:
                    break
                b64 = str(item.get("b64_json") or "").strip()
                if not b64:
                    continue
                path = os.path.join(
                    out_dir, f"img-{int(time.time() * 1000)}-{len(saved)}.png"
                )
                with open(path, "wb") as f:
                    f.write(base64.b64decode(b64))
                saved.append(path)
        if len(saved) >= n:
            break
    return saved, message


def generate_image(
    prompt: str,
    aspect: str = "16:9",
    n: int = 1,
    out_dir: str = "out",
    enhance: bool = True,
    style: Style = "auto",
    ref_image: str | None = None,
    thinking: Thinking = "auto",
    brand_colors: list[str] | None = None,
    reserve_corner: str | None = None,
) -> list[str]:
    """Generate n image(s) and save them as PNGs. Returns saved file paths.

    When enhance is True (default), the prompt is first expanded via the ChatGPT
    text path (mirrors the web UI). style="slide" applies a clean editorial
    presentation-slide aesthetic (light, restrained, one hero, short labels) —
    best for slide content.

    ref_image must be an https:// URL (public image) or a base64 data:image
    URL (local uploads). The engine runs the image-EDIT path: the model SEES
    the reference and matches its DESIGN STYLE only (palette, layout,
    typography, mood) — its text/content is NOT copied. Local file paths are
    rejected (H4): a URL parameter must never double as an arbitrary local
    file read.

    thinking selects reasoning effort before drawing: "auto" (default = ChatGPT's
    own default) or "standard"/"extended"/"max" (increasing). Higher effort
    improves rendered-text fidelity (e.g. Vietnamese diacritics) at the cost of
    speed.

    brand_colors (list of hex) forces a palette; reserve_corner (e.g. "top-left")
    keeps a corner clear for a logo and bans any model-drawn logo/text. With
    enhance=False, supplying a slide style / brand_colors / reserve_corner routes
    through the deterministic offline template (concise, no LLM bloat) instead of
    being silently ignored.
    """
    size = resolve_size(aspect)
    if enhance:
        # Fix the account for THIS slide so enhance (text) and the image share it
        # (raises NoQuotaError up-front if every account is exhausted).
        active = get_pool().current_token()
        prompt = enhance_prompt(prompt, style=style, brand_colors=brand_colors,
                                reserve_corner=reserve_corner, access_token=active)
        print(f"[enhance] prompt expanded to {len(prompt)} chars", file=sys.stderr)
    elif style in _SLIDE_STYLES or brand_colors or reserve_corner:
        prompt = template_enhance(prompt, style=style, brand_colors=brand_colors,
                                  reserve_corner=reserve_corner)
        print(f"[template] styled prompt ({len(prompt)} chars, no LLM)", file=sys.stderr)

    encoded: list[str] | None = None
    if ref_image:
        # URL-strict (H4): only public https URLs, never a local path. The bytes
        # are fetched over https (curl_cffi, same lib the engine uses) then
        # encoded - a URL parameter can never double as a local file read.
        # A base64 data:image URL (the extension's local uploads) is decoded
        # directly - no fetch, no local-file access.
        parsed = urlparse(ref_image)
        if ref_image.startswith("data:image/"):
            # data:image/<kind>;base64,<b64>
            try:
                kind, _, b64 = ref_image[len("data:"):].partition(";base64,")
                data = base64.b64decode(b64, validate=True)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"invalid ref_image data: URL: {exc}") from exc
            if kind.startswith("image/"):
                kind = kind.removeprefix("image/")
            if kind not in ("png", "jpeg", "webp") or not data:
                raise ValueError(
                    "ref_image data: URL must be base64 png/jpeg/webp")
            mime = {"png": "image/png", "jpeg": "image/jpeg",
                    "webp": "image/webp"}[kind]
        elif parsed.scheme != "https" or not parsed.netloc:
            raise ValueError(
                f"ref_image must be an https:// URL or data:image URL, "
                f"got {ref_image!r} (local file paths are not supported)"
            )
        else:
            # Strong STYLE-ONLY instruction so the model borrows the look, not the words.
            prompt = (
                prompt
                + " QUAN TRỌNG: Ảnh đính kèm CHỈ là tham chiếu PHONG CÁCH THIẾT KẾ "
                "(bảng màu, bố cục, kiểu chữ, không khí, hoạ tiết trang trí). TUYỆT ĐỐI "
                "KHÔNG sao chép chữ, tiêu đề, hay nội dung cụ thể trong ảnh tham chiếu. "
                "Hãy tạo slide MỚI với nội dung đã cho ở trên, mang phong cách giống ảnh "
                "tham chiếu. (IMPORTANT: the attached image is a DESIGN-STYLE reference "
                "ONLY — palette, layout, typography, mood, decorative motifs. Do NOT copy "
                "any text, titles, or specific content from it; create a NEW slide with "
                "the content above, styled like the reference.)"
            )
            from curl_cffi import requests as curl_requests

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
        encoded = encode_images([(data, mime, "ref." + mime.split("/")[1])])

    request = ConversationRequest(
        model="gpt-image-2",
        prompt=prompt,
        size=size,
        n=n,
        quality="auto",
        images=encoded,
        # response_format defaults to "b64_json" -> result dicts carry b64_json.
    )

    os.makedirs(out_dir, exist_ok=True)

    # Select reasoning effort for the image-prepare payload (no-op when "auto").
    image_thinking.set_thinking(thinking)
    try:
        # The pool wrapper calls account_service.get_available_access_token()
        # internally (our shim -> our token), so no manual backend construction.
        saved, message = _collect_saved(
            stream_image_outputs_with_pool(request), n, out_dir
        )
    finally:
        image_thinking.set_thinking("auto")

    if not saved:
        raise RuntimeError(
            f"image generation produced no images. Engine said: {message or '(no message)'}"
        )
    return saved
