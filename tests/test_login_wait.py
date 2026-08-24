"""Unit tests for the login callback receiver (aigpt.login_wait).

oauth_login.complete is monkeypatched (never touches the network).
"""
from __future__ import annotations

import json

import pytest

from aigpt import login_wait

# ------------------------------------------------------------ payload parsing

def test_parse_callback_payload_valid():
    url = "https://platform.openai.com/auth/callback?code=abc&state=xyz"
    body = json.dumps({"url": url}).encode()
    assert login_wait.parse_callback_payload(body) == url


def test_parse_callback_payload_strips_whitespace():
    url = "https://platform.openai.com/auth/callback?code=abc&state=xyz"
    body = json.dumps({"url": f"  {url}  "}).encode()
    assert login_wait.parse_callback_payload(body) == url


def test_parse_callback_payload_rejects_bad_json():
    with pytest.raises(ValueError, match="JSON"):
        login_wait.parse_callback_payload(b"{oops")


def test_parse_callback_payload_rejects_non_dict():
    with pytest.raises(TypeError, match="JSON"):
        login_wait.parse_callback_payload(b"[1, 2]")


def test_parse_callback_payload_rejects_missing_url():
    with pytest.raises(ValueError, match="url"):
        login_wait.parse_callback_payload(json.dumps({"nourl": 1}).encode())


def test_parse_callback_payload_rejects_non_https():
    with pytest.raises(ValueError, match="https"):
        login_wait.parse_callback_payload(
            json.dumps({"url": "http://evil.example/x"}).encode())


def test_parse_callback_payload_rejects_oversize():
    url = "https://platform.openai.com/auth/callback?x=" + "a" * (login_wait._MAX_BODY)
    body = json.dumps({"url": url}).encode()
    assert len(body) > login_wait._MAX_BODY
    with pytest.raises(ValueError, match="too large"):
        login_wait.parse_callback_payload(body)


# ------------------------------------------------------------ handler behavior

class _FakeServer:
    def __init__(self, token: str = "testtoken16chars"):
        import queue
        self.result_q = queue.Queue(maxsize=1)
        self._session_token = token


class _FakeHandler:
    """Minimal BaseHTTPRequestHandler stand-in: captures the JSON response."""

    def __init__(self, path: str, body: bytes, complete,
                 token: str = "testtoken16chars"):
        self.path = path
        self.rfile = _BytesReader(body)
        self.headers = {"Content-Length": str(len(body))}
        self.complete = complete
        self.server = _FakeServer(token)
        self.sent: tuple[int, dict] | None = None
        login_wait.CallbackHandler._json = self._json  # capture, not real socket

    def _json(self, status: int, payload: dict) -> None:
        self.sent = (status, payload)

    def do_post(self):
        handler = login_wait.CallbackHandler.__new__(login_wait.CallbackHandler)
        handler.path = self.path
        handler.rfile = self.rfile
        handler.headers = self.headers
        handler.server = self.server
        handler._json = self._json  # instance-level override
        handler.do_POST()


class _BytesReader:
    def __init__(self, data: bytes):
        self._data = data

    def read(self, length: int) -> bytes:
        return self._data[:length]


def _callback_body(url: str = "https://platform.openai.com/auth/callback?code=a&state=s",
                   token: str = "testtoken16chars") -> bytes:
    return json.dumps({"url": url, "token": token}).encode()


def test_handler_200_and_pushes_result(monkeypatch):
    monkeypatch.setattr(login_wait.oauth_login, "complete",
                        lambda url: {"email": "a@b.c", "access_token": "t"})
    h = _FakeHandler("/callback", _callback_body(), None)
    h.do_post()
    status, payload = h.sent
    assert status == 200
    assert payload["ok"] is True
    assert payload["email"] == "a@b.c"
    assert not h.server.result_q.empty()


def test_handler_400_on_complete_error(monkeypatch):
    def boom(url):
        raise RuntimeError("no pending login - run `aigpt login` first")

    monkeypatch.setattr(login_wait.oauth_login, "complete", boom)
    h = _FakeHandler("/callback", _callback_body(), None)
    h.do_post()
    status, payload = h.sent
    assert status == 400
    assert payload["ok"] is False
    assert "no pending login" in payload["error"]


def test_handler_403_on_wrong_token():
    h = _FakeHandler("/callback",
                     _callback_body(token="wrong-token"),
                     None,
                     token="real-token-abcdef123456")
    h.do_post()
    status, payload = h.sent
    assert status == 403
    assert "invalid token" in payload["error"]
    assert h.server.result_q.empty()  # nothing pushed on auth failure


def test_handler_400_on_bad_payload():
    h = _FakeHandler("/callback", b"{bad json", None)
    h.do_post()
    status, body = h.sent
    assert status == 400
    assert body["ok"] is False


def test_handler_404_on_unknown_path():
    h = _FakeHandler("/nope", b"{}", None)
    h.do_post()
    status, _ = h.sent
    assert status == 404


