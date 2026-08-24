"""Shared typed enums for tool parameters.

Annotating the MCP tool functions with these `Literal` aliases makes FastMCP
emit a JSON-Schema `enum` for each parameter, so any connecting AI agent sees the
exact set of valid values (not just a free-form string) - it can't guess an
invalid value like "high" and get an HTTP 422. The CLI derives its argparse
`choices` from the same aliases (via typing.get_args), keeping one source of truth.
"""
from __future__ import annotations

from typing import Literal

# Image reasoning effort, sent to ChatGPT's image backend as `thinking_effort`.
# "auto" sends no field (ChatGPT default). standard < extended < max.
Thinking = Literal["auto", "standard", "extended", "max"]

# Slide design treatment applied by the prompt enhancer.
Style = Literal["auto", "slide", "fintech"]
