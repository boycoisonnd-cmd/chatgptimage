"""Expand a short prompt into a rich, detailed image-generation prompt, using
the same ChatGPT account's text path (mirrors what the web UI does silently).
Falls back to a deterministic template wrapper if the text call fails.

The verbose system-prompt / template strings live in enhance_templates.py."""
from __future__ import annotations

# Importing _vendor_path makes the vendored `services.*` importable AND sets the
# vendored config's required auth-key env (single source: see _vendor_path).
# enhance.py may be imported standalone, so do it at module load.
import aigpt._vendor_path  # noqa: F401  (side-effect: _vendor on sys.path + auth-key env)
from aigpt.engine.enhance_templates import (
    FINTECH_SYSTEM,
    FINTECH_TEMPLATE,
    SLIDE_SYSTEM,
    SLIDE_TEMPLATE,
    SYSTEM,
    TEMPLATE,
)

# Skip enhancement when the user already wrote a detailed prompt.
_LONG_PROMPT_CHARS = 280

_SLIDE_STYLES = {"slide", "fintech"}


def _brand_clause(brand_colors: list[str] | None, reserve_corner: str | None) -> str:
    """Build the extra instruction text appended for brand context (may be empty)."""
    parts: list[str] = []
    if brand_colors:
        parts.append(
            "Use EXACTLY this brand color palette and no other dominant colors: "
            f"{', '.join(brand_colors)}. Apply them to the background, accents, "
            "and typography."
        )
    if reserve_corner:
        parts.append(
            f"Leave the {reserve_corner} corner area visually clear/empty — a logo "
            "will be placed there. Do NOT draw any logo, wordmark, brand name, or "
            "company text yourself anywhere in the image."
        )
    return " ".join(parts)


def _template_enhance(prompt: str, brand_clause: str = "", style: str = "auto") -> str:
    base = {"slide": SLIDE_TEMPLATE, "fintech": FINTECH_TEMPLATE}.get(style, TEMPLATE)
    out = base.format(p=prompt.strip())
    if brand_clause:
        out = f"{out} {brand_clause}"
    return out


def template_enhance(prompt: str, *, style: str = "auto",
                     brand_colors: list[str] | None = None,
                     reserve_corner: str | None = None) -> str:
    """Deterministic, offline prompt expansion (NO network call).

    Applies the style template (slide / fintech / auto) plus any brand clause.
    This is the same path enhance_prompt() falls back to, exposed for callers that
    want a styled prompt WITHOUT the LLM enhance (e.g. --no-enhance + --style):
    concise and predictable, and it never bloats content the way the LLM slide
    enhancer can (which is what garbles dense Vietnamese text).
    """
    return _template_enhance(
        prompt.strip(), _brand_clause(brand_colors, reserve_corner), style
    )


def enhance_prompt(prompt: str, *, text_model: str = "gpt-5",
                   brand_colors: list[str] | None = None,
                   reserve_corner: str | None = None,
                   style: str = "auto",
                   access_token: str | None = None) -> str:
    """Return an expanded prompt. Never raises — falls back to template on any error.

    style="slide" (clean editorial) or "fintech" (light-blue dashboard) apply a slide
    design aesthetic and ALWAYS enhance (the >=280-char skip is bypassed) so even
    detailed slide content gets the designer treatment, with content completed to a
    balanced medium density. When brand_colors/reserve_corner are provided, brand
    instructions are appended and enhancement also always runs.
    """
    p = prompt.strip()
    brand_clause = _brand_clause(brand_colors, reserve_corner)
    has_brand = bool(brand_clause)
    is_slide = style in _SLIDE_STYLES

    # Skip only for the generic "auto" style with no brand context on long prompts.
    if not is_slide and not has_brand and len(p) >= _LONG_PROMPT_CHARS:
        return p
    try:
        # Local imports: keep the vendored engine import lazy + after env/sys.path setup.
        from services.openai_backend_api import OpenAIBackendAPI
        from services.protocol.conversation import (
            ConversationRequest,
            stream_text_deltas,
        )
        token = access_token
        if not token:
            # No explicit token -> share the pool's active image account.
            from services.account_service import account_service
            token = account_service.get_text_access_token()
        backend = OpenAIBackendAPI(access_token=token)
        system = {"slide": SLIDE_SYSTEM, "fintech": FINTECH_SYSTEM}.get(style, SYSTEM)
        if brand_clause:
            system = f"{system} {brand_clause}"
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": p},
        ]
        req = ConversationRequest(model=text_model, messages=messages)
        out = "".join(stream_text_deltas(backend, req)).strip()
        # Guard against empty / junk / accidental refusal.
        if len(out) >= 40:
            return out
    except Exception:
        pass
    return _template_enhance(p, brand_clause, style)