def test_authorize_returns_pending_url():
    """GET /authorize serves the pending login's authorize URL to the panel."""
    sent = []

    def fake_json(status, payload):
        sent.append((status, payload))

    handler = login_wait.CallbackHandler.__new__(login_wait.CallbackHandler)
    handler.path = "/authorize"
    handler.headers = {"Origin": "chrome-extension://abc123"}
    handler._json = fake_json
    handler.server = _FakeServer()
    handler.server.authorize_url = "https://auth.openai.com/api/accounts/authorize?x=1"
    handler.do_GET()

    status, payload = sent[0]
    assert status == 200
    assert payload["url"] == "https://auth.openai.com/api/accounts/authorize?x=1"


def test_authorize_requires_extension_origin():
    sent = []

    def fake_json(status, payload):
        sent.append((status, payload))

    handler = login_wait.CallbackHandler.__new__(login_wait.CallbackHandler)
    handler.path = "/authorize"
    handler.headers = {"Origin": "https://evil.example"}
    handler._json = fake_json
    handler.server = _FakeServer()
    handler.do_GET()

    status, payload = sent[0]
    assert status == 403


def test_authorize_503_when_no_pending_login():
    sent = []

    def fake_json(status, payload):
        sent.append((status, payload))

    handler = login_wait.CallbackHandler.__new__(login_wait.CallbackHandler)
    handler.path = "/authorize"
    handler.headers = {"Origin": "chrome-extension://abc123"}
    handler._json = fake_json
    handler.server = _FakeServer()  # no authorize_url attribute
    handler.do_GET()

    status, payload = sent[0]
    assert status == 503
    assert "uv run aigpt login --wait" in payload["error"]


def test_handshake_requires_extension_origin_and_nonce():
    sent = []

    def fake_json(status, payload):
        sent.append((status, payload))

    def do_get_with_headers(headers, token="handshake-token-xyz"):
        sent.clear()
        handler = login_wait.CallbackHandler.__new__(login_wait.CallbackHandler)
        handler.path = "/handshake"
        handler.headers = headers
        handler._json = fake_json
        handler.server = _FakeServer(token)
        handler.do_GET()

    do_get_with_headers({"Origin": "https://evil.example", "X-AIGPT-Nonce": "1"})
    assert sent[0][0] == 403

    do_get_with_headers({"Origin": "chrome-extension://abc123", "X-AIGPT-Nonce": "1"})
    assert sent[0][0] == 200
    assert sent[0][1]["token"] == "handshake-token-xyz"

    do_get_with_headers({"Origin": "chrome-extension://abc123", "X-AIGPT-Nonce": ""})
    assert sent[0][0] == 403

    # Service worker fetch() does not send Origin. Nonce is the proof.
    do_get_with_headers({"X-AIGPT-Nonce": "1"})
    assert sent[0][0] == 200
    assert sent[0][1]["token"] == "handshake-token-xyz"


def test_token_is_per_server_instance():
    """Two live servers must NOT share a token (the old class-attr bug)."""
    server_a = login_wait._CallbackServer(("127.0.0.1", 0), login_wait.CallbackHandler,
                                          __import__("queue").Queue(maxsize=1))
    server_b = login_wait._CallbackServer(("127.0.0.1", 0), login_wait.CallbackHandler,
                                          __import__("queue").Queue(maxsize=1))
    try:
        assert server_a._session_token
        assert server_b._session_token
        assert server_a._session_token != server_b._session_token
    finally:
        server_a.server_close()
        server_b.server_close()

# ------------------------------------------------------------ session primitives

def test_start_login_session_on_port_0(monkeypatch):
    from aigpt.login_wait import (
        login_session_status,
        start_login_session,
        stop_login_session,
    )
    # Monkeypatch the pending-file path so oauth_login.complete won't fail.
    monkeypatch.setattr(login_wait.oauth_login, "complete",
                        lambda url: {"email": "a@b.c", "access_token": "t"})
    session = start_login_session("https://auth.example/auth", port=0)
    try:
        st = login_session_status(session)
        assert st["state"] == "waiting"
        # Push a fake result via the queue.
        session.result_q.put_nowait({"email": "done@x", "access_token": "tk"})
        st2 = login_session_status(session)
        assert st2["state"] == "done"
        assert st2["email"] == "done@x"
    finally:
        stop_login_session(session)


def test_stop_releases_port(monkeypatch):
    from aigpt.login_wait import start_login_session, stop_login_session
    session = start_login_session("https://auth.example/auth", port=0)
    stop_login_session(session)
    # Now bind the same port again — should succeed (no SO_EXCLUSIVEADDRUSE issue
    # on ephemeral port 0, but we just verify the socket is freed).
    released = session.server.server_address[1]
    session2 = start_login_session("https://auth.example/auth", port=released)
    stop_login_session(session2)


def test_session_error_state(monkeypatch):
    from aigpt.login_wait import (
        login_session_status,
        start_login_session,
        stop_login_session,
    )
    session = start_login_session("https://auth.example/auth", port=0)
    try:
        session.result_q.put_nowait(RuntimeError("oops"))
        st = login_session_status(session)
        assert st["state"] == "error"
        assert "oops" in st["error"]
    finally:
        stop_login_session(session)
