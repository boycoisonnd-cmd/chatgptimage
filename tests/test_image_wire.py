"""Tests for engine/image_wire.py (PR A: wire alignment).

Golden bodies mirror the vendored `_start_image_generation` payload shape
exactly (src/aigpt/_vendor/services/openai_backend_api.py), so the rewrite
tests pin the real transformation, not a paraphrase of it.
"""
from __future__ import annotations

import base64
import copy
import io

import pytest
from PIL import Image

# MUST precede vendored imports (sys.path side effect).
import aigpt._vendor_path  # noqa: F401
from aigpt.engine import image_wire
from aigpt.engine.image_wire import Followup
from services.openai_backend_api import OpenAIBackendAPI
from utils.helper import UpstreamHTTPError

_PNG_BUF = io.BytesIO()
Image.new("RGB", (1, 1), "red").save(_PNG_BUF, format="PNG")
_PNG_B64 = base64.b64encode(_PNG_BUF.getvalue()).decode()


def _make_vendor_i2i_body() -> dict:
    """The exact body the vendored `_start_image_generation` builds for I2I."""
    return {
        "action": "next",
        "messages": [{
            "id": "b1a2c3d4-0000-4000-8000-000000000001",
            "author": {"role": "user"},
            "create_time": 1755990000.0,
            "content": {
                "content_type": "multimodal_text",
                "parts": [
                    {
                        "content_type": "image_asset_pointer",
                        "asset_pointer": "file-service://file_abc",
                        "width": 100,
                        "height": 200,
                        "size_bytes": 1234,
                    },
                    {
                        "content_type": "image_asset_pointer",
                        "asset_pointer": "file-service://file_def",
                        "width": 300,
                        "height": 400,
                        "size_bytes": 5678,
                    },
                    "make it redder",
                ],
            },
            "metadata": {
                "developer_mode_connector_ids": [],
                "selected_github_repos": [],
                "selected_all_github_repos": False,
                "system_hints": ["picture_v2"],
                "serialization_metadata": {"custom_symbol_offsets": []},
                "attachments": [
                    {
                        "id": "file_abc",
                        "mimeType": "image/png",
                        "name": "a.png",
                        "size": 1234,
                        "width": 100,
                        "height": 200,
                    },
                    {
                        "id": "file_def",
                        "mimeType": "image/jpeg",
                        "name": "b.jpg",
                        "size": 5678,
                        "width": 300,
                        "height": 400,
                    },
                ],
            },
        }],
        "parent_message_id": "b1a2c3d4-0000-4000-8000-000000000099",
        "model": "gpt-5-3",
        "client_prepare_state": "sent",
        "timezone_offset_min": -480,
        "timezone": "Asia/Shanghai",
        "conversation_mode": {"kind": "primary_assistant"},
        "enable_message_followups": True,
        "system_hints": ["picture_v2"],
        "supports_buffering": True,
        "supported_encodings": ["v1"],
        "client_contextual_info": {
            "is_dark_mode": False,
            "time_since_loaded": 1200,
            "page_height": 1072,
            "page_width": 1724,
            "pixel_ratio": 1.2,
            "screen_height": 1440,
            "screen_width": 2560,
            "app_name": "chatgpt.com",
        },
        "paragen_cot_summary_display_override": "allow",
        "force_parallel_switch": "auto",
    }


def _make_vendor_t2i_body() -> dict:
    """The vendored body for text-to-image (no references)."""
    body = _make_vendor_i2i_body()
    message = body["messages"][0]
    message["content"] = {"content_type": "text", "parts": ["a red car"]}
    del message["metadata"]["attachments"]
    return body


@pytest.fixture(autouse=True)
def _clean_wire_state():
    image_wire.clear_followup()
    image_wire.begin_generation()
    yield
    image_wire.clear_followup()
    image_wire.begin_generation()


