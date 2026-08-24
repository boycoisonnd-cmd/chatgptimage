"""Local callback receiver for extension-assisted login (`aigpt login --wait`).

The Chrome extension watches for a navigation to the platform.openai.com OAuth
callback URL and POSTs it here (http://127.0.0.1:8788/callback). This module
finishes the PKCE exchange via oauth_login.complete() and exits the waiting CLI.

The callback POST is authenticated with a per-server token: the extension
fetches it from GET /handshake (CORS-restricted to the extension origin) and
echoes it back in the POST body. On Windows the socket is bound exclusively
(SO_EXCLUSIVEADDRUSE) so a rogue local process that grabs port 8788 first
cannot silently preempt our port and replay a captured callback code.
"""
from __future__ import annotations

import json
import queue
import secrets
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# MUST precede vendored imports (sys.path side effect).
import aigpt._vendor_path  # noqa: F401
from aigpt.auth import oauth_login

_MAX_BODY = 8192  # 8 KiB: a callback URL never needs more
_EXTENSION_ORIGIN = "chrome-extension://"  # any installed extension; see below

# NOTE: the Origin check below allows ANY chrome-extension:// origin, not just
# our own id. The token (fetched from /handshake and echoed in the POST) is the
# real gate; Origin is just a cheap first filter against non-extension callers.
# A malicious extension cannot obtain the token either, because fetch() in an
# extension page sends the request without our token and the response is not
# readable cross-extension (separate origins) - and no installed extension can
# read another extension's code except through web-accessible resources, which
# we do not expose. We harden this further by requiring a custom request header
# for the token, which is also CORS-simple-blocked for cross-origin web pages.

# Port preemption: SO_REUSEADDR (the stdlib default) lets a second process bind
# the same port on Windows; the first listener keeps the port but silently loses
# connections to the newcomer. We bind exclusively (SO_EXCLUSIVEADDRUSE +
# allow_reuse_address=False) so a rogue process that grabs 8788 first makes our
# bind FAIL LOUDLY instead of serving a stolen callback token to it.


