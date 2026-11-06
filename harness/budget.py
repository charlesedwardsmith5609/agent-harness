"""Process-level cost budgets from the audit log.

Per-call max_cost_usd stops one expensive request. A stolen key or a retry storm
burns money across many runs; roll up cost_usd from the audit JSONL by agent and day.
"""

from __future__ import annotations

import json
import time
from pathlib import Path


class BudgetExceeded(Exception):
    """The process budget would be exceeded by another call. Fail closed."""


def _day_key(timestamp: str | None = None) -> str:
    if timestamp and len(timestamp) >= 10:
        return timestamp[:10]
    return time.strftime("%Y-%m-%d", time.gmtime())


def sum_audit_cost(
    audit_log_path: str | Path,
    *,
    agent_name: str | None = None,
    day: str | None = None,
) -> float:
    """Sum cost_usd from a JSONL audit file. Missing file → 0.0."""
    path = Path(audit_log_path)
    if not path.exists():
        return 0.0
    target_day = day or _day_key()
    total = 0.0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        if agent_name and record.get("agent_name") != agent_name:
            continue
        stamp = str(record.get("timestamp") or "")
        if _day_key(stamp) != target_day:
            continue
        try:
            total += float(record.get("cost_usd") or 0.0)
        except (TypeError, ValueError):
            continue
    return total


def check_daily_budget(
    audit_log_path: str | Path | None,
    *,
    agent_name: str,
    max_cost_usd_per_day: float | None,
    upcoming_cost_usd: float = 0.0,
) -> None:
    """Raise BudgetExceeded when spent + upcoming would pass the daily cap."""
    if max_cost_usd_per_day is None or audit_log_path is None:
        return
    if max_cost_usd_per_day < 0:
        raise ValueError("max_cost_usd_per_day must be >= 0")
    spent = sum_audit_cost(audit_log_path, agent_name=agent_name)
    projected = spent + max(0.0, upcoming_cost_usd)
    if projected > max_cost_usd_per_day + 1e-12:
        raise BudgetExceeded(
            f"Daily budget for {agent_name!r}: spent ${spent:.4f} + upcoming "
            f"${upcoming_cost_usd:.4f} exceeds max ${max_cost_usd_per_day:.2f}"
        )
