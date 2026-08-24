"""CLI gen --conversation-id: parsed + forwarded to the engine (Phase 3)."""
from __future__ import annotations

from unittest.mock import patch

from aigpt.cli import main
from aigpt.engine.result import GenerateResult


def _run_gen(tmp_path, argv_extra):
    captured = {}

    def _fake(prompt, **kw):
        captured.update(kw)
        captured["prompt"] = prompt
        return GenerateResult.from_list([str(tmp_path / "img.png")], "conv-9")

    with patch("aigpt.engine.generate.generate_image", _fake):
        rc = main(["gen", "make it blue", "--out", str(tmp_path), *argv_extra])
    return rc, captured


def test_conversation_id_parsed_and_forwarded(tmp_path, capsys):
    rc, captured = _run_gen(tmp_path, ["--conversation-id", "conv-9"])
    assert rc == 0
    assert captured["conversation_id"] == "conv-9"
    # The resulting conversation id is still echoed on stderr.
    assert "conversation_id=conv-9" in capsys.readouterr().err


def test_conversation_id_defaults_to_absent(tmp_path):
    rc, captured = _run_gen(tmp_path, [])
    assert rc == 0
    assert captured["conversation_id"] is None
