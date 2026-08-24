"""Make the vendored chatgpt2api importable, and satisfy its config's auth-key.

Two side effects on import (importing this module once is enough):
1. Prepend the vendored package root to sys.path so the vendored code keeps its
   original absolute imports (`import services.*`, `import utils.*`).
2. Set `CHATGPT2API_AUTH_KEY` if unset. The vendored `services.config` builds a
   ConfigStore AT IMPORT and raises without this key. Because EVERY path that
   touches vendored code must import this module first (to resolve `services.*`),
   doing it here is the single guarantee the config imports on the login /
   accounts / generate paths alike. It is unrelated to our OAuth token, and
   `setdefault` never overrides a real value the user may have exported.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("CHATGPT2API_AUTH_KEY", "aigpt-local")

_VENDOR_ROOT = str(Path(__file__).resolve().parent / "_vendor")
if _VENDOR_ROOT not in sys.path:
    sys.path.insert(0, _VENDOR_ROOT)