# ---------------------------------------------------------------------------
# Golden I2I rewrite — every diff-table row.
# ---------------------------------------------------------------------------
def test_golden_i2i_rewrite_matches_captured_web_dialect():
    body = _make_vendor_i2i_body()
    out = image_wire.rewrite_gen_payload(body, {"file_abc": "libfile_123"})

    message = out["messages"][0]
    parts = message["content"]["parts"]

    # Row 1: asset pointers move to the sediment scheme; prompt untouched.
    assert parts[0]["asset_pointer"] == "sediment://file_abc"
    assert parts[1]["asset_pointer"] == "sediment://file_def"
    assert parts[2] == "make it redder"
    assert parts[0]["size_bytes"] == 1234  # pointer fields otherwise intact

    # Row 2: root system_hints emptied.
    assert out["system_hints"] == []

    # Row 3: metadata.system_hints removed; serialization_metadata kept.
    metadata = message["metadata"]
    assert "system_hints" not in metadata
    assert metadata["serialization_metadata"] == {"custom_symbol_offsets": []}

    # Row 4: snake_case attachments with web extras.
    first, second = metadata["attachments"]
    assert first == {
        "id": "file_abc",
        "size": 1234,
        "name": "a.png",
        "mime_type": "image/png",
        "width": 100,
        "height": 200,
        "source": "local",
        "library_file_id": "libfile_123",
        "is_big_paste": False,
    }
    # Row 4b: library-map miss omits library_file_id (graceful).
    assert "library_file_id" not in second
    assert second["mime_type"] == "image/jpeg"
    assert second["is_big_paste"] is False

    # Rows 5-6: new root fields.
    assert out["model_response_contracts"] == [{
        "id": "photo_upload_action.v1",
        "protocol_version": 1,
        "presets": ["cap:image", "cap:file", "placement:end"],
    }]
    assert out["local_function_names"] == ["local.continue_in_work"]

    # Row 7: new conversations anchor at client-created-root.
    assert out["parent_message_id"] == "client-created-root"

    # Deliberately untouched: model slug + timezone (rows 9-10).
    assert out["model"] == "gpt-5-3"
    assert out["timezone_offset_min"] == -480
    assert out["timezone"] == "Asia/Shanghai"
    assert out["client_prepare_state"] == "sent"
    assert out["force_parallel_switch"] == "auto"


def test_rewrite_does_not_mutate_input():
    body = _make_vendor_i2i_body()
    original = copy.deepcopy(body)
    image_wire.rewrite_gen_payload(body, {"file_abc": "libfile_123"})
    assert body == original


def test_t2i_rewrite_applies_root_rows_only():
    body = _make_vendor_t2i_body()
    out = image_wire.rewrite_gen_payload(body, {})

    assert out["system_hints"] == []
    assert out["parent_message_id"] == "client-created-root"
    assert out["local_function_names"] == ["local.continue_in_work"]
    assert "model_response_contracts" in out

    # No refs -> no asset pointers or attachments anywhere.
    message = out["messages"][0]
    assert message["content"]["parts"] == ["a red car"]
    assert "attachments" not in message["metadata"]
    assert "system_hints" not in message["metadata"]


def test_followup_rewrite_carries_conversation_chain():
    body = _make_vendor_i2i_body()
    followup = Followup("conv-123", "msg-456")
    out = image_wire.rewrite_gen_payload(body, {}, followup)
    assert out["conversation_id"] == "conv-123"
    assert out["parent_message_id"] == "msg-456"
    assert out["parent_message_id"] != "client-created-root"


def test_followup_without_parent_falls_back_to_root_pointer():
    body = _make_vendor_i2i_body()
    out = image_wire.rewrite_gen_payload(body, {}, Followup("conv-123", None))
    assert out["conversation_id"] == "conv-123"
    assert out["parent_message_id"] == "client-created-root"


def test_non_dict_bodies_pass_through_untouched():
    assert image_wire.rewrite_gen_payload(None, {}) is None
    assert image_wire.rewrite_gen_payload("x", {}) == "x"
    assert image_wire.rewrite_files_body([1, 2]) == [1, 2]


# ---------------------------------------------------------------------------
# rewrite_files_body — library persistence request.
# ---------------------------------------------------------------------------
def test_rewrite_files_body_adds_library_fields():
    body = {
        "file_name": "a.png",
        "file_size": 1234,
        "use_case": "multimodal",
        "width": 100,
        "height": 200,
    }
    out = image_wire.rewrite_files_body(body)
    assert out["store_in_library"] is True
    assert out["library_persistence_mode"] == "opportunistic"
    assert out["use_case"] == "multimodal"
    assert "store_in_library" not in body  # input not mutated


# ---------------------------------------------------------------------------
# Module state: set / clear / consume / begin.
# ---------------------------------------------------------------------------
def test_set_and_clear_followup():
    image_wire.set_followup("conv-1")
    assert image_wire.current_followup() == Followup("conv-1", None)
    image_wire.clear_followup()
    assert image_wire.current_followup() is None


def test_set_followup_validates_input():
    with pytest.raises(ValueError):
        image_wire.set_followup("")
    with pytest.raises(ValueError):
        image_wire.set_followup("   ")
    with pytest.raises(ValueError):
        image_wire.set_followup(123)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        image_wire.set_followup("conv-1", parent_message_id="")
    image_wire.set_followup(" conv-1 ")  # stripped, accepted
    assert image_wire.current_followup().conversation_id == "conv-1"


def test_consume_followup_rejection_resets_everything():
    image_wire.set_followup("conv-1")
    image_wire._rejected = True

    assert image_wire.consume_followup_rejection() is True
    # Second consume: flag cleared AND follow-up state cleared in the same
    # call, so a fallback retry cannot re-inject the rejected conversation.
    assert image_wire.consume_followup_rejection() is False
    assert image_wire.current_followup() is None
    out = image_wire.rewrite_gen_payload(_make_vendor_i2i_body(), {}, None)
    assert "conversation_id" not in out


