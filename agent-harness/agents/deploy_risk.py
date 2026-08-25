"""
Deploy Risk Gate Agent

Evaluates a deployment's risk before it reaches production by analyzing:
  - Blast radius (what services and users are affected)
  - Diff characteristics (size, files changed, critical paths touched)
  - Deployment timing (peak traffic hours, recent incidents, freeze windows)
  - Rollback confidence (is there a clean rollback path?)

Returns a gated recommendation: DEPLOY / DEPLOY_WITH_CAUTION / HOLD

Designed to run as a required CI check before any production deployment.
A HOLD recommendation blocks the deploy pipeline and requires human override.
"""

from __future__ import annotations
import datetime
import json
import os
import sys
from dataclasses import dataclass
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from harness.agent import AgentHarness, RunConfig, AgentResult


SYSTEM_PROMPT = """\
You are a platform reliability engineer evaluating whether a deployment is safe to proceed.

Your goal is to catch deployments that carry elevated risk BEFORE they affect production users
and SLO error budgets. When in doubt, recommend HOLD — a delayed deploy costs less than an incident.

You MUST respond with valid JSON only. No prose outside the JSON structure.

Required output schema:
{
  "confidence": <float 0.0–1.0>,
  "reasoning": <string, 2–4 sentences explaining your risk assessment>,
  "recommendation": <"DEPLOY" | "DEPLOY_WITH_CAUTION" | "HOLD">,
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

Recommendation rules:
  DEPLOY:                All risk factors are LOW. Clean rollback path exists. Off-peak timing.
  DEPLOY_WITH_CAUTION:   1+ MEDIUM risk factors, or deploying during elevated traffic.
                         Recommend: staged rollout, enhanced monitoring, engineer on standby.
  HOLD:                  Any HIGH/CRITICAL risk factor, or multiple MEDIUM factors together,
                         or recent incident in the same service, or active freeze window.

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
    service:        str
    environment:    str
    diff_summary:   str              # Summary of what changed
    files_changed:  List[str]        # List of changed file paths
    lines_added:    int
    lines_deleted:  int
    has_db_migration:  bool = False
    deploy_time_utc:   Optional[datetime.datetime] = None
    recent_incident:   bool = False
    in_freeze_window:  bool = False
    rollback_tested:   bool = False
    test_coverage_pct: Optional[float] = None

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
            f"Deploy time (UTC): {self.deploy_time_utc or 'unknown'} "
            f"({'PEAK TRAFFIC' if peak else 'off-peak'})",
            f"",
            f"Diff summary:",
            f"{self.diff_summary}",
            f"",
            f"Files changed ({len(self.files_changed)}):",
            *[f"  - {f}" for f in self.files_changed[:50]],
            f"",
            f"Lines added: {self.lines_added}",
            f"Lines deleted: {self.lines_deleted}",
            f"Total change size: {self.lines_added + self.lines_deleted} lines",
            f"",
            f"Risk signals:",
            f"  - Database migration included: {self.has_db_migration}",
            f"  - Recent incident in this service: {self.recent_incident}",
            f"  - In freeze window: {self.in_freeze_window}",
            f"  - Rollback procedure tested: {self.rollback_tested}",
            f"  - Test coverage: {self.test_coverage_pct:.0f}%" if self.test_coverage_pct else "  - Test coverage: unknown",
            f"  - Deploying during peak traffic: {peak}",
        ]
        return "\n".join(lines)


def assess_deploy_risk(
    context:  DeploymentContext,
    api_key:  Optional[str] = None,
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
        model="claude-sonnet-4-6",
        max_tokens=1024,
        temperature=0.1,   # Very low — we want consistent, conservative decisions
        min_confidence=0.70,
        max_cost_usd=0.20,
        audit_log_path=os.environ.get("AGENT_AUDIT_LOG"),
    )
    harness = AgentHarness(config=config, api_key=api_key)

    return harness.run(
        system_prompt=SYSTEM_PROMPT,
        user_message=context.to_prompt_context(),
        context={
            "deploy.service":     context.service,
            "deploy.environment": context.environment,
            "deploy.has_migration": str(context.has_db_migration),
        },
    )


def format_deploy_output(result: AgentResult) -> str:
    """Format result for CI log output."""
    if not result.success:
        return (
            f"[DEPLOY GATE] ❌ Agent failed — defaulting to HOLD\n"
            f"Reason: {result.reasoning}\n"
            f"Override with: DEPLOY_OVERRIDE=true"
        )

    data = result.data
    rec  = data.get("recommendation", "HOLD")
    risk = data.get("risk_level", "UNKNOWN")

    emoji = {"DEPLOY": "✅", "DEPLOY_WITH_CAUTION": "⚠️", "HOLD": "🚫"}.get(rec, "❓")
    lines = [
        f"[DEPLOY GATE] {emoji} {rec} (risk: {risk})",
        f"",
        f"Assessment: {data.get('reasoning', '')}",
        f"Blast radius: {data.get('blast_radius', 'unknown')}",
        f"Rollback confidence: {data.get('rollback_confidence', 'unknown')}",
        f"Agent confidence: {result.confidence:.0%}",
        f"",
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
    parser.add_argument("--service",     required=True)
    parser.add_argument("--environment", default="production")
    parser.add_argument("--diff",        required=True, help="Diff summary text or @file")
    parser.add_argument("--files",       nargs="*", default=[])
    parser.add_argument("--lines-added",   type=int, default=0)
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
    print(format_deploy_output(result))

    rec = result.data.get("recommendation", "HOLD") if result.success else "HOLD"
    override = os.environ.get("DEPLOY_OVERRIDE", "").lower() == "true"

    if override:
        print("\n[DEPLOY GATE] ⚠️  DEPLOY_OVERRIDE=true — gate bypassed by human override")
        sys.exit(0)

    sys.exit(0 if rec == "DEPLOY" else 1)
