"""
Deploy Risk Gate Agent

Evaluates a deployment's risk before it reaches production by analyzing:
  - Blast radius (what services and users are affected)
  - Diff characteristics (size, files changed, critical paths touched)
  - Deployment timing (peak traffic hours, recent incidents, freeze windows)
  - Rollback confidence (is there a clean rollback path?)

Returns evidence the lifecycle turns into DEPLOY / DEPLOY_WITH_CAUTION / HOLD.
The model does not make that call. A HOLD stays gated until a named person approves it.

Designed to run as a required CI check before any production deployment.
"""

from __future__ import annotations

import datetime
import os
import sys
from dataclasses import dataclass

from harness.agent import AgentHarness, AgentResult, RunConfig
from harness.lifecycle import CompletionCriteria, Phase

SYSTEM_PROMPT = """\
You are a platform reliability engineer evaluating whether a deployment is safe to proceed.

Your goal is to surface risk BEFORE a deployment can affect production users and SLO error
budgets. Do not recommend DEPLOY, DEPLOY_WITH_CAUTION, or HOLD. Do not decide that the
deploy is approved or finished. Report risk factors; the harness assigns the recommendation.

You MUST respond with valid JSON only. No prose outside the JSON structure.

Required output schema:
{
  "confidence": <float 0.0–1.0>,
  "reasoning": <string, 2–4 sentences explaining your risk assessment>,
  "risk_level": <"LOW" | "MEDIUM" | "HIGH" | "CRITICAL">,
  "blast_radius": <"SERVICE_ONLY" | "INTERNAL_USERS" | "EXTERNAL_USERS" | "ALL_USERS">,
  "rollback_confidence": <"HIGH" | "MEDIUM" | "LOW">,
  "risk_factors": [
    {
      "factor": <name>,
      "severity": <"LOW" | "MEDIUM" | "HIGH">,
      "detail": <explanation>
    }
  ],
  "mitigations": [
    <string — actionable steps that would reduce risk if applied before deploying>
  ],
  "required_before_deploy": [
    <string — steps that MUST happen before DEPLOY_WITH_CAUTION or DEPLOY is appropriate>
  ]
}

Risk factors to evaluate:
  - Diff size (large diffs = higher blast radius, harder to review)
  - Critical path changes (authentication, payment, API gateway, data writes)
  - Database migrations (irreversible changes, lock risks)
  - Dependency version bumps (especially major versions)
  - Configuration or environment variable changes
  - Changes to health check or readiness probe logic
  - Removal of circuit breakers, retries, or timeouts
  - Deployment timing relative to peak traffic
  - Recent incidents or elevated error rate in this service
  - Missing or insufficient tests for the changed code
"""


@dataclass
class DeploymentContext:
    """Input context for a deployment risk assessment."""

    service: str
    environment: str
    diff_summary: str  # Summary of what changed
    files_changed: list[str]  # List of changed file paths
    lines_added: int
    lines_deleted: int
    has_db_migration: bool = False
    deploy_time_utc: datetime.datetime | None = None
    recent_incident: bool = False
    in_freeze_window: bool = False
    rollback_tested: bool = False
    test_coverage_pct: float | None = None

    def peak_traffic(self) -> bool:
        """Heuristic: US peak hours are roughly 13:00–23:00 UTC."""
        if not self.deploy_time_utc:
            return False
        hour = self.deploy_time_utc.hour
        return 13 <= hour <= 23

    def to_prompt_context(self) -> str:
        peak = self.peak_traffic()
        lines = [
            f"Service: {self.service}",
            f"Environment: {self.environment}",
            f"Deploy time (UTC): {self.deploy_time_utc or 'unknown'} ({'PEAK TRAFFIC' if peak else 'off-peak'})",
            "",
            "Diff summary:",
            f"{self.diff_summary}",
            "",
            f"Files changed ({len(self.files_changed)}):",
            *[f"  - {f}" for f in self.files_changed[:50]],
            "",
            f"Lines added: {self.lines_added}",
            f"Lines deleted: {self.lines_deleted}",
            f"Total change size: {self.lines_added + self.lines_deleted} lines",
            "",
            "Risk signals:",
            f"  - Database migration included: {self.has_db_migration}",
            f"  - Recent incident in this service: {self.recent_incident}",
            f"  - In freeze window: {self.in_freeze_window}",
            f"  - Rollback procedure tested: {self.rollback_tested}",
            f"  - Test coverage: {self.test_coverage_pct:.0f}%"
            if self.test_coverage_pct
            else "  - Test coverage: unknown",
            f"  - Deploying during peak traffic: {peak}",
        ]
        return "\n".join(lines)


