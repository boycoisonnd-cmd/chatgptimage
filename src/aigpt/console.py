"""Console encoding helper.

Windows consoles default to a legacy code page (e.g. cp1252) that cannot encode
non-ASCII output; printing a path or email with accents would raise
UnicodeEncodeError and crash the CLI/server. force_utf8() switches stdout/stderr
to UTF-8 so output never crashes regardless of the console code page. It is a
best-effort no-op where reconfigure is unavailable (e.g. a plain pipe).
"""
from __future__ import annotations

import sys


def force_utf8() -> None:
    """Reconfigure stdout + stderr to UTF-8 with line buffering (best-effort).

    line_buffering matters for long-running commands (`aigpt login --wait`,
    `aigpt-api`): when stdout is a redirect/pipe it defaults to block buffering,
    so prompts would stay invisible until the process exits.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", line_buffering=True)  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
