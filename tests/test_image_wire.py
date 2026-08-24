"""Tests for engine/image_wire.py (PR A: wire alignment).

Golden bodies mirror the vendored `_start_image_generation` payload shape
exactly (src/aigpt/_vendor/services/openai_backend_api.py), so the rewrite
tests pin the real transformation, not a paraphrase of it.
"""
from __future__ import annotations

import copy

import pytest

from aigpt.engine import image_wire
from aigpt.engine.image_wire import Followup


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
