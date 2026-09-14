"""I2I engine behavior: mode inference, aliases, enhance branch, conversation_id."""
from __future__ import annotations

import base64
from unittest.mock import patch

import pytest

from aigpt.engine.result import GenerateResult

# Real 2x2 PNG so Pillow can read dimensions.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEElEQVR4nGP8zwACTGCSAQANHQEDgslx/wAAAABJRU5ErkJggg==")
_PNG_B64 = base64.b64encode(_PNG).decode()  # base64 string for b64_json field
_DATA_URL = "data:image/png;base64," + _PNG_B64


class _FakeImgOutput:
    """Minimal duck-typed ImageOutput carrying conversation_id."""

    def __init__(self, kind="result", data=None, text="", conversation_id=""):
        self.kind = kind
        self.data = data or []
        self.text = text
        self.conversation_id = conversation_id


def _run(*, prompt="make background purple", **kw):
    from aigpt.engine.generate import generate_image

    with patch("aigpt.engine.generate.encode_images",
               side_effect=lambda imgs: ["enc"] * len(imgs)), \
         patch("aigpt.engine.generate.stream_image_outputs_with_pool",
               return_value=iter([_FakeImgOutput(data=[{"b64_json": _PNG_B64}],
                                                 conversation_id="conv-7")])):
        return generate_image(prompt, **kw)


def _run_edit(**kw):
    kw.setdefault("ref_images", [_DATA_URL])
    kw.setdefault("enhance", False)
    return _run(**kw)


def test_generate_without_refs_no_overlay_and_no_refs():
    from aigpt.engine.generate import generate_image

    captured = {}

    def _fake_stream(request):
        captured["images"] = request.images
        captured["size"] = request.size
        return iter([_FakeImgOutput(data=[{"b64_json": _PNG_B64}])])

    with patch("aigpt.engine.generate.encode_images",
               return_value=["enc"]), \
         patch("aigpt.engine.generate.stream_image_outputs_with_pool",
               side_effect=_fake_stream), \
         patch("aigpt.engine.generate.get_pool",
               return_value=type("P", (), {"current_token": lambda self: "tok"})()):
        res = generate_image("a cat")
    assert res.paths
    assert captured["images"] is None
    assert captured["size"] == "1920x1080"


def test_generate_rejects_refs():
    with pytest.raises(ValueError, match="mode='generate'"):
        _run(mode="generate", ref_images=[_DATA_URL])


def test_edit_default_inferred_with_ref():
    captured = {}

    def _fake_stream(request):
        captured["prompt"] = request.prompt
        captured["images"] = request.images
        return iter([_FakeImgOutput(data=[{"b64_json": _PNG_B64}])])

    with patch("aigpt.engine.generate.encode_images",
               side_effect=lambda imgs: ["enc"] * len(imgs)), \
         patch("aigpt.engine.generate.stream_image_outputs_with_pool",
               side_effect=_fake_stream), \
         patch("aigpt.engine.generate._ref_dimensions", return_value=(1, 1)):
        from aigpt.engine.generate import generate_image
        res = generate_image("make background purple", ref_images=[_DATA_URL],
                             enhance=False)
    assert res.paths
    assert "Edit the attached image" in captured["prompt"]
    assert captured["images"] == ["enc"]


def test_ref_image_alias_merges_to_list():
    captured = {}

    def _fake_stream(request):
        captured["images"] = request.images
        return iter([_FakeImgOutput(data=[{"b64_json": _PNG_B64}])])

    with patch("aigpt.engine.generate.encode_images",
               side_effect=lambda imgs: ["enc"] * len(imgs)), \
         patch("aigpt.engine.generate.stream_image_outputs_with_pool",
               side_effect=_fake_stream), \
         patch("aigpt.engine.generate._ref_dimensions", return_value=(1, 1)):
        from aigpt.engine.generate import generate_image
        generate_image("x", ref_image=_DATA_URL, enhance=False)
    assert captured["images"] == ["enc"]


def test_both_alias_and_list_rejected():
    with pytest.raises(ValueError, match="either ref_image or ref_images"):
        _run(ref_images=[_DATA_URL], ref_image=_DATA_URL)


def test_edit_compose_overlay_for_two_refs():
    captured = {}

    def _fake_stream(request):
        captured["prompt"] = request.prompt
        return iter([_FakeImgOutput(data=[{"b64_json": _PNG_B64}])])

    with patch("aigpt.engine.generate.encode_images",
               side_effect=lambda imgs: ["enc"] * len(imgs)), \
         patch("aigpt.engine.generate.stream_image_outputs_with_pool",
               side_effect=_fake_stream), \
         patch("aigpt.engine.generate._ref_dimensions", return_value=(1, 1)):
        from aigpt.engine.generate import generate_image
        generate_image("put person into scene",
                       ref_images=[_DATA_URL, _DATA_URL], enhance=False)
    assert "Combine the attached images" in captured["prompt"]
    assert "primary subject" not in captured["prompt"]