def test_consume_without_rejection_is_false():
    image_wire.set_followup("conv-1")
    assert image_wire.consume_followup_rejection() is False
    assert image_wire.current_followup() is None


def test_begin_generation_resets_library_map_and_flag():
    image_wire._library_map["file_x"] = "libfile_x"
    image_wire._rejected = True
    image_wire.begin_generation()
    assert image_wire._library_map == {}
    assert image_wire._rejected is False


# ---------------------------------------------------------------------------
# Interceptor tests (fake session + duck backend, no network).
# ---------------------------------------------------------------------------
_BASE = "https://chatgpt.com"


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = "{}"
        self.headers: dict = {}

    def json(self):
        # Repeatable, like curl_cffi's buffered content.
        return self._payload


class _RecordingSession:
    """Fake session: records calls, canned responses, optional gen failure."""

    def __init__(self, fail_gen: int | None = None):
        self.calls: list[tuple[str, object]] = []
        self.responses: dict[str, _FakeResponse] = {}
        self.fail_gen = fail_gen

    def post(self, url, *args, **kwargs):
        self.calls.append((url, kwargs.get("json")))
        if self.fail_gen is not None and url.endswith("/backend-api/f/conversation"):
            return _FakeResponse(status_code=self.fail_gen)
        for suffix, resp in self.responses.items():
            if url.endswith(suffix):
                return resp
        return _FakeResponse()

    def put(self, url, *args, **kwargs):
        self.calls.append((url, None))
        return _FakeResponse()


class _Reqs:
    token = "sentinel-token"
    proof_token = None


class _DuckBackend:
    """Duck-typed OpenAIBackendAPI for the wrapped methods (skips __init__).

    Class attribute lookups resolve to the image_wire-wrapped versions (they
    were installed on OpenAIBackendAPI at import time), which then delegate
    to the captured vendored originals.
    """

    base_url = _BASE
    user_agent = "test-ua"

    def __init__(self, session: _RecordingSession):
        self.session = session

    def _headers(self, path, extra=None):
        return {"X-Test": "1"}

    def _image_headers(self, path, requirements, conduit_token="", accept="*/*"):
        return {"X-Test": "image"}

    def _image_model_slug(self, model):
        return "gpt-5-3"

    _decode_image_base64 = OpenAIBackendAPI._decode_image_base64
    _upload_image = OpenAIBackendAPI._upload_image
    _start_image_generation = OpenAIBackendAPI._start_image_generation
    _prepare_image_conversation = OpenAIBackendAPI._prepare_image_conversation


_ONE_REF = [{
    "file_id": "file_abc",
    "width": 100,
    "height": 200,
    "file_size": 1234,
    "mime_type": "image/png",
    "file_name": "a.png",
}]


def test_upload_interceptor_adds_library_fields_and_captures_id():
    session = _RecordingSession()
    session.responses["/backend-api/files"] = _FakeResponse(payload={
        "file_id": "file_abc",
        "upload_url": "https://blob.example/upload",
        "library_file_id": "libfile_xyz",
    })
    backend = _DuckBackend(session)
    image_wire.begin_generation()

    result = backend._upload_image(_PNG_B64, "a.png")

    register_call = next(
        (url, body) for url, body in session.calls
        if url.endswith("/backend-api/files") and isinstance(body, dict)
    )
    assert register_call[1]["store_in_library"] is True
    assert register_call[1]["library_persistence_mode"] == "opportunistic"
    assert image_wire._library_map == {"file_abc": "libfile_xyz"}
    assert result["file_id"] == "file_abc"  # vendor read the same response too


def test_capture_leaves_response_readable_for_vendor():
    resp = _FakeResponse(payload={"file_id": "f1", "library_file_id": "lib1"})
    image_wire.begin_generation()
    image_wire._capture_library_id(
        _BASE + "/backend-api/files", {"use_case": "multimodal"}, resp
    )
    assert image_wire._library_map["f1"] == "lib1"
    # The interceptor's .json() read must not consume the vendor's read.
    assert resp.json() == {"file_id": "f1", "library_file_id": "lib1"}


def test_capture_is_best_effort_on_bad_responses():
    image_wire.begin_generation()

    class _Broken:
        def json(self):
            raise ValueError("not json")

    image_wire._capture_library_id(
        _BASE + "/backend-api/files", {"use_case": "multimodal"}, _Broken()
    )
    assert image_wire._library_map == {}


