"""Verify `aigpt-mcp` spawns over stdio from an arbitrary working directory.

This is the cross-project path: another repo configures MCP with
`uv run --project <this-repo> aigpt-mcp` and spawns it from its own cwd.
The server must initialize, list its tools, and answer `login_status`
without depending on the repo directory being the cwd.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pump(stream):
    for line in stream:
        # drain stderr so the pipe never fills and blocks the server
        pass


def _send(proc, obj):
    proc.stdin.write(json.dumps(obj, separators=(",", ":")).encode() + b"\n")
    proc.stdin.flush()


def _recv(proc, timeout=15.0):
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


def test_mcp_spawns_and_initializes_from_foreign_cwd(tmp_path):
    # Foreign cwd: a scratch dir that is NOT the repo root.
    foreign = tmp_path / "other-project"
    foreign.mkdir()
    proc = subprocess.Popen(
        [sys.executable, "-m", "aigpt.server"],
        cwd=str(foreign),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": REPO_ROOT},
    )
    threading.Thread(target=_pump, args=(proc.stderr,), daemon=True).start()
    try:
        _send(proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "cross-project-test", "version": "0.0.1"},
            },
        })
        res = _recv(proc)["result"]
        assert res["serverInfo"]["name"] == "ai-image-gpt"

        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = {t["name"] for t in _recv(proc)["result"]["tools"]}
        assert {"generate_image", "login_status"} <= tools

        _send(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                     "params": {"name": "login_status", "arguments": {}}})
        r = _recv(proc, timeout=15)
        assert "error" not in r, f"login_status failed: {r.get('error')}"
        text = json.loads(r["result"]["content"][0]["text"])
        assert "authed" in text  # hint-based status; pool may be empty on CI
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