def test_edit_does_not_call_t2i_enhance():
    """edit + enhance=True must NOT expand via the T2I text enhancer."""
    with patch("aigpt.engine.generate.enhance_prompt",
               side_effect=AssertionError("T2I enhance must not run for edit")) as m, \
         patch("aigpt.engine.generate.encode_images",
               side_effect=lambda imgs: ["enc"]), \
         patch("aigpt.engine.generate.stream_image_outputs_with_pool",
               return_value=iter([_FakeImgOutput(data=[{"b64_json": _PNG_B64}])])), \
         patch("aigpt.engine.generate._ref_dimensions", return_value=(1, 1)):
        _run_edit(enhance=True)
    m.assert_not_called()


def test_style_overlay_injected_for_style_mode():
    captured = {}

    def _fake_stream(request):
        captured["prompt"] = request.prompt
        return iter([_FakeImgOutput(data=[{"b64_json": _PNG_B64}])])

    with patch("aigpt.engine.generate.encode_images",
               side_effect=lambda imgs: ["enc"]), \
         patch("aigpt.engine.generate.stream_image_outputs_with_pool",
               side_effect=_fake_stream), \
         patch("aigpt.engine.generate._ref_dimensions", return_value=(1, 1)):
        from aigpt.engine.generate import generate_image
        generate_image("slide about coffee", ref_images=[_DATA_URL],
                       mode="style", enhance=False)
    assert "DESIGN-STYLE" in captured["prompt"]


def test_conversation_id_captured():
    res = _run_edit()
    assert isinstance(res, GenerateResult)
    assert res.conversation_id == "conv-7"
    assert isinstance(res.paths, tuple)


def test_chinese_engine_message_stripped_from_error():
    """A Chinese message from the engine must not reach the caller as-is."""
    from aigpt.engine.generate import generate_image

    with patch("aigpt.engine.generate.encode_images",
               side_effect=lambda imgs: ["enc"]), \
         patch("aigpt.engine.generate.stream_image_outputs_with_pool",
               return_value=iter([_FakeImgOutput(kind="message",
                                                 text="ChatGPT 生图超时（已等待 120 秒）")])), \
         patch("aigpt.engine.generate._ref_dimensions", return_value=(1, 1)), \
         pytest.raises(RuntimeError, match="image generation produced no images") as ei:
        generate_image("x", ref_images=[_DATA_URL], enhance=False)
    assert "生图超时" not in str(ei.value)


def test_source_aspect_uses_first_ref_dimensions():
    captured = {}

    def _fake_stream(request):
        captured["size"] = request.size
        return iter([_FakeImgOutput(data=[{"b64_json": _PNG_B64}])])

    with patch("aigpt.engine.generate.encode_images",
               side_effect=lambda imgs: ["enc"]), \
         patch("aigpt.engine.generate.stream_image_outputs_with_pool",
               side_effect=_fake_stream), \
         patch("aigpt.engine.generate._ref_dimensions", return_value=(1920, 1080)):
        from aigpt.engine.generate import generate_image
        generate_image("x", ref_images=[_DATA_URL], aspect="source", enhance=False)
    assert captured["size"] == "1920x1080"


def test_quality_passed_to_request():
    captured = {}

    def _fake_stream(request):
        captured["quality"] = request.quality
        return iter([_FakeImgOutput(data=[{"b64_json": _PNG_B64}])])

    with patch("aigpt.engine.generate.encode_images",
               side_effect=lambda imgs: ["enc"]), \
         patch("aigpt.engine.generate.stream_image_outputs_with_pool",
               side_effect=_fake_stream):
        from aigpt.engine.generate import generate_image
        generate_image("x", quality="high", enhance=False)
    assert captured["quality"] == "high"


def test_transparent_hint_appended_for_edit():
    captured = {}

    def _fake_stream(request):
        captured["prompt"] = request.prompt
        return iter([_FakeImgOutput(data=[{"b64_json": _PNG_B64}])])

    with patch("aigpt.engine.generate.encode_images",
               side_effect=lambda imgs: ["enc"]), \
         patch("aigpt.engine.generate.stream_image_outputs_with_pool",
               side_effect=_fake_stream), \
         patch("aigpt.engine.generate._ref_dimensions", return_value=(1, 1)):
        from aigpt.engine.generate import generate_image
        generate_image("logo", ref_images=[_DATA_URL], transparent=True,
                       enhance=False)
    assert "transparent background" in captured["prompt"]


def test_build_image_prompt_has_no_chinese():
    """size/quality hints must not inject Chinese text into the model prompt."""
    import re

    import aigpt._vendor_path  # noqa: F401  (vendor on sys.path)
    from services.protocol.conversation import build_image_prompt

    out = build_image_prompt("x", "1024x1536", "auto")
    assert re.search(r"[一-鿿]", out) is None, f"prompt contains CJK: {out!r}"
    assert "Output image size: 1024x1536." in out
    assert "Output image quality: auto." in out


def test_build_image_prompt_no_size_returns_prompt_verbatim():
    import aigpt._vendor_path  # noqa: F401
    from services.protocol.conversation import build_image_prompt

    assert build_image_prompt("x", None, None) == "x"
