"""Contract tests for the embedded Antigravity OAuth and image provider."""
from __future__ import annotations

import base64
import json
import time

import pytest

from aigpt import api
from aigpt.auth import tokens
from aigpt.antigravity import store
from aigpt.antigravity.client import (
    AntigravityClient,
    AntigravityHTTPError,
    _walk_images,
)
from aigpt.antigravity.service import AntigravityService


@pytest.fixture
def ag_config(tmp_path, monkeypatch):
    monkeypatch.setattr(tokens, "_config_dir", lambda: tmp_path)
    return tmp_path


def test_auth_url_contains_google_scopes_and_local_callback():
    from urllib.parse import parse_qs, urlsplit

    query = parse_qs(urlsplit(AntigravityClient().auth_url("state-1")).query)
    assert query["state"] == ["state-1"]
    assert query["redirect_uri"] == ["http://localhost:51121/oauth-callback"]
    assert "userinfo.email" in query["scope"][0]
    assert query["access_type"] == ["offline"]


def test_project_id_onboarding_reads_nested_response():
    calls = []

    def request(method, url, **kwargs):
        calls.append(url)
        if url.endswith("loadCodeAssist"):
            return 200, json.dumps({"allowedTiers": [{"id": "free-tier", "isDefault": True}]}).encode()
        return 200, json.dumps({"done": True, "response": {"projectId": "project-123"}}).encode()

    client = AntigravityClient(request_fn=request, sleep_fn=lambda _: None)
    assert client.project_id("access") == "project-123"
    assert any(url.endswith("onboardUser") for url in calls)


def test_token_exchange_refresh_and_userinfo_use_mocked_http():
    requests = []

    def request(method, url, **kwargs):
        requests.append((method, url, kwargs["body"]))
        if "userinfo" in url:
            return 200, b'{"email":"person@example.com"}'
        return 200, b'{"access_token":"new-access","refresh_token":"new-refresh","expires_in":3600}'

    client = AntigravityClient(request_fn=request)
    exchanged = client.exchange_code("oauth-code", "http://localhost:51121/oauth-callback")
    refreshed = client.refresh("old-refresh")
    assert exchanged["access_token"] == "new-access"
    assert refreshed["refresh_token"] == "new-refresh"
    assert client.user_email("new-access") == "person@example.com"
    assert b"oauth-code" in requests[0][2]
    assert b"old-refresh" in requests[1][2]


def test_generate_payload_and_inline_data_are_not_duplicated():
    payloads = []
    encoded = base64.b64encode(b"png").decode()

    def request(method, url, **kwargs):
        if url.endswith("generateContent"):
            payloads.append(json.loads(kwargs["body"]))
            return 200, json.dumps({"candidates": [{"content": {"parts": [
                {"inlineData": {"mimeType": "image/png", "data": encoded}}
            ]}}]}).encode()
        raise AssertionError(url)

    image = "data:image/png;base64," + base64.b64encode(b"ref").decode()
    result = AntigravityClient(request_fn=request).generate(
        "token", "project", "gemini-image", "draw", "1:1", "2K", 2, [image]
    )
    request_payload = payloads[0]
    assert request_payload["requestType"] == "image_gen"
    assert request_payload["request"]["generationConfig"]["imageConfig"]["imageSize"] == "2K"
    assert request_payload["request"]["generationConfig"]["candidateCount"] == 2
    assert request_payload["request"]["contents"][0]["parts"][1]["inlineData"]["mimeType"] == "image/png"
    assert result == [("image/png", b"png")]
    assert _walk_images({"inline_data": {"mime_type": "image/jpeg", "data": encoded}}) == [
        ("image/jpeg", b"png")
    ]


def test_store_is_versioned_atomic_and_never_exposes_tokens(ag_config):
    store.upsert({"email": "a@example.com", "access_token": "access", "refresh_token": "refresh"})
    raw = json.loads(store.auth_path().read_text(encoding="utf-8"))
    assert raw["version"] == store.CURRENT_VERSION
    # chmod(0600) is applied by the implementation; Windows may not expose
    # POSIX mode bits, so only assert the atomic target exists here.
    assert store.auth_path().exists()
    public = store.load()[0]
    assert public["access_token"] == "access"  # internal store retains it for refresh
    from aigpt.antigravity.service import _public_account
    assert "access_token" not in _public_account(public)
    assert "refresh_token" not in _public_account(public)


def test_pool_sticky_and_failover_only_on_quota(ag_config):
    future = time.time() + 3600
    store.save([
        {"email": "one@example.com", "access_token": "a", "refresh_token": "ra", "expires_at": future,
         "project_id": "p1", "models": [{"id": "image-model", "supports_2k": True}]},
        {"email": "two@example.com", "access_token": "b", "refresh_token": "rb", "expires_at": future,
         "project_id": "p2", "models": [{"id": "image-model", "supports_2k": True}]},
    ])

    class FakeClient:
        def __init__(self):
            self.calls = []

        def credits(self, token):
            return {"status": "available"}

        def generate(self, token, project, model, prompt, aspect, resolution, n, refs):
            self.calls.append(token)
            if token == "a":
                raise AntigravityHTTPError(429, "RESOURCE_EXHAUSTED")
            return [("image/png", b"ok")]

    client = FakeClient()
    service = AntigravityService(client=client)
    result = service.generate({"prompt": "cat", "model": "image-model", "resolution": "1K"})
    assert result["account_email"] == "two@example.com"
    assert client.calls == ["a", "b"]
    # The next request is sticky to the successful account.
    service.generate({"prompt": "dog", "model": "image-model", "resolution": "1K"})
    assert client.calls[-1] == "b"


def test_pool_does_not_failover_on_ambiguous_error(ag_config):
    future = time.time() + 3600
    store.save([{"email": "one@example.com", "access_token": "a", "expires_at": future,
                 "project_id": "p", "models": [{"id": "image-model"}]}])

    class FakeClient:
        def credits(self, token):
            return {"status": "unknown"}

        def generate(self, *args):
            raise TimeoutError("upstream timeout")

    with pytest.raises(TimeoutError):
        AntigravityService(client=FakeClient()).generate({"prompt": "cat", "model": "image-model"})


def test_antigravity_error_mapping_and_2k_message():
    error = AntigravityHTTPError(400, "image_size 2K unsupported")
    status, body = api._build_antigravity_error(error)
    assert status == 400
    assert "try 1K" in body["error"]