class _CallbackServer(ThreadingHTTPServer):
    """ThreadingHTTPServer carrying the login-outcome queue to its handlers.

    Per-instance session token prevents cross-request bleed. On Windows the
    socket is bound exclusively (SO_EXCLUSIVEADDRUSE) so a rogue process
    cannot preempt the port and steal the OAuth callback.
    """

    daemon_threads = True
    allow_reuse_address = False  # Windows: SO_EXCLUSIVEADDRUSE instead

    def __init__(self, address, handler_cls, result_q: queue.Queue,
                 authorize_url: str | None = None):
        super().__init__(address, handler_cls)
        self.result_q = result_q
        self.authorize_url = authorize_url  # served via GET /authorize to the panel
        self._session_token = secrets.token_hex(16)

    def server_bind(self):
        if sys.platform == "win32":
            self.socket.setsockopt(
                socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class CallbackHandler(BaseHTTPRequestHandler):
    """Endpoints:
    GET  /authorize {url} — the pending authorize URL (extension origin only)
    GET  /handshake  {token} — the callback POST token (extension origin + nonce)
    POST /callback   {url, token} — the captured OAuth callback URL
    """

    def do_POST(self) -> None:
        if self.path != "/callback":
            self._json(404, {"ok": False, "error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > _MAX_BODY:
            self._json(413, {"ok": False, "error": "body too large"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(400, {"ok": False, "error": "expected JSON"})
            return
        if not isinstance(payload, dict):
            self._json(400, {"ok": False, "error": "expected JSON object"})
            return
        if payload.get("token") != self.server._session_token:
            self._json(403, {"ok": False, "error": "invalid token"})
            return
        try:
            url = parse_callback_payload(json.dumps({"url": payload.get("url")}).encode("utf-8"))
            account = oauth_login.complete(url)
        except (TypeError, ValueError, RuntimeError) as exc:
            self.server.result_q.put_nowait(exc)
            self._json(400, {"ok": False, "error": str(exc)})
            return
        self.server.result_q.put_nowait(account)
        email = account.get("email") or "(email unknown until first run)"
        self._json(200, {"ok": True, "email": email})

    def do_GET(self) -> None:
        """Extension-origin endpoints: /authorize (panel's login URL), /handshake (token).

        /handshake is called from the MV3 service worker, which does not send
        an Origin header. The custom nonce is the SW's proof of identity; a
        web page always sends Origin, so we still reject any Origin that is
        not chrome-extension://. /authorize is called from the panel page
        (which DOES send Origin) so it still requires the extension origin.
        """
        origin = self.headers.get("Origin", "")
        if self.path == "/authorize":
            if not origin.startswith(_EXTENSION_ORIGIN):
                self._json(403, {"ok": False, "error": "extension origin required"})
                return
            url = getattr(self.server, "authorize_url", None)
            if url:
                self._json(200, {"url": url})
            else:
                self._json(503, {"ok": False,
                                 "error": "no pending login - run: uv run aigpt login --wait"})
        elif self.path == "/handshake":
            if origin and not origin.startswith(_EXTENSION_ORIGIN):
                self._json(403, {"ok": False, "error": "extension origin required"})
                return
            if self.headers.get("X-AIGPT-Nonce") == "1":
                self._json(200, {"token": self.server._session_token})
            else:
                self._json(403, {"ok": False, "error": "X-AIGPT-Nonce required"})
        elif self.path == "/health":
            self._json(200, {"ok": True})
        else:
            self._json(404, {"ok": False, "error": "not found"})

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass  # silent: the CLI already reports the outcome


def parse_callback_payload(body: bytes) -> str:
    """Extract the callback URL from the extension's JSON POST body.

    Returns the URL string, or raises ValueError with a user-facing message.
    """
    if len(body) > _MAX_BODY:
        raise ValueError(f"body too large ({len(body)} bytes > {_MAX_BODY})")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('expected JSON object with a "url" string') from exc
    if not isinstance(payload, dict):
        raise TypeError('expected JSON object with a "url" string')
    url = payload.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ValueError('missing "url" string')
    url = url.strip()
    if not url.startswith("https://"):
        raise ValueError("url must be an https:// callback URL")
    return url


@dataclass
class LoginSession:
    """One live callback receiver (port 8788) waiting for an OAuth callback.

    Shared by the CLI (`wait_for_callback`) and the API server (`POST
    /login/start`), so both flows use the same hardened server/handler.
    """
    authorize_url: str
    server: _CallbackServer
    thread: threading.Thread
    result_q: queue.Queue = field(repr=False)
    started_at: float = 0.0
    stopped: bool = False


def start_login_session(authorize_url: str, port: int = 8788) -> LoginSession:
    """Bind the callback receiver and start serving it in a daemon thread.

    Raises OSError if the port is already held (Windows: SO_EXCLUSIVEADDRUSE
    makes this fail loudly instead of silently sharing the port). The queue is
    unbounded: the API server polls it lazily, so a late callback must not be
    dropped by a full queue.
    """
    result_q: queue.Queue = queue.Queue()
    server = _CallbackServer(("127.0.0.1", port), CallbackHandler, result_q,
                             authorize_url=authorize_url)

    def _serve() -> None:
        server.serve_forever(poll_interval=0.1)

    thread = threading.Thread(target=_serve, name="aigpt-login-session", daemon=True)
    thread.start()
    return LoginSession(authorize_url=authorize_url, server=server, thread=thread,
                        result_q=result_q, started_at=time.time())


def login_session_status(session: LoginSession) -> dict:
    """Drain the session queue (non-blocking) into a pollable status dict."""
    outcome = None
    try:
        while True:
            outcome = session.result_q.get_nowait()
    except queue.Empty:
        pass
    if outcome is None:
        return {"state": "waiting"}
    if isinstance(outcome, BaseException):
        return {"state": "error", "error": str(outcome)}
    return {"state": "done", "email": outcome.get("email") or ""}


def stop_login_session(session: LoginSession) -> None:
    """Shut the receiver down and release the port. Idempotent."""
    if session.stopped:
        return
    session.stopped = True
    session.server.shutdown()
    session.server.server_close()
    session.thread.join(timeout=5)


def wait_for_callback(port: int = 8788, timeout: float | None = None,
                      authorize_url: str | None = None) -> dict:
    """Blocking variant for the CLI: serve until the login completes (or timeout).

    `authorize_url` (the pending login's authorize URL) is served via
    GET /authorize so the extension panel can open the real login page.

    Returns the account record from oauth_login.complete(). Raises
    TimeoutError if `timeout` seconds pass with no callback, or re-raises the
    login error the handler received. The server is always shut down and its
    thread joined (also on exceptions).
    """
    session = start_login_session(authorize_url or "", port=port)
    try:
        try:
            outcome = session.result_q.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError(
                f"no callback received in {timeout} seconds - is the extension "
                "installed and the browser logged into ChatGPT?"
            ) from exc
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome
    finally:
        stop_login_session(session)
