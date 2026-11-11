"""Resolve who invoked an agent run for the audit log."""

from __future__ import annotations

import os


def caller_from_env() -> str | None:
    """Prefer AGENT_CALLER, then GITHUB_ACTOR. Empty values are ignored."""
    for var in ("AGENT_CALLER", "GITHUB_ACTOR"):
        value = os.environ.get(var, "").strip()
        if value:
            return value
    return None
