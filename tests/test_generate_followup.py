"""Phase 3 follow-up engine behavior: conversation_id kwarg + refs-gated fallback."""
from __future__ import annotations

import base64
from unittest.mock import patch

import pytest

from aigpt.engine import image_wire

# Real 2x2 PNG so Pillow can read dimensions.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEElEQVR4nGP8zwACTGCSAQANHQEDgslx/wAAAABJRU5ErkJggg==")
_PNG_B64 = base64.b64encode(_PNG).decode()
_DATA_URL = "data:image/png;base64," + _PNG_B64


class _FakeImgOutput:
    """Minimal duck-typed ImageOutput carrying conversation_id."""

    def __init__(self, kind="result", data=None, text="", conversation_id=""):
        self.kind = kind
        self.data = data or []
        self.text = text
        self.conversation_id = conversation_id


def _ok_output(conversation_id="conv-7"):
    return _FakeImgOutput(data=[{"b64_json": _PNG_B64}],
                          conversation_id=conversation_id)


def test_followup_kwarg_reaches_wire_state_and_is_cleared():
    """conversation_id arms the wire follow-up for the stream, cleared after."""
    from aigpt.engine.generate import generate_image

    seen = {}

    def _fake_stream(request):
        seen["followup"] = image_wire.current_followup()
        return iter([_ok_output()])

    with patch("aigpt.engine.generate.encode_images",
               side_effect=lambda imgs: ["enc"] * len(imgs)), \
         patch("aigpt.engine.generate.stream_image_outputs_with_pool",
               side_effect=_fake_stream):
        res = generate_image("make it blue", conversation_id="conv-1",
                             enhance=False)
    assert res.paths
    assert seen["followup"] is not None
    assert seen["followup"].conversation_id == "conv-1"
    # Cleared in finally after the stream completes.
    assert image_wire.current_followup() is None


def test_followup_state_cleared_even_on_raise():
    from aigpt.engine.generate import generate_image

    def _fake_stream(request):
        raise RuntimeError("boom")

    with patch("aigpt.engine.generate.encode_images",
               side_effect=lambda imgs: ["enc"] * len(imgs)), \
         patch("aigpt.engine.generate.stream_image_outputs_with_pool",
               side_effect=_fake_stream), pytest.raises(RuntimeError, match="boom"):
        generate_image("make it blue", conversation_id="conv-1",
                       enhance=False)
    assert image_wire.current_followup() is None


def test_followup_with_n_gt_1_rejected():
    from aigpt.engine.generate import generate_image
    with pytest.raises(ValueError, match="n=1"):
        generate_image("x", conversation_id="conv-1", n=2, enhance=False)


def test_followup_no_refs_no_overlay_no_enhance():
    """A follow-up without refs this turn sends the instruction as-is."""
    from aigpt.engine.generate import generate_image

    captured = {}

    def _fake_stream(request):
        captured["prompt"] = request.prompt
        captured["images"] = request.images
        return iter([_ok_output()])

    with patch("aigpt.engine.generate.encode_images",
               side_effect=lambda imgs: ["enc"] * len(imgs)), \
         patch("aigpt.engine.generate.stream_image_outputs_with_pool",
               side_effect=_fake_stream):
        generate_image("make it blue", conversation_id="conv-1", enhance=False)

    assert captured["prompt"] == "make it blue"  # no edit overlay, no enhance
    assert captured["images"] is None


def test_followup_with_refs_keeps_edit_overlay():
    from aigpt.engine.generate import generate_image

    captured = {}

    def _fake_stream(request):
        captured["prompt"] = request.prompt
        captured["images"] = request.images
        return iter([_ok_output()])

    with patch("aigpt.engine.generate.encode_images",
               side_effect=lambda imgs: ["enc"] * len(imgs)), \
         patch("aigpt.engine.generate.stream_image_outputs_with_pool",
               side_effect=_fake_stream), \
         patch("aigpt.engine.generate._ref_dimensions", return_value=(1, 1)):
        generate_image("make it blue", conversation_id="conv-1",
                       ref_images=[_DATA_URL], enhance=False)

    # mode inferred "edit" (refs present) -> overlay applied, refs encoded.
    assert "Edit the attached image" in captured["prompt"]
    assert captured["images"] == ["enc"]


def _flag_rejection_then_fail(first_error="upstream rejected"):
    """Return a stream fake that flags a wire rejection on call 1 then raises.

    Mirrors production: the wrapper sets _rejected=True on UpstreamHTTPError,
    then the pool layer flattens that error into a plain exception.
    """
    calls = {"count": 0}

    def _fake_stream(request):
        calls["count"] += 1
        if calls["count"] == 1:
            image_wire._rejected = True  # wrapper flagged the rejection
            raise RuntimeError(first_error)
        return iter([_ok_output("conv-fresh")])

    return _fake_stream, calls


def test_fallback_retries_fresh_on_rejection_with_refs():
    """Rejected follow-up WITH refs retries once as a fresh generation."""
    from aigpt.engine.generate import generate_image

    _fake_stream, calls = _flag_rejection_then_fail()
    images_per_call = []

    def _stream(request):
        images_per_call.append(request.images)
        return _fake_stream(request)

    with patch("aigpt.engine.generate.encode_images",
               side_effect=lambda imgs: ["enc"] * len(imgs)), \
         patch("aigpt.engine.generate.stream_image_outputs_with_pool",
               side_effect=_stream), \
         patch("aigpt.engine.generate._ref_dimensions", return_value=(1, 1)):
        res = generate_image("make it blue", conversation_id="conv-1",
                             ref_images=[_DATA_URL], enhance=False)

    assert calls["count"] == 2                       # retried exactly once
    assert images_per_call == [["enc"], ["enc"]]     # refs re-attached both calls
    assert res.paths
    assert res.conversation_id == "conv-fresh"       # fresh conversation
    assert image_wire.current_followup() is None


def test_rejection_without_refs_raises_loudly_no_retry():
    """Rejected follow-up WITHOUT refs refuses instead of wasting quota."""
    from aigpt.engine.generate import generate_image

    _fake_stream, calls = _flag_rejection_then_fail()

    with patch("aigpt.engine.generate.encode_images",
               side_effect=lambda imgs: ["enc"] * len(imgs)), \
         patch("aigpt.engine.generate.stream_image_outputs_with_pool",
               side_effect=_fake_stream), \
         pytest.raises(RuntimeError, match="reference images"):
        generate_image("make it blue", conversation_id="conv-1",
                       enhance=False)

    assert calls["count"] == 1  # no retry, no second quota spend
    assert image_wire.current_followup() is None


def test_no_fallback_on_non_rejection_errors():
    """A generic engine error (not a flagged rejection) propagates, no retry."""
    from aigpt.engine.generate import generate_image

    calls = {"count": 0}

    def _fake_stream(request):
        calls["count"] += 1
        raise RuntimeError("some other failure")  # _rejected never set

    with patch("aigpt.engine.generate.encode_images",
               side_effect=lambda imgs: ["enc"] * len(imgs)), \
         patch("aigpt.engine.generate.stream_image_outputs_with_pool",
               side_effect=_fake_stream), \
         pytest.raises(RuntimeError, match="some other failure"):
        generate_image("make it blue", conversation_id="conv-1",
                       enhance=False)

    assert calls["count"] == 1
