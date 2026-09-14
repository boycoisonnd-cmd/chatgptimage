"""Native Messaging host for GPT Image Studio.

Reads one framed JSON message from stdin (4-byte LE length + UTF-8 JSON),
spawns the aigpt-api server if needed, writes a framed JSON response, then
exits.  Chrome handles the framed protocol; this script reads/writes it.

Message format (Chrome Native Messaging):
  - 4 bytes, little-endian unsigned int = payload length
  - payload = UTF-8 JSON (no trailing NUL)

Actions:
  {"action":"start"}  → probe 127.0.0.1:8789; spawn if down; {"ok":true,"spawned":bool}
  {"action":"ping"}   → {"ok":true}
  unknown action      → {"ok":false,"error":"unknown action"}

On error (timeout, spawn failure, bad input) → {"ok":false,"error":"..."} exit 1.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import threading
import urllib.request

_HOST = "127.0.0.1"
_PORT = 8789
_READ_TIMEOUT = 5.0  # seconds to wait for a complete message from Chrome

# Resolve repo root = parent of this script's directory (extension/native_host)
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_VENV_PYTHONW = os.path.join(_REPO_ROOT, ".venv", "Scripts", "pythonw.exe")


def _read_message() -> dict | None:
    """Read one framed message from stdin. Returns parsed JSON or None on error.

    A single thread reads the whole frame (header + payload) so we never have
    two readers racing on the same non-thread-safe BufferedReader. Chrome
    always sends one message per connectNative, so the blocking read is safe.
    """
    result: list[bytes | None] = [None]

    def reader() -> None:
        try:
            raw_len = sys.stdin.buffer.read(4)
            if raw_len is None or len(raw_len) != 4:
                return
            (length,) = struct.unpack("<I", raw_len)
            if length == 0:
                result[0] = b"{}"
                return
            if length > 1 * 1024 * 1024:
                # Chrome's extension-side limit is 1 MiB; enforce it too.
                return
            payload = sys.stdin.buffer.read(length)
            if payload is not None and len(payload) == length:
                result[0] = payload
        except (OSError, ValueError):
            pass

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    t.join(_READ_TIMEOUT)
    if result[0] is None:
        return None
    try:
        return json.loads(result[0].decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _write_message(obj: dict) -> None:
    """Write a framed JSON message to stdout."""
    payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(payload)))
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


def _port_open(host: str, port: int) -> bool:
    """Check that the expected aigpt-api, not merely another service, is up."""
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=1.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return response.status == 200 and payload.get("service") == "aigpt-api"
    except (OSError, socket.timeout, ValueError, json.JSONDecodeError):
        return False


def _spawn_server() -> bool:
    """Launch aigpt-api via pythonw.exe (hidden, detached, no console)."""
    if not os.path.isfile(_VENV_PYTHONW):
        return False
    proc = subprocess.Popen(
        [_VENV_PYTHONW, "-m", "aigpt.api", "--port", str(_PORT)],
        cwd=_REPO_ROOT,
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Return quickly; the caller polls /health to confirm readiness.
    return proc.poll() is None  # True if still running (healthy start)


def main() -> int:
    msg = _read_message()
    if msg is None:
        _write_message({"ok": False, "error": "read timeout"})
        return 1

    action = msg.get("action", "")
    if action == "ping":
        _write_message({"ok": True})
        return 0

    if action == "start":
        already_open = _port_open(_HOST, _PORT)
        if already_open:
            _write_message({"ok": True, "spawned": False})
            return 0
        spawned = _spawn_server()
        _write_message({"ok": spawned, "spawned": spawned})
        return 0 if spawned else 1

    _write_message({"ok": False, "error": "unknown action"})
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