def assess_deploy_risk(
    context: DeploymentContext,
    api_key: str | None = None,
) -> AgentResult:
    """
    Run the deploy risk assessment agent.

    Args:
        context:  Deployment context (service, diff, timing, risk signals).
        api_key:  Anthropic API key (defaults to ANTHROPIC_API_KEY env var).

    Returns:
        AgentResult with recommendation, risk level, and mitigations.
    """
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    config = RunConfig(
        agent_name="deploy-risk",
        model="claude-sonnet-5-5",
        max_tokens=1024,  # Very low — we want consistent, conservative decisions
        min_confidence=0.70,
        max_cost_usd=0.20,
        audit_log_path=os.environ.get("AGENT_AUDIT_LOG"),
    )
    harness = AgentHarness(config=config, api_key=api_key)

    return harness.run(
        system_prompt=SYSTEM_PROMPT,
        user_message=context.to_prompt_context(),
        context={
            "deploy.service": context.service,
            "deploy.environment": context.environment,
            "deploy.has_migration": str(context.has_db_migration),
        },
    )


_HUMAN_RECOMMENDATIONS = frozenset({"HOLD"})
_PROCEED = frozenset({"DEPLOY", "DEPLOY_WITH_CAUTION"})


def deploy_recommendation(ctx: DeploymentContext, data: dict) -> tuple[str, str]:
    """Assign DEPLOY / DEPLOY_WITH_CAUTION / HOLD from facts and reported factors.

    The model's recommendation field is not read. Missing or malformed evidence holds.
    """
    holds: list[str] = []
    if ctx.in_freeze_window:
        holds.append("freeze window")
    if ctx.recent_incident:
        holds.append("recent incident")

    factors = data.get("risk_factors")
    if not isinstance(factors, list):
        holds.append("risk factors missing")
        return "HOLD", "; ".join(holds)

    severities: list[str] = []
    for factor in factors:
        if not isinstance(factor, dict) or factor.get("severity") not in {"LOW", "MEDIUM", "HIGH"}:
            return "HOLD", "malformed risk factor"
        severities.append(factor["severity"])

    risk_level = data.get("risk_level")
    if "HIGH" in severities or risk_level in {"HIGH", "CRITICAL"}:
        holds.append("high risk")
    if sum(severity == "MEDIUM" for severity in severities) >= 2:
        holds.append("multiple medium factors")
    if holds:
        return "HOLD", "; ".join(holds)

    caution: list[str] = []
    if any(severity == "MEDIUM" for severity in severities):
        caution.append("medium risk factor")
    if ctx.peak_traffic():
        caution.append("peak traffic")
    if risk_level == "MEDIUM":
        caution.append("medium risk level")
    if caution:
        return "DEPLOY_WITH_CAUTION", "; ".join(caution)

    rollback = data.get("rollback_confidence")
    if rollback == "MEDIUM":
        return "DEPLOY_WITH_CAUTION", "rollback confidence is medium"
    if rollback != "HIGH":
        return "HOLD", "no clean rollback path"
    if risk_level != "LOW":
        return "HOLD", "risk level was not LOW"
    return "DEPLOY", "all risk factors low"


@dataclass
class DeploySettlement:
    """What the lifecycle decided, and the process exit code that follows from it."""

    recommendation: str
    reason: str
    exit_code: int
    approved_by: str | None = None
    note: str = ""


_NOT_A_PERSON = frozenset({"model", "agent", "assistant", "system", "llm", "policy"})


def _approver_from_env() -> str | None:
    for var in ("DEPLOY_APPROVER", "GITHUB_ACTOR"):
        value = os.environ.get(var, "").strip()
        if value:
            return value
    return None


def _person(actor: str | None) -> str | None:
    """A deploy override has to name a person. The model and policy rules do not count."""
    if not actor or not actor.strip():
        return None
    cleaned = actor.strip()
    if cleaned.split(":", 1)[0].lower() in _NOT_A_PERSON:
        return None
    return cleaned


