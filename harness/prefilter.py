"""Cheap decision before a model call.

A prefilter returns a typed result and a confidence. The harness may skip the
model when that confidence is at or above a configured threshold. It must not
skip when the injection scanner flags the input; that case always calls the model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class PreFilterError(Exception):
    """The prefilter failed. The model is not called."""


@dataclass(frozen=True)
class PreFilterDecision:
    """Typed prefilter result. `data` is the JSON object used if the model is skipped."""

    confidence: float
    reason: str
    data: dict[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.confidence, (int, float)) or isinstance(self.confidence, bool):
            raise PreFilterError("prefilter confidence must be a number")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise PreFilterError("prefilter confidence must be between 0 and 1")
        if not isinstance(self.data, dict):
            raise PreFilterError("prefilter data must be an object")
        object.__setattr__(self, "confidence", float(self.confidence))
