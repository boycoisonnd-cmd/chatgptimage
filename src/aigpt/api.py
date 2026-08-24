"""Localhost HTTP REST API for image generation (`aigpt-api`).

Lets other projects generate images by POSTing JSON - no MCP client needed.
Bind to 127.0.0.1 only; anything else would expose the account pool to the LAN.

Zero new dependencies: stdlib http.server (the engine is synchronous and the
pool enforces sequential generation, so async buys nothing here).

Security posture (review fixes):
- GET /file serves ONLY images this server generated (id -> path registry),
  never an arbitrary path from the query string.
- /generate_image rejects requests whose Origin header is a web page: CORS
  requires the server to opt in, and we never send Access-Control-Allow-Origin.
  A cross-origin site using a "simple" POST (text/plain, no preflight) is
  rejected here because its Origin is present and not the extension's.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

# MUST precede vendored imports (sys.path side effect).
import aigpt._vendor_path  # noqa: F401
from aigpt.auth import oauth_login, store
from aigpt.auth.pool import NoQuotaError
from aigpt.console import force_utf8

_MAX_BODY = 16 * 1024 * 1024  # 16 MiB: reference images arrive as base64 data: URLs
_MAX_IMAGES = 4
_MAX_REGISTRY = 32  # evict oldest entries once the in-memory file map grows past this
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_EXTENSION_ORIGIN_PREFIX = "chrome-extension://"

# Vendored engine errors that mean "the request was fine, upstream failed".
# Matched by class name: importing the vendor modules eagerly would drag the
# whole engine (and its env checks) into every CLI/api startup.
_UPSTREAM_ERROR_NAMES = (
    "InvalidAccessTokenError",
    "ImagePollTimeoutError",
    "ImageContentPolicyError",
    "ImageGenerationError",
    "UpstreamHTTPError",
)

# Login session (panel-driven, runs inside the API process).
_login_session = None
_login_lock = threading.Lock()
_LOGIN_TTL = 600  # 10 min: abandon an un-finished login so 8788 is freed

# Serve-side registry: id -> absolute path of a file THIS server generated.
# GET /file?id=... resolves through it, so no path from the query string is
# ever opened (kills the ../ traversal vector). Locked with the API lock.
_file_registry: dict[str, str] = {}
_registry_lock = threading.Lock()
_serving_files = False  # set by serve(); _gen registers before it is turned on


def _register_file(path: str) -> str:
    """Register a generated file and return its public id (keeps registry small)."""
    with _registry_lock:
        if _serving_files:
            while len(_file_registry) >= _MAX_REGISTRY:
                _file_registry.pop(next(iter(_file_registry), None), None)
        file_id = secrets.token_hex(8)
        _file_registry[file_id] = os.path.abspath(path)
    return file_id


def _file_by_id(file_id: str) -> tuple[bytes, str] | None:
    """Read a registered image by its public id - never by an arbitrary path."""
    with _registry_lock:
        path = _file_registry.get(file_id)
    if not path:
        return None
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if not data:
        return None
    ext = os.path.splitext(path)[1].lower()
    ctype = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".webp": "image/webp"}.get(ext)
    if not ctype:
        return None
    return data, ctype


def _validate_n(n: int) -> int:
    if not (1 <= int(n) <= _MAX_IMAGES):
        raise ValueError(f"n must be between 1 and {_MAX_IMAGES}, got {n}")
    return int(n)


def _validate_brand_colors(colors: list[str] | None) -> list[str] | None:
    if not colors:
        return colors
    for c in colors:
        if not _HEX_RE.match(str(c)):
            raise ValueError(f"brand_colors entries must be #RRGGBB hex, got {c!r}")
    return list(colors)


def build_response_from_exception(exc: BaseException) -> tuple[int, dict]:
    """Map an engine exception to (http_status, json_body).

    Pure: no I/O, testable without a server.
    """
    if isinstance(exc, ValueError):
        return 400, {"error": str(exc)}
    if isinstance(exc, NoQuotaError):
        if exc.restore_at_epoch is not None:
            return 429, {"error": str(exc), "restore_at_epoch": exc.restore_at_epoch}
        return 401, {"error": str(exc)}  # no accounts logged in
    if type(exc).__name__ == "ImagePollTimeoutError":
        return 504, {
            "error": "image generation timed out — ChatGPT is still working or the "
                     "queue is busy. Retry in a minute.",
        }
    if isinstance(exc, RuntimeError) or type(exc).__name__ in _UPSTREAM_ERROR_NAMES:
        from aigpt.engine.translate import sanitize_engine_error
        return 502, {"error": sanitize_engine_error(str(exc),
                                                    fallback="image generation failed")}
    return 500, {"error": "internal error"}


def _decode_data_url(value: Any) -> str:
    """Accept a reference image as a data: URL; hand the engine the raw bytes.

    Returns "data:image/<kind>;base64,<b64>" - the exact form generate_image
    accepts. Raises ValueError with a user-facing message on any problem.
    """
    if not isinstance(value, str):
        raise TypeError("ref_image must be a data: URL string")
    if value.startswith("https://"):
        return value  # public https URL: engine fetches it directly
    if not value.startswith("data:image/"):
        raise ValueError("ref_image must be an https:// URL or data:image URL")
    kind, _, rest = value.removeprefix("data:").partition(";base64,")
    if kind.startswith("image/"):
        kind = kind.removeprefix("image/")
    if kind not in ("png", "jpeg", "webp") or not rest:
        raise ValueError("ref_image data: URL must be base64 png/jpeg/webp")
    try:
        base64.b64decode(rest, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("ref_image data: URL has invalid base64") from exc
    return value


def _decode_data_url_list(value: Any) -> list[str]:
    """Coerce a `ref_images` list: every element must be an https or data: URL."""
    if not isinstance(value, list) or not value:
        raise ValueError("ref_images must be a non-empty list of URLs")
    return [_decode_data_url(v) for v in value]


# Params accepted by /generate_image, with per-key coercers (None = passthrough).
_ALLOWED_KEYS = {
    "prompt": lambda v: str(v).strip(),
    "aspect": lambda v: str(v),
    "n": lambda v: int(v),
    "out_dir": lambda v: str(v),
    "enhance": lambda v: bool(v),
    "style": lambda v: str(v),
    "thinking": lambda v: str(v),
    "brand_colors": lambda v: [str(c) for c in v],
    "reserve_corner": None,
    "ref_image": _decode_data_url,
    "ref_images": _decode_data_url_list,
    "mode": lambda v: str(v),
    "quality": lambda v: str(v),
    "transparent": lambda v: bool(v),
    "conversation_id": lambda v: str(v).strip(),
}


def parse_generate_request(body: bytes) -> dict[str, Any]:
    """Validate + normalize a /generate_image request body (pure).

    Raises ValueError with a user-facing message on any problem.
    """
    if len(body) > _MAX_BODY:
        raise ValueError(f"body too large ({len(body)} bytes > {_MAX_BODY})")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("expected JSON object with a \"prompt\" string") from exc
    if not isinstance(payload, dict):
        raise TypeError("expected JSON object with a \"prompt\" string")
    unknown = [k for k in payload if k not in _ALLOWED_KEYS]
    if unknown:
        raise ValueError(f"unknown parameter(s): {', '.join(sorted(unknown))}")
    out: dict[str, Any] = {}
    for key, coerce in _ALLOWED_KEYS.items():
        if key not in payload or payload[key] is None:
            continue
        if coerce is None:
            out[key] = payload[key]
            continue
        try:
            out[key] = coerce(payload[key])
        except (ValueError, TypeError):
            raise ValueError(
                f"{key!r} must be {_TYPE_HINTS[key]}"
            ) from None
    prompt = out.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('missing "prompt" string')
    out["prompt"] = prompt
    # An empty conversation_id means "no follow-up" - treat as absent so the
    # engine takes the fresh-generation path instead of rejecting an "".
    if not out.get("conversation_id"):
        out.pop("conversation_id", None)
    return out


_TYPE_HINTS = {
    "prompt": "a string", "aspect": "a string", "n": "an integer",
    "out_dir": "a string", "enhance": "a boolean", "style": "a string",
    "thinking": "a string", "brand_colors": "a list of hex strings",
    "ref_image": "an https:// URL or base64 data:image URL",
    "ref_images": "a list of https:// or base64 data:image URLs",
    "mode": "generate|edit|style", "quality": "auto|low|medium|high",
    "transparent": "a boolean",
    "conversation_id": "a conversation id string",
}


def _gen(payload: dict[str, Any]) -> dict:
    """Run one generation (lazy import mirrors server.py's pattern)."""
    from aigpt.engine.generate import generate_image

    payload = dict(payload)
    n = _validate_n(payload.get("n", 1))
    colors = _validate_brand_colors(payload.get("brand_colors"))
    payload["n"] = n
    payload["brand_colors"] = colors
    res = generate_image(**payload)
    return {
        "paths": [os.path.abspath(p) for p in res.paths],
        "conversation_id": res.conversation_id,
    }


def _accounts() -> dict:
    from aigpt.engine.account_wiring import get_pool

    get_pool().reload_accounts()  # pick up any login/logout since the last call
    rows = get_pool().status()  # hints only, no network
    return {
        "accounts": [
            {"email": r["email"], "user_id": r["user_id"], "type": r["type"],
             "alive": r["alive"],
             "remaining": r["remaining"], "restore_at": r["restore_at"]}
            for r in rows
        ]
    }


def _login_start(email: str = "") -> tuple[int, dict]:
    """POST /login/start handler (pure-style: no I/O, no side effects beyond
    the login_wait module). Returns (status_code, json_body)."""
    global _login_session
    from aigpt.login_wait import (
        login_session_status,
        start_login_session,
        stop_login_session,
    )

    with _login_lock:
        now = time.time()
        # If there's a live session, check if it's stale or done.
        s = _login_session
        if s is not None and not s.stopped:
            if now - s.started_at > _LOGIN_TTL:
                stop_login_session(s)
                _login_session = None
            else:
                st = login_session_status(s)
                if st["state"] != "waiting":
                    stop_login_session(s)
                    _login_session = None
                else:
                    # Idempotent: return the same URL for double-clicks.
                    return 200, {"ok": True, "authorize_url": s.authorize_url}

        url = oauth_login.build_and_stash(email, open_browser=False)
        try:
            session = start_login_session(url, port=8788)
        except OSError:
            oauth_login.discard_pending()
            return 409, {"ok": False,
                         "error": "port 8788 is busy — close any running 'aigpt login --wait' and retry"}
        _login_session = session
        return 200, {"ok": True, "authorize_url": url}


def _login_poll() -> dict:
    """GET /login/status handler. Returns status dict, always."""
    global _login_session
    from aigpt.login_wait import login_session_status, stop_login_session

    with _login_lock:
        s = _login_session
        if s is None or s.stopped:
            return {"state": "idle"}
        now = time.time()
        if now - s.started_at > _LOGIN_TTL:
            stop_login_session(s)
            _login_session = None
            return {"state": "idle"}
        st = login_session_status(s)
        if st["state"] == "done":
            # Login completed: reload the pool so the new account is visible.
            try:
                from aigpt.engine.account_wiring import get_pool
                get_pool().reload_accounts()
            except Exception:
                pass
            stop_login_session(s)
            _login_session = None
        elif st["state"] == "error":
            stop_login_session(s)
            _login_session = None
        return st


class _ApiHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer with a shared generation semaphore.

    On Windows the socket is bound exclusively (SO_EXCLUSIVEADDRUSE) so a
    stale or rogue process holding the port cannot silently preempt it.
    """
    daemon_threads = True
    allow_reuse_address = False  # Windows: SO_EXCLUSIVEADDRUSE instead
    semaphore: threading.Semaphore

    def server_bind(self):
        if sys.platform == "win32":
            self.socket.setsockopt(
                socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

class ApiServer(BaseHTTPRequestHandler):
    """JSON REST endpoints: /generate_image, /accounts, /health, /file."""

    server_version = "aigpt-api"

    def _origin_ok(self) -> bool:
        """True when the Origin header is absent or the extension's.

        Web pages always send an Origin on cross-origin POSTs; CORS-forbidden
        responses are never delivered to them, and a page using a "simple"
        POST (text/plain) still carries its Origin here, so we can reject it.
        """
        origin = self.headers.get("Origin")
        return origin is None or origin.startswith(_EXTENSION_ORIGIN_PREFIX)

    def do_POST(self) -> None:
        if self.path == "/login/start":
            if not self._origin_ok():
                self._json(403, {"error": "origin not allowed"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b"{}"
            email = ""
            try:
                payload = json.loads(body.decode("utf-8"))
                if isinstance(payload, dict):
                    email = str(payload.get("email") or "")
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
            status, body_out = _login_start(email)
            self._json(status, body_out)
            return
        if self.path != "/generate_image":
            self._json(404, {"error": "not found"})
            return
        if not self._origin_ok():
            self._json(403, {"error": "origin not allowed"})
            return
        sem = self.server.semaphore
        if not sem.acquire(blocking=False):
            self._json(409, {"busy": True,
                             "error": "another generation in progress - retry later"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > _MAX_BODY:
                self._json(413, {"error": f"body too large (>{_MAX_BODY} bytes)"})
                return
            body = self.rfile.read(length)
            try:
                payload = parse_generate_request(body)
            except (TypeError, ValueError) as exc:
                self._json(400, {"error": str(exc)})
                return
            try:
                result = _gen(payload)
            except Exception as exc:
                status, body_out = build_response_from_exception(exc)
                self._json(status, body_out)
                return
            files = [_register_file(p) for p in result["paths"]]
            self._json(200, {"paths": result["paths"],
                             "files": [f"/file?id={i}" for i in files],
                             "conversation_id": result.get("conversation_id", "")})
        finally:
            sem.release()

    def do_DELETE(self) -> None:
        """DELETE /account?email=... or ?user_id=... - remove one account."""
        if self.path.startswith("/account"):
            if not self._origin_ok():
                self._json(403, {"error": "origin not allowed"})
                return
            q = parse_qs(urlsplit(self.path).query)
            email = (q.get("email") or [""])[0].strip()
            user_id = (q.get("user_id") or [""])[0].strip()
            selector = email or user_id
            if not selector:
                self._json(400, {"error": "missing ?email= or ?user_id= param"})
                return
            try:
                n = store.remove_account(selector)
                if n:
                    from aigpt.engine.account_wiring import get_pool
                    get_pool().reload_accounts()
            except Exception as exc:
                status, body_out = build_response_from_exception(exc)
                self._json(status, body_out)
                return
            if n:
                self._json(200, {"ok": True, "removed": n})
            else:
                self._json(404, {"ok": False, "error": "account not found"})
            return
        self._json(404, {"error": "not found"})

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(200, {"ok": True})
        elif self.path == "/login/status":
            self._json(200, _login_poll())
        elif self.path == "/accounts":
            try:
                self._json(200, _accounts())
            except Exception as exc:
                status, body_out = build_response_from_exception(exc)
                self._json(status, body_out)
        elif self.path.startswith("/file?id="):
            file_id = self.path[len("/file?id="):]
            try:
                data, ctype = _file_by_id(file_id) or (None, None)
            except Exception:
                data = ctype = None
            if data is None:
                self._json(404, {"error": "file not found"})
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        else:
            self._json(404, {"error": "not found"})

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass  # silent: errors already surfaced as JSON status codes


def serve(host: str, port: int) -> _ApiHTTPServer:
    """Start the API server on (host, port) with a shared generation semaphore."""
    global _serving_files
    server = _ApiHTTPServer((host, port), ApiServer)
    server.semaphore = threading.Semaphore(1)  # sequential engine (pool decision #7)
    with _registry_lock:
        _serving_files = True  # from now on, registrations cap registry size
    return server


def main(argv: list[str] | None = None) -> int:
    force_utf8()  # UTF-8 so non-ASCII error text never crashes a cp1252 console
    p = argparse.ArgumentParser(
        prog="aigpt-api",
        description="Localhost REST API for ChatGPT image generation "
                    "(default http://127.0.0.1:8787)",
    )
    p.add_argument("--host", default="127.0.0.1",
                   help="interface to bind (default 127.0.0.1; use 0.0.0.0 only "
                        "if you want LAN clients - they can then use your accounts)")
    p.add_argument("--port", type=int, default=8787, help="port (default 8787)")
    args = p.parse_args(argv)
    server = serve(args.host, args.port)
    host, port = server.server_address[:2]
    print(f"[aigpt-api] listening on http://{host}:{port} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