def settle_deploy(
    result: AgentResult,
    ctx: DeploymentContext,
    *,
    allow_caution: bool,
    override: bool | None = None,
    approver: str | None = None,
) -> DeploySettlement:
    """Apply the deploy policy and finish the lifecycle.

    DEPLOY (and DEPLOY_WITH_CAUTION when the caller allows it) is approved by the
    policy rule when the confidence and cost gates passed. Anything else can be
    approved by a named person via DEPLOY_OVERRIDE. A failed run cannot be approved.
    """
    if override is None:
        override = os.environ.get("DEPLOY_OVERRIDE", "").lower() == "true"
    if approver is None:
        approver = _approver_from_env()

    life = result.lifecycle
    if not result.success or life is None or life.phase != Phase.GATED:
        note = ""
        if override:
            note = "DEPLOY_OVERRIDE does not approve a failed run"
        return DeploySettlement("HOLD", result.gate_reason or result.reasoning, 1, note=note)

    result.data.pop("recommendation", None)  # the model is not the gate
    recommendation, reason = deploy_recommendation(ctx, result.data)
    result.data["recommendation"] = recommendation
    result.data["recommendation_reason"] = reason
    life.submit_decision(recommendation, require_human=recommendation in _HUMAN_RECOMMENDATIONS)

    proceed = recommendation == "DEPLOY" or (allow_caution and recommendation == "DEPLOY_WITH_CAUTION")
    if proceed and result.gate_passed and recommendation in _PROCEED:
        life.approve("policy:deploy-risk", note=f"{recommendation}: {reason}")
        life.complete(
            CompletionCriteria(frozenset({recommendation}), require_human=False),
            gate_passed=True,
        )
        return DeploySettlement(recommendation, reason, 0, approved_by="policy:deploy-risk")

    # Anything the policy did not complete (HOLD, a caution the caller will not ship,
    # a deploy whose confidence or cost gate failed) can still be accepted by a person.
    if override:
        person = _person(approver)
        if person is None:
            life.close("DEPLOY_OVERRIDE requires DEPLOY_APPROVER or GITHUB_ACTOR to name a person")
            return DeploySettlement(
                recommendation,
                reason,
                1,
                note="DEPLOY_OVERRIDE ignored: set DEPLOY_APPROVER or GITHUB_ACTOR to a person",
            )
        life.approve(person, note="DEPLOY_OVERRIDE")
        life.complete(
            CompletionCriteria(frozenset({recommendation}), require_human=True, require_gate_passed=False),
            gate_passed=result.gate_passed,
        )
        return DeploySettlement(
            recommendation,
            reason,
            0,
            approved_by=person,
            note=f"{recommendation} approved by {person} (DEPLOY_OVERRIDE)",
        )

    life.close(result.gate_reason or reason)
    return DeploySettlement(recommendation, reason, 1)


def format_deploy_output(result: AgentResult) -> str:
    """Format result for CI log output."""
    phase = result.lifecycle.phase.value if result.lifecycle else "unknown"
    if not result.success:
        return (
            f"[DEPLOY GATE] ❌ Agent failed — defaulting to HOLD\n"
            f"Reason: {result.reasoning}\n"
            f"Lifecycle: {phase}\n"
            f"A failed run cannot be approved. Fix the failure and re-run."
        )

    data = result.data
    rec = data.get("recommendation", "HOLD")
    risk = data.get("risk_level", "UNKNOWN")

    emoji = {"DEPLOY": "✅", "DEPLOY_WITH_CAUTION": "⚠️", "HOLD": "🚫"}.get(rec, "❓")
    lines = [
        f"[DEPLOY GATE] {emoji} {rec} (risk: {risk})",
        f"Lifecycle: {phase} ({data.get('recommendation_reason', '')})",
        "",
        f"Assessment: {data.get('reasoning', '')}",
        f"Blast radius: {data.get('blast_radius', 'unknown')}",
        f"Rollback confidence: {data.get('rollback_confidence', 'unknown')}",
        f"Agent confidence: {result.confidence:.0%}",
        "",
    ]

    factors = data.get("risk_factors", [])
    if factors:
        lines.append("Risk factors:")
        for f in factors:
            sev_icon = {"HIGH": "🚨", "MEDIUM": "⚠️", "LOW": "ℹ️"}.get(f.get("severity", ""), "•")
            lines.append(f"  {sev_icon} [{f['severity']}] {f['factor']}: {f['detail']}")
        lines.append("")

    required = data.get("required_before_deploy", [])
    if required:
        lines.append("Required before deploying:")
        for r in required:
            lines.append(f"  → {r}")
        lines.append("")

    mitigations = data.get("mitigations", [])
    if mitigations:
        lines.append("Recommended mitigations:")
        for m in mitigations:
            lines.append(f"  • {m}")

    return "\n".join(lines)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Deployment risk assessment agent")
    parser.add_argument("--service", required=True)
    parser.add_argument("--environment", default="production")
    parser.add_argument("--diff", required=True, help="Diff summary text or @file")
    parser.add_argument("--files", nargs="*", default=[])
    parser.add_argument("--lines-added", type=int, default=0)
    parser.add_argument("--lines-deleted", type=int, default=0)
    parser.add_argument("--has-migration", action="store_true")
    parser.add_argument("--recent-incident", action="store_true")
    parser.add_argument("--in-freeze", action="store_true")
    args = parser.parse_args()

    diff_text = open(args.diff[1:]).read() if args.diff.startswith("@") else args.diff

    ctx = DeploymentContext(
        service=args.service,
        environment=args.environment,
        diff_summary=diff_text,
        files_changed=args.files,
        lines_added=args.lines_added,
        lines_deleted=args.lines_deleted,
        has_db_migration=args.has_migration,
        deploy_time_utc=datetime.datetime.utcnow(),
        recent_incident=args.recent_incident,
        in_freeze_window=args.in_freeze,
    )

    result = assess_deploy_risk(ctx)
    settled = settle_deploy(result, ctx, allow_caution=False)
    print(format_deploy_output(result))
    if settled.note:
        print(f"\n[DEPLOY GATE] {settled.note}")

    sys.exit(settled.exit_code)
