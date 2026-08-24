"""Regression tests for review fixes: M1 (OAuth state verification) and
H4 (ref_image URL-strict)."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from aigpt.auth.oauth_login import (
    build_authorize_url,
    complete,
    extract_code,
)

# A real 1x1 PNG so the engine can read dimensions for the request.
_PNG_1x1 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR42mP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"

# ---------- M1: state verification ----------


def test_build_authorize_url_returns_url_verifier_state():
    url, verifier, state = build_authorize_url("someone@example.com")
    assert url.startswith("https://auth.openai.com/api/accounts/authorize?")
    assert "code_challenge=" in url
    assert "state=" in url and state
    assert len(verifier) > 20
    assert "login_hint=someone%40example.com" in url


def test_extract_code_rejects_state_mismatch():
    url, _, state = build_authorize_url()
    # Callback with a DIFFERENT state (forged/stale) must be rejected (M1).
    other = url.replace("state=" + state, "state=forged")
    forged = other + "&code=abc123"
    with pytest.raises(RuntimeError, match="state mismatch"):
        extract_code(forged, expected_state=state)


def test_extract_code_accepts_matching_state():
    url, _, state = build_authorize_url()
    callback = url + "&code=abc123"
    assert extract_code(callback, expected_state=state) == "abc123"


def test_extract_code_missing_code_raises():
    url, _, state = build_authorize_url()
    with pytest.raises(RuntimeError, match="no \\?code="):
        extract_code(url, expected_state=state)


def test_extract_code_bare_code_paste():
    assert extract_code("abc123") == "abc123"


def test_complete_removes_pending_file_even_on_failure():
    """M1: pending file must be unlinked even when token exchange fails."""
    with TemporaryDirectory() as tmp:
        pending = Path(tmp) / "login_pending.json"
        pending.write_text(
            json.dumps({"code_verifier": "v1", "state": "s1"}), encoding="utf-8"
        )
        with patch("aigpt.auth.oauth_login._pending_path",
                   return_value=pending), \
             patch("aigpt.auth.oauth_login.exchange_code",
                   side_effect=RuntimeError("exchange exploded")), \
             pytest.raises(RuntimeError, match="exchange exploded"):
            complete("https://x/callback?state=s1&code=c1")
        assert not pending.exists(), "pending file must be cleaned up on failure"


# ---------- H4: ref_image URL-strict ----------


def _gen():
    """Import generate_image lazily (heavy import chain)."""
    from aigpt.engine.generate import generate_image
    return generate_image


def test_ref_image_rejects_local_path():
    with pytest.raises(ValueError, match="https:// URL"):
        _gen()("slide about coffee", ref_image="C:/Users/me/photo.png",
               enhance=False)


def test_ref_image_rejects_http_and_file_urls():
    for bad in ("http://example.com/img.png", "file:///C:/photo.png",
                "ftp://example.com/img.png"):
        with pytest.raises(ValueError, match="https:// URL"):
            _gen()("slide about coffee", ref_image=bad, enhance=False)


def test_ref_image_accepts_https_url_then_fetches():
    """A valid https URL must go down the fetch path (curl_cffi + encode),
    never fail validation. Mock the fetch so no network is hit."""
    import base64 as _b64

    class FakeResp:
        status_code = 200
        content = _b64.b64decode(_PNG_1x1)

        def __init__(self) -> None:
            self.headers = {"Content-Type": "image/png"}

        def raise_for_status(self):
            return None

    with patch("aigpt.engine.refs.curl_requests.get", return_value=FakeResp()), \
         patch("aigpt.engine.generate.encode_images", return_value=["enc"]) as mock_enc, \
         patch("aigpt.engine.generate.stream_image_outputs_with_pool", return_value=iter([])) as mock_stream, \
         patch("aigpt.engine.generate._collect_saved", return_value=(["C:/fake.png"], "", "conv-1")):
        res = _gen()("slide about coffee", ref_image="https://example.com/img.png",
                     enhance=False, mode="style")
        assert res.paths == ("C:/fake.png",)
        assert res.conversation_id == "conv-1"
        mock_enc.assert_called_once()
        assert mock_stream.called


def test_ref_image_https_default_edit_not_style_only():
    """Default mode with an https ref is EDIT (not style-only). The STYLE-ONLY
    overlay must appear only when mode='style' is explicit."""
    import base64 as _b64

    class FakeResp:
        status_code = 200
        content = _b64.b64decode(_PNG_1x1)

        def __init__(self) -> None:
            self.headers = {"Content-Type": "image/png"}

        def raise_for_status(self):
            return None

    captured: dict = {}

    def _fake_stream(request):
        captured["prompt"] = request.prompt
        return iter([])

    with patch("aigpt.engine.refs.curl_requests.get", return_value=FakeResp()), \
         patch("aigpt.engine.generate.stream_image_outputs_with_pool",
               side_effect=_fake_stream) as mock_stream, \
         patch("aigpt.engine.generate._collect_saved",
               return_value=(["C:/fake.png"], "", "")):
        _gen()("change the background to blue",
               ref_image="https://example.com/img.png", enhance=False)
    assert "DESIGN-STYLE" not in captured["prompt"]
    assert "Edit the attached image" in captured["prompt"]
    assert mock_stream.called
