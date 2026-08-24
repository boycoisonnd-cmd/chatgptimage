"""Drive the vendored ChatGPT image-gen engine with our single-account token.

Entry: generate_image(prompt, aspect, n, out_dir) -> GenerateResult.
"""
from __future__ import annotations

import base64
import os
import sys
import time
from io import BytesIO

from PIL import Image

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
from aigpt.engine.edit_prompt import apply_mode_overlay
from aigpt.engine.enhance import enhance_prompt, template_enhance
from aigpt.engine.refs import RefImage, load_ref_images
from aigpt.engine.result import GenerateResult
from aigpt.sizes import resolve_size
from aigpt.types import Mode, Quality, Style, Thinking
from services.protocol.conversation import (
    ConversationRequest,
    encode_images,
    stream_image_outputs_with_pool,
)

_SLIDE_STYLES = ("slide", "fintech")

# A generation-only request must never silently swallow a reference image.
_GENERATE_WITH_REFS_MSG = (
    "mode='generate' does not accept ref_images; use mode='edit' or mode='style'"
)


def _ref_dimensions(ref: RefImage) -> tuple[int, int]:
    """Return (width, height) of a decoded reference image (Pillow)."""
    with Image.open(BytesIO(ref.data)) as img:
        return img.size


def _collect_saved(outputs, n: int, out_dir: str) -> tuple[list[str], str, str]:
    """Save up to `n` images from the engine output stream.

    The backend can return MORE variants than requested (n=1 has been seen to
    yield 2 results); cap at `n` so callers get exactly what they asked for.
    Returns (saved_paths, last_message, conversation_id).
    """
    saved: list[str] = []
    message = ""
    conversation_id = ""
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
            conversation_id = conversation_id or (output.conversation_id or "")
        if len(saved) >= n:
            break
    return saved, message, conversation_id


def _resolve_mode(mode: str | None, refs: list[str] | None) -> Mode:
    """Infer the generation mode when omitted: refs present -> edit, else generate."""
    if mode:
        return mode
    return "edit" if refs else "generate"


def generate_image(
    prompt: str,
    aspect: str = "16:9",
    n: int = 1,
    out_dir: str = "out",
    enhance: bool = True,
    style: Style = "auto",
    ref_image: str | None = None,
    ref_images: list[str] | None = None,
    mode: Mode | None = None,
    thinking: Thinking = "auto",
    brand_colors: list[str] | None = None,
    reserve_corner: str | None = None,
    quality: Quality = "auto",
    transparent: bool = False,
) -> GenerateResult:
    """Generate n image(s) and save them as PNGs. Returns saved file paths.

    mode discriminator:
      - "generate": pure text-to-image. Rejects ref_images.
      - "edit": 1 image = edit in place (keep identity); 2-4 = compose.
        enhance defaults to False (an enhance step would rewrite the edit
        instruction into a text-to-image prompt).
      - "style": match the reference's design style (palette/layout/type/mood);
        content is NOT copied.

    ref_image is an alias for a single ref_images[0]. ref_images entries must be
    https:// URLs (public images) or base64 data:image URLs (local uploads).
    Local file paths are rejected (H4): a URL parameter must never double as an
    arbitrary local file read. CLI converts local files to data: URLs in-process.

    thinking selects reasoning effort before drawing: "auto" (default = ChatGPT's
    own default) or "standard"/"extended"/"max" (increasing). Higher effort
    improves rendered-text fidelity (e.g. Vietnamese diacritics) at the cost of
    speed.

    quality is a hint to ChatGPT's image backend ("auto"/"low"/"medium"/"high").

    transparent=True appends a transparent-background instruction (works best in
    edit/style mode with a source image).

    brand_colors (list of hex) forces a palette; reserve_corner (e.g. "top-left")
    keeps a corner clear for a logo and bans any model-drawn logo/text.
    """
    if ref_image is not None and ref_images is not None:
        raise ValueError(
            "pass either ref_image or ref_images, not both (ref_image is an alias)")
    if ref_image is not None:
        ref_images = [ref_image]

    refs = load_ref_images(ref_images) if ref_images else []

    mode = _resolve_mode(mode, refs)

    if mode == "generate" and refs:
        raise ValueError(_GENERATE_WITH_REFS_MSG)

    if refs:
        dims = [_ref_dimensions(ref) for ref in refs]
    else:
        dims = None
    size = resolve_size(aspect, dims)

    if mode == "edit":
        # Edit NEVER goes through the T2I text enhancer (it would rewrite the
        # edit instruction into a text-to-image prompt). Apply the overlay and
        # keep the user's instruction intact.
        prompt = apply_mode_overlay("edit", prompt, len(refs), transparent)
    elif mode == "style":
        prompt = apply_mode_overlay("style", prompt, len(refs), transparent)
    elif enhance:
        # T2I enhance: fix the account for THIS image so enhance (text) and the
        # image share it (raises NoQuotaError up-front if every account is exhausted).
        active = get_pool().current_token()
        prompt = enhance_prompt(prompt, style=style, brand_colors=brand_colors,
                                reserve_corner=reserve_corner, access_token=active)
        if transparent:
            prompt = apply_mode_overlay("generate", prompt, 0, transparent)
        print(f"[enhance] prompt expanded to {len(prompt)} chars", file=sys.stderr)
    elif style in _SLIDE_STYLES or brand_colors or reserve_corner:
        prompt = template_enhance(prompt, style=style, brand_colors=brand_colors,
                                  reserve_corner=reserve_corner)
        print(f"[template] styled prompt ({len(prompt)} chars, no LLM)", file=sys.stderr)

    encoded: list[str] | None = None
    if refs:
        encoded = encode_images(
            [(ref.data, ref.mime, ref.name) for ref in refs]
        )

    request = ConversationRequest(
        model="gpt-image-2",
        prompt=prompt,
        size=size,
        n=n,
        quality=quality,
        images=encoded,
        # response_format defaults to "b64_json" -> result dicts carry b64_json.
    )

    os.makedirs(out_dir, exist_ok=True)

    # Select reasoning effort for the image-prepare payload (no-op when "auto").
    image_thinking.set_thinking(thinking)
    try:
        # The pool wrapper calls account_service.get_available_access_token()
        # internally (our shim -> our token), so no manual backend construction.
        saved, message, conversation_id = _collect_saved(
            stream_image_outputs_with_pool(request), n, out_dir
        )
    finally:
        image_thinking.set_thinking("auto")

    if not saved:
        raise RuntimeError(
            f"image generation produced no images. Engine said: {message or '(no message)'}"
        )
    return GenerateResult.from_list(saved, conversation_id)
