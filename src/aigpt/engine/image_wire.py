"""Align the image-generation wire format with the real ChatGPT web client.

The vendored chatgpt2api image flow predates the current chatgpt.com client,
so its payloads diverge from the traffic the web UI actually sends today
(captured from real Chrome DevTools on 2026-08-24, image created successfully):

  #  field                    vendored              web (real)
  1  parts[].asset_pointer    file-service://...    sediment://...
  2  root system_hints        ["picture_v2"]        []
  3  metadata.system_hints    present               removed
  4  attachments[]            camelCase mimeType    snake_case + source/
                                                    library_file_id/is_big_paste
  5  model_response_contracts missing               photo_upload_action.v1
  6  local_function_names     missing               ["local.continue_in_work"]
  7  parent_message_id        random uuid           "client-created-root"

Deliberately LEFT alone (verified working / unverified-but-risky): the model
slug (prod-proven; "auto" unverified) and the timezone client metadata
(cosmetic).

Like ``engine/image_thinking.py``, this module never edits the vendored files
(``scripts/update-vendor.sh`` re-pulls them verbatim): class-level wrappers
around ``_upload_image`` and ``_start_image_generation`` temporarily swap
``session.post`` (exact-restore in ``finally`` — never ``del``, which would
strip an outer stacked interceptor too) and rewrite the JSON bodies in flight.

The pure rewrite functions are module-level so golden tests can pin the exact
wire dialect without any session at all.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

# MUST precede vendored imports (sys.path side effect).
import aigpt._vendor_path  # noqa: F401
from services.openai_backend_api import OpenAIBackendAPI
from utils.helper import UpstreamHTTPError

_FILE_SERVICE_PREFIX = "file-service://"
_SEDIMENT_PREFIX = "sediment://"

# HTTP statuses that mean "this conversation cannot be continued" (bogus id,
# cross-account 404, stale chain). Confirmed live in spike B0 before PR B's
# fallback specifics were hardcoded.
_REJECTION_STATUSES = (400, 404, 422)


@dataclass(frozen=True)
class Followup:
    """An in-flight decision to continue an earlier image conversation.

    ``parent_message_id`` may start as None and be resolved lazily (leaf of
    the conversation) by the prepare wrapper before the generation POST runs.
    """

    conversation_id: str
    parent_message_id: str | None = None


# ---------------------------------------------------------------------------
# Module state (safe: n>1 parallel generation is force-disabled upstream, and
# follow-up itself forces n==1; api.py serializes HTTP requests in practice).
# ---------------------------------------------------------------------------
_followup: Followup | None = None
_rejected: bool = False
_library_map: dict[str, str] = {}


def set_followup(conversation_id: str, parent_message_id: str | None = None) -> None:
    """Continue ``conversation_id`` on the next generation (one turn only)."""
    global _followup, _rejected
    if not isinstance(conversation_id, str) or not conversation_id.strip():
        raise ValueError("conversation_id must be a non-empty string")
    if parent_message_id is not None and (
        not isinstance(parent_message_id, str) or not parent_message_id.strip()
    ):
        raise ValueError("parent_message_id must be a non-empty string or None")
    _followup = Followup(conversation_id.strip(), parent_message_id)
    _rejected = False


def clear_followup() -> None:
    global _followup, _rejected
    _followup = None
    _rejected = False


def consume_followup_rejection() -> bool:
    """True iff the last follow-up attempt was rejected by the backend.

    Consumption clears BOTH the rejection flag and the follow-up state in the
    same call: a fallback retry must never re-inject the rejected
    conversation_id (it would be rejected again and silently lose the
    fallback).
    """
    global _followup, _rejected
    was_rejected = _rejected
    _rejected = False
    _followup = None
    return was_rejected


def begin_generation() -> None:
    """Reset per-generation state (library map from prior uploads, old flag)."""
    global _rejected
    _library_map.clear()
    _rejected = False


def current_followup() -> Followup | None:
    return _followup


# ---------------------------------------------------------------------------
# Pure payload rewrites (golden-test targets; no session needed).
# ---------------------------------------------------------------------------
def rewrite_files_body(body: Any) -> Any:
    """Copy of the files-register body with library persistence requested.

    Same dialect the editable path already sends successfully; the response
    then carries ``library_file_id``, which the generation rewrite attaches
    (web capture shows attachments carry it).
    """
    if not isinstance(body, dict):
        return body
    return {
        **body,
        "store_in_library": True,
        "library_persistence_mode": "opportunistic",
    }


def _rewrite_part(part: Any) -> Any:
    """Row 1: ``file-service://<file_id>`` asset pointers -> ``sediment://``."""
    if not isinstance(part, dict):
        return part
    pointer = part.get("asset_pointer")
    if isinstance(pointer, str) and pointer.startswith(_FILE_SERVICE_PREFIX):
        return {
            **part,
            "asset_pointer": _SEDIMENT_PREFIX + pointer[len(_FILE_SERVICE_PREFIX):],
        }
    return dict(part)


