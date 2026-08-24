"""Smoke test the aigpt-mcp stdio server: initialize -> list -> call login_status.

Runs the server as a subprocess with stdin=DEVNULL (the piped JSON-RPC frames
are written to its real stdin via pipe); our own stdin is not touched, so
`uv run python scripts/mcp_smoke.py` works without the shell swallowing input.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

PYTHONPATH = os.path.join(os.path.dirname(__file__), "..", "src")


def pump_stderr(proc: subprocess.Popen) -> None:
    for line in proc.stderr:
        print("[stderr]", line.decode(errors="replace").rstrip())


def send(proc: subprocess.Popen, obj: dict) -> None:
    # MCP 1.x stdio = newline-delimited JSON (mcp.server.stdio reads `async for
    # line in stdin`), NOT the length-prefixed framing of older protocols.
    proc.stdin.write(json.dumps(obj, separators=(",", ":")).encode() + b"\n")
    proc.stdin.flush()


def recv(proc: subprocess.Popen, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    buf = b""
    while b"\n" not in buf:
        if time.time() > deadline:
            raise TimeoutError("no newline-terminated JSON within timeout")
        chunk = proc.stdout.read(1)
        if not chunk:
            raise RuntimeError("EOF from server")
        buf += chunk
    return json.loads(buf)


def main() -> int:
    proc = subprocess.Popen(
        [sys.executable, "-m", "aigpt.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": PYTHONPATH},
    )
    threading.Thread(target=pump_stderr, args=(proc,), daemon=True).start()

    try:
        send(proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "smoke", "version": "0.0.1"},
            },
        })
        print("initialize:", recv(proc)["result"]["serverInfo"])

        send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        r = recv(proc)
        print("tools:", [t["name"] for t in r["result"]["tools"]])

        send(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "login_status", "arguments": {}}})
        r = recv(proc, timeout=15)
        if "error" in r:
            print("login_status ERROR:", json.dumps(r["error"])[:400])
        else:
            text = r["result"]["content"][0]["text"]
            print("login_status:", text[:300])
    except Exception as exc:  # noqa: BLE001 - smoke test reports any failure
        print("FAIL:", type(exc).__name__, exc)
        return 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
