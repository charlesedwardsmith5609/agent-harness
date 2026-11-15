"""Typed evidence checks at the gated boundary.

The model may return free-form JSON; policy must not trust a bad shape.
Malformed evidence fails closed (caller maps the error to HOLD / BLOCK).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class EvidenceError(Exception):
    """Evidence failed schema validation. No approval may follow from it."""


_SEVERITIES = frozenset({"HIGH", "MEDIUM", "LOW", "INFO"})
_RISK_SEVERITIES = frozenset({"LOW", "MEDIUM", "HIGH"})
_RISK_LEVELS = frozenset({"LOW", "MEDIUM", "HIGH", "CRITICAL"})
_ROLLBACK = frozenset({"HIGH", "MEDIUM", "LOW"})


def validate_review_evidence(data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise EvidenceError("review evidence must be an object")
    findings = data.get("findings")
    if findings is None:
        raise EvidenceError("findings missing")
    if not isinstance(findings, list):
        raise EvidenceError("findings must be a list")
    for i, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise EvidenceError(f"finding[{i}] must be an object")
        severity = finding.get("severity")
        if severity not in _SEVERITIES:
            raise EvidenceError(f"finding[{i}] has invalid severity {severity!r}")
        if not str(finding.get("title") or "").strip():
            raise EvidenceError(f"finding[{i}] title missing")


def validate_deploy_evidence(data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise EvidenceError("deploy evidence must be an object")
    risk_level = data.get("risk_level")
    if risk_level not in _RISK_LEVELS:
        raise EvidenceError(f"risk_level invalid: {risk_level!r}")
    rollback = data.get("rollback_confidence")
    if rollback not in _ROLLBACK:
        raise EvidenceError(f"rollback_confidence invalid: {rollback!r}")
    factors = data.get("risk_factors")
    if not isinstance(factors, list):
        raise EvidenceError("risk_factors must be a list")
    for i, factor in enumerate(factors):
        if not isinstance(factor, dict):
            raise EvidenceError(f"risk_factor[{i}] must be an object")
        if factor.get("severity") not in _RISK_SEVERITIES:
            raise EvidenceError(f"risk_factor[{i}] severity invalid")
        if not str(factor.get("factor") or "").strip():
            raise EvidenceError(f"risk_factor[{i}] name missing")


def validate_runbook_evidence(data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise EvidenceError("runbook evidence must be an object")
    runbook = data.get("runbook")
    if not isinstance(runbook, dict):
        raise EvidenceError("runbook object missing")
    if not str(runbook.get("title") or "").strip():
        raise EvidenceError("runbook title missing")
    if not str(runbook.get("service") or "").strip():
        raise EvidenceError("runbook service missing")
    diagnosis = runbook.get("diagnosis_steps")
    remediation = runbook.get("remediation")
    if diagnosis is not None and not isinstance(diagnosis, list):
        raise EvidenceError("diagnosis_steps must be a list")
    if remediation is not None and not isinstance(remediation, list):
        raise EvidenceError("remediation must be a list")


@dataclass(frozen=True)
class AgentScope:
    """Who ran, which intent, and which tools were allowed for one audit row.

    `allowed_tools` is None when the caller did not set a scope. An empty list
    means the scope was set and no tools are allowed.
    """

    caller: str | None
    intent: str | None
    allowed_tools: list[str] | None

    def to_dict(self) -> dict[str, Any]:
        tools = None if self.allowed_tools is None else list(self.allowed_tools)
        return {"caller": self.caller, "intent": self.intent, "allowed_tools": tools}


def read_agent_scope(record: dict[str, Any]) -> AgentScope:
    """Read `agent_scope` from an audit row.

    Rows written before the field existed fall back to top-level `caller_identity`
    with unknown intent and unknown tool scope.
    """
    if not isinstance(record, dict):
        raise EvidenceError("audit record must be an object")
    fallback = _optional_text(record.get("caller_identity"))
    raw = record.get("agent_scope")
    if not isinstance(raw, dict):
        return AgentScope(caller=fallback, intent=None, allowed_tools=None)
    caller = _optional_text(raw.get("caller")) if "caller" in raw else None
    if caller is None:
        caller = fallback
    intent = _optional_text(raw.get("intent")) if "intent" in raw else None
    if "allowed_tools" not in raw or raw.get("allowed_tools") is None:
        tools: list[str] | None = None
    elif isinstance(raw.get("allowed_tools"), list):
        tools = [item.strip() for item in raw["allowed_tools"] if isinstance(item, str) and item.strip()]
    else:
        tools = None
    return AgentScope(caller=caller, intent=intent, allowed_tools=tools)


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None