def _rewrite_attachment(attachment: Any, library_map: Mapping[str, str]) -> Any:
    """Row 4: vendored camelCase attachment -> the web's snake_case shape.

    Adds ``source``/``is_big_paste`` and ``library_file_id`` when the upload
    response carried one. A library-map miss omits the field (graceful: the
    web also tolerates attachments without it).
    """
    if not isinstance(attachment, dict):
        return attachment
    file_id = str(attachment.get("id") or "")
    rewritten: dict[str, Any] = {
        "id": file_id,
        "size": attachment.get("size"),
        "name": attachment.get("name"),
        # Idempotent: accept either the vendored camelCase or an already
        # rewritten body.
        "mime_type": attachment.get("mimeType") or attachment.get("mime_type"),
        "width": attachment.get("width"),
        "height": attachment.get("height"),
        "source": "local",
    }
    library_id = library_map.get(file_id)
    if library_id:
        rewritten["library_file_id"] = library_id
    rewritten["is_big_paste"] = False
    return rewritten


def rewrite_gen_payload(
    body: Any,
    library_map: Mapping[str, str],
    followup: Followup | None = None,
) -> Any:
    """Copy of the vendored image-generation body rewritten to the web dialect.

    Applies diff-table rows 1-7. T2I (no references) naturally reduces to the
    root-level rows (2/5/6/7) because there are no asset parts/attachments.
    Never mutates the input.
    """
    if not isinstance(body, dict):
        return body
    payload = dict(body)

    messages = payload.get("messages")
    if isinstance(messages, list):
        new_messages = []
        for message in messages:
            if not isinstance(message, dict):
                new_messages.append(message)
                continue
            message = dict(message)
            content = message.get("content")
            if isinstance(content, dict):
                parts = content.get("parts")
                if isinstance(parts, list):
                    content = {**content, "parts": [_rewrite_part(p) for p in parts]}
                    message["content"] = content
            metadata = message.get("metadata")
            if isinstance(metadata, dict):
                # Row 3: metadata.system_hints removed.
                metadata = {k: v for k, v in metadata.items() if k != "system_hints"}
                attachments = metadata.get("attachments")
                if isinstance(attachments, list):
                    metadata["attachments"] = [
                        _rewrite_attachment(a, library_map) for a in attachments
                    ]
                message["metadata"] = metadata
            new_messages.append(message)
        payload["messages"] = new_messages

    # Row 2: the web sends an empty root system_hints (["picture_v2"] is the
    # vendored marker this module matches on BEFORE rewriting).
    if "system_hints" in payload:
        payload["system_hints"] = []

    # Rows 5-6: present in every captured web image request.
    payload["model_response_contracts"] = [{
        "id": "photo_upload_action.v1",
        "protocol_version": 1,
        "presets": ["cap:image", "cap:file", "placement:end"],
    }]
    payload["local_function_names"] = ["local.continue_in_work"]

    # Row 7 / follow-up chain.
    if followup is not None:
        payload["conversation_id"] = followup.conversation_id
        # The prepare wrapper resolves+ caches the leaf before this runs; the
        # root pointer is only a safety net if that step never happened.
        payload["parent_message_id"] = (
            followup.parent_message_id or "client-created-root"
        )
    else:
        payload["parent_message_id"] = "client-created-root"
    return payload


# ---------------------------------------------------------------------------
# Matchers (run on the PRE-mutation body: the rewrite clears the marker, so
# matching after mutation would never fire twice — and must fire at all).
# ---------------------------------------------------------------------------
def _is_files_register(url: object, body: object) -> bool:
    return (
        isinstance(url, str)
        and url.rstrip("/").endswith("/backend-api/files")
        and isinstance(body, dict)
        and "use_case" in body
    )


def _is_gen_payload(url: object, body: object) -> bool:
    """The image-generation SSE POST.

    Unambiguous against the other ``/backend-api/f/conversation`` POSTs: the
    prepare body carries ``partial_query`` (no ``messages``), editable uses
    ``system_hints == []``, chat-requirements posts ``{"p": ...}``.
    """
    return (
        isinstance(url, str)
        and url.rstrip("/").endswith("/backend-api/f/conversation")
        and isinstance(body, dict)
        and "messages" in body
        and body.get("system_hints") == ["picture_v2"]
    )