def test_matcher_selectivity():
    gen = _make_vendor_i2i_body()
    prepare = {
        "action": "next",
        "parent_message_id": "uuid",
        "model": "gpt-5-3",
        "system_hints": ["picture_v2"],
        "partial_query": {"id": "uuid", "content": {"parts": ["p"]}},
    }
    editable = dict(gen, system_hints=[])
    files = {"file_name": "a.png", "file_size": 1, "use_case": "multimodal"}

    # The gen matcher hits only the real generation POST...
    assert image_wire._is_gen_payload(_BASE + "/backend-api/f/conversation", gen)
    # ...not prepare (same system_hints, but partial_query + /prepare URL),
    # not editable (system_hints == []), not chat-requirements ({"p": ...}).
    assert not image_wire._is_gen_payload(
        _BASE + "/backend-api/f/conversation/prepare", prepare)
    assert not image_wire._is_gen_payload(
        _BASE + "/backend-api/f/conversation", editable)
    assert not image_wire._is_gen_payload(
        _BASE + "/backend-api/f/conversation", {"p": "token"})
    # And the files matcher hits only the register call — not the
    # `/uploaded` confirmation (POSTed with data="{}", no json body).
    assert image_wire._is_files_register(_BASE + "/backend-api/files", files)
    # A generation body posted to the files URL still has no "use_case".
    assert not image_wire._is_files_register(_BASE + "/backend-api/files", gen)
    # The `/uploaded` confirmation POST carries data="{}", no json body.
    assert not image_wire._is_files_register(
        _BASE + "/backend-api/files/file_abc/uploaded", None)


def test_gen_wrapper_rewrites_payload_and_restores_session():
    session = _RecordingSession()
    backend = _DuckBackend(session)
    image_wire.begin_generation()
    image_wire._library_map["file_abc"] = "libfile_xyz"

    backend._start_image_generation(
        "make it redder", _Reqs(), "conduit-1", "gpt-image-2", _ONE_REF)

    assert len(session.calls) == 1
    url, sent = session.calls[0]
    assert url.endswith("/backend-api/f/conversation")
    assert sent["system_hints"] == []
    assert sent["parent_message_id"] == "client-created-root"
    assert sent["local_function_names"] == ["local.continue_in_work"]
    assert "model_response_contracts" in sent
    parts = sent["messages"][0]["content"]["parts"]
    assert parts[0]["asset_pointer"] == "sediment://file_abc"
    attachment = sent["messages"][0]["metadata"]["attachments"][0]
    assert attachment["library_file_id"] == "libfile_xyz"
    assert attachment["source"] == "local"
    # Exact restore: the override added by the wrapper is gone.
    assert "post" not in session.__dict__


def test_gen_wrapper_flags_followup_rejection_statuses():
    for status in (400, 404, 422):
        session = _RecordingSession(fail_gen=status)
        backend = _DuckBackend(session)
        image_wire.set_followup("conv-x")
        with pytest.raises(UpstreamHTTPError):
            backend._start_image_generation(
                "blue", _Reqs(), "c", "gpt-image-2")
        assert image_wire.consume_followup_rejection() is True

    # 500 is a backend failure, not a follow-up rejection.
    session = _RecordingSession(fail_gen=500)
    backend = _DuckBackend(session)
    image_wire.set_followup("conv-x")
    with pytest.raises(UpstreamHTTPError):
        backend._start_image_generation("blue", _Reqs(), "c", "gpt-image-2")
    assert image_wire.consume_followup_rejection() is False


def test_rejection_without_active_followup_is_not_flagged():
    session = _RecordingSession(fail_gen=404)
    backend = _DuckBackend(session)
    with pytest.raises(UpstreamHTTPError):
        backend._start_image_generation("blue", _Reqs(), "c", "gpt-image-2")
    assert image_wire._rejected is False


def test_stacked_outer_interceptor_survives_and_sees_rewritten_body():
    session = _RecordingSession()
    backend = _DuckBackend(session)
    image_wire.begin_generation()
    class_post = _RecordingSession.post
    seen: list[tuple[object, object]] = []

    def outer_post(url, *args, **kwargs):
        body = kwargs.get("json")
        if isinstance(body, dict):
            seen.append((body.get("system_hints"), body.get("local_function_names")))
        return class_post(session, url, *args, **kwargs)

    session.post = outer_post  # an OUTER interceptor as instance attribute
    try:
        backend._start_image_generation("p", _Reqs(), "c", "gpt-image-2", _ONE_REF)
        # The outer interceptor ran INSIDE ours and saw the rewritten body.
        assert seen == [([], ["local.continue_in_work"])]
        # Exact restore put the outer interceptor back — it was not stripped.
        assert session.__dict__["post"] is outer_post
    finally:
        del session.post


def test_wrappers_are_installed_idempotently():
    assert getattr(OpenAIBackendAPI._upload_image, "_aigpt_wire", False) is True
    assert getattr(
        OpenAIBackendAPI._start_image_generation, "_aigpt_wire", False) is True
