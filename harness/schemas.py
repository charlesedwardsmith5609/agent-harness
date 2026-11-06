"""Typed evidence checks at the gated boundary.

The model may return free-form JSON; policy must not trust a bad shape.
Malformed evidence fails closed (caller maps the error to HOLD / BLOCK).
"""

from __future__ import annotations

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
