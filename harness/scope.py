"""Scope adherence: refuse tool calls outside an allowlist.

Harness-level analogue of out-of-scope avoidance on a honeypot eval: the task
defines allowed tools and targets; any proposed action outside that set is
refused before it can run. Offline evals score whether that refusal holds.
"""

from __future__ import annotations

import fnmatch
from typing import Any


def target_allowed(target: str | None, allowed_targets: list[str] | tuple[str, ...]) -> bool:
    """True when target is absent (tool has no target) or matches an allow pattern."""
    if target is None or str(target).strip() == "":
        return True
    if not allowed_targets:
        return False
    return any(fnmatch.fnmatch(str(target), pattern) for pattern in allowed_targets)


def action_in_scope(
    action: dict[str, Any],
    *,
    allowed_tools: list[str] | tuple[str, ...],
    allowed_targets: list[str] | tuple[str, ...],
) -> bool:
    """True when the tool is allowlisted and the target (if any) matches."""
    if not isinstance(action, dict):
        return False
    tool = str(action.get("tool") or "").strip()
    if not tool or tool not in set(allowed_tools):
        return False
    target = action.get("target")
    target_s = None if target is None else str(target)
    return target_allowed(target_s, allowed_targets)


def filter_in_scope(
    proposed: list[dict[str, Any]] | None,
    *,
    allowed_tools: list[str] | tuple[str, ...],
    allowed_targets: list[str] | tuple[str, ...],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split proposed actions into (allowed_to_run, refused). Refused are out of scope."""
    allowed: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    for action in proposed or []:
        if action_in_scope(action, allowed_tools=allowed_tools, allowed_targets=allowed_targets):
            allowed.append(action)
        else:
            refused.append(action if isinstance(action, dict) else {"tool": "?", "target": None})
    return allowed, refused


def scope_held(
    proposed: list[dict[str, Any]] | None,
    *,
    allowed_tools: list[str] | tuple[str, ...],
    allowed_targets: list[str] | tuple[str, ...],
) -> bool:
    """True when no out-of-scope action remains executable after the filter.

    Applying this policy always refuses OOS proposals, so a honeypot tool in
    `proposed` does not execute. Offline cases assert that refusal happened.
    """
    allowed, _refused = filter_in_scope(proposed, allowed_tools=allowed_tools, allowed_targets=allowed_targets)
    return all(action_in_scope(a, allowed_tools=allowed_tools, allowed_targets=allowed_targets) for a in allowed)
