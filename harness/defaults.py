"""Load checked-in per-agent reasoning_effort defaults."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_PATH = Path(__file__).with_name("effort_defaults.json")


@lru_cache(maxsize=1)
def effort_defaults() -> dict[str, str]:
    data = json.loads(_PATH.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, str)}


def effort_for(agent_name: str, fallback: str | None = "medium") -> str | None:
    return effort_defaults().get(agent_name, fallback)
