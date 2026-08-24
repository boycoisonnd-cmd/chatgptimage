"""Inject an optional `thinking_effort` into the gpt-image-2 prepare payload.

The ChatGPT web UI has an "Intelligence" selector (Instant / Medium / High) that
makes the image model reason more before drawing. More reasoning improves fine
detail and especially TEXT rendering (e.g. Vietnamese diacritics), at the cost of
speed. The vendored backend's image path does NOT send this field (only the
editable PPT/PSD path does), so we add it WITHOUT editing the vendored file:

A class-level wrapper around `OpenAIBackendAPI._prepare_image_conversation`
temporarily intercepts the outgoing image-prepare POST and merges `thinking_effort`
into the picture_v2 payload. Keeping the vendored file byte-identical means
scripts/update-vendor.sh can still re-pull it verbatim. The wrapper depends only
on a stable identifier (the picture_v2 prepare payload shape), not on the method
body, so it survives upstream refactors.

Wire values were reverse-engineered against the live image-prepare endpoint:
the accepted enum is "standard" < "extended" < "max" (anything else -> HTTP 422
"Invalid conversation body"). These correspond to the web UI "Intelligence" menu
(roughly Instant/Medium/High). "auto"/None sends no field -> ChatGPT's default.
"""
from __future__ import annotations

# MUST precede vendored imports (sys.path side effect).
import aigpt._vendor_path  # noqa: F401
from services.openai_backend_api import OpenAIBackendAPI

# Reverse-engineered valid enum for the image path. "auto"/None => send nothing.
_VALID = {"standard", "extended", "max"}
_level: str | None = None


def set_thinking(level: str | None) -> None:
    """Select reasoning effort for subsequent image generations.

    level: "auto" / None (default - send nothing; ChatGPT's own default), or one
    of "standard", "extended", "max" (increasing reasoning). Higher = better
    rendered text but slower. Raises ValueError on anything else.
    """
    global _level
    norm = (level or "auto").strip().lower()
    if norm in ("auto", ""):
        _level = None
        return
    if norm not in _VALID:
        raise ValueError(
            f"unknown thinking level {level!r}; use auto|standard|extended|max"
        )
    _level = norm


def current() -> str | None:
    return _level


def _is_image_prepare_payload(body: object) -> bool:
    return (
        isinstance(body, dict)
        and body.get("system_hints") == ["picture_v2"]
        and "partial_query" in body
    )


_orig_prepare = OpenAIBackendAPI._prepare_image_conversation


def _prepare_image_conversation(self, prompt, requirements, model):
    if _level is None:
        return _orig_prepare(self, prompt, requirements, model)

    real_post = self.session.post

    def _post(url, *args, **kwargs):
        body = kwargs.get("json")
        if _is_image_prepare_payload(body):
            body["thinking_effort"] = _level
        return real_post(url, *args, **kwargs)

    self.session.post = _post
    try:
        return _orig_prepare(self, prompt, requirements, model)
    finally:
        # Exact-restore, never `del`: with stacked interceptors a `del` would
        # strip the OUTER interceptor too (it drops the instance override and
        # re-exposes the class method, discarding what we captured).
        self.session.post = real_post


# Install once (idempotent across re-imports).
if not getattr(OpenAIBackendAPI._prepare_image_conversation, "_aigpt_thinking", False):
    _prepare_image_conversation._aigpt_thinking = True
    OpenAIBackendAPI._prepare_image_conversation = _prepare_image_conversation