# ---------------------------------------------------------------------------
# session.post interception (shared swap helper + wrappers).
# ---------------------------------------------------------------------------
Mutator = Callable[[object, Any], Any]
ResponseHook = Callable[[object, Any, Any], None]


def _wrapped_post(
    real_post: Any,
    mutate: Mutator | None = None,
    on_response: ResponseHook | None = None,
) -> Any:
    """Build a ``session.post`` interceptor.

    ``mutate(url, body)`` may return a replacement JSON body (or None to send
    the original); ``on_response(url, pre_mutation_body, response)`` observes
    the response without consuming it (``Response.json()`` is repeatable on
    curl_cffi's buffered content).
    """

    def _post(url, *args, **kwargs):
        body = kwargs.get("json")
        rewritten = mutate(url, body) if mutate is not None else None
        if rewritten is not None:
            kwargs["json"] = rewritten
        response = real_post(url, *args, **kwargs)
        if on_response is not None:
            on_response(url, body, response)
        return response

    return _post


def _swap_post(session: Any, build_interceptor: Callable[[Any], Any]) -> Callable[[], None]:
    """Swap ``session.post`` and return a restorer that exactly undoes it.

    ``build_interceptor(real_post)`` receives the captured original so the
    interceptor can delegate to it. Restoration is exact in BOTH senses:
    - if ``post`` was already an instance attribute (an OUTER interceptor is
      stacked on top), it is assigned back — never ``del``-ed, which would
      strip that outer interceptor;
    - if ``post`` resolved from the class (no outer override), the override
      we added is removed so the session is left byte-for-byte as we found it.
    """
    had_instance = "post" in session.__dict__
    real_post = session.post
    session.post = build_interceptor(real_post)

    def restore() -> None:
        if had_instance:
            session.post = real_post
            return
        try:
            del session.post
        except AttributeError:
            session.post = real_post

    return restore


def _capture_library_id(url: object, body: object, response: Any) -> None:
    """Remember ``file_id -> library_file_id`` for the generation rewrite.

    Best-effort by design: a miss only means the attachment goes out without
    ``library_file_id`` (graceful degradation, verified acceptable in the
    spike), so a non-JSON/error response must never break the upload itself.
    """
    if not _is_files_register(url, body):
        return
    try:
        meta = response.json()
    except Exception:  # best-effort capture, see docstring
        return
    if not isinstance(meta, dict):
        return
    file_id = str(meta.get("file_id") or "")
    library_id = meta.get("library_file_id")
    if file_id and library_id:
        _library_map[file_id] = str(library_id)


_orig_upload_image = OpenAIBackendAPI._upload_image
_orig_start_image_generation = OpenAIBackendAPI._start_image_generation


def _upload_image_w(self, image, file_name: str = "image.png"):
    """Interceptor #1: request library persistence, capture library_file_id."""

    def _build(real_post):
        return _wrapped_post(
            real_post,
            mutate=lambda url, body: (
                rewrite_files_body(body) if _is_files_register(url, body) else None
            ),
            on_response=_capture_library_id,
        )

    restore = _swap_post(self.session, _build)
    try:
        return _orig_upload_image(self, image, file_name)
    finally:
        restore()  # exact-restore: never strips an outer stacked interceptor


def _start_image_generation_w(
    self, prompt, requirements, conduit_token, model, references=None
):
    """Interceptor #2: rewrite the generation payload to the web dialect.

    Also flags follow-up rejections (PR B): the pool layer flattens
    UpstreamHTTPError into a plain string, so detection must happen here, at
    the wrapper, via module state — not by string-matching later.
    """
    global _rejected

    def _mutate(url, body):
        if _is_gen_payload(url, body):
            return rewrite_gen_payload(body, _library_map, _followup)
        return None

    def _build(real_post):
        return _wrapped_post(real_post, mutate=_mutate)

    restore = _swap_post(self.session, _build)
    try:
        return _orig_start_image_generation(
            self, prompt, requirements, conduit_token, model, references
        )
    except UpstreamHTTPError as exc:
        if _followup is not None and exc.status_code in _REJECTION_STATUSES:
            _rejected = True
        raise
    finally:
        restore()  # exact-restore: never strips an outer stacked interceptor


# Install once (idempotent across re-imports), same pattern as image_thinking.
if not getattr(OpenAIBackendAPI._upload_image, "_aigpt_wire", False):
    _upload_image_w._aigpt_wire = True  # type: ignore[attr-defined]
    OpenAIBackendAPI._upload_image = _upload_image_w

if not getattr(OpenAIBackendAPI._start_image_generation, "_aigpt_wire", False):
    _start_image_generation_w._aigpt_wire = True  # type: ignore[attr-defined]
    OpenAIBackendAPI._start_image_generation = _start_image_generation_w
