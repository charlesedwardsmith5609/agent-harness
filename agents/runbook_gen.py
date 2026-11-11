"""
Runbook Generator Agent

After an incident, turns a structured incident report into a runbook entry
that can be immediately committed to the team's runbook repository.

Designed to run automatically from a PagerDuty or Rootly webhook, or
triggered manually after a postmortem is written.

The generated runbook entry includes:
  - Symptom signatures (what monitoring showed)
  - Diagnosis steps (how to confirm the root cause)
  - Remediation playbook (step-by-step commands)
  - Prevention actions (what to do to avoid recurrence)
  - Links to relevant dashboards and alerts
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field

from harness.agent import AgentHarness, AgentResult, RunConfig
from harness.defaults import effort_for
from harness.identity import caller_from_env
from harness.lifecycle import CompletionCriteria, Phase
from harness.schemas import EvidenceError, validate_runbook_evidence

SYSTEM_PROMPT = """\
You are a senior site reliability engineer writing a runbook entry based on an incident report.

Your goal is to produce documentation that a junior on-call engineer can follow at 3am
to diagnose and resolve a repeat of this incident — without needing to understand the full system.

Be specific. Use exact command syntax. Include expected outputs so the engineer knows if
the command worked. Prefer commands over UI instructions wherever possible.

Do not decide that the runbook is ready to commit. The harness marks it complete only
when title, service, and at least one diagnosis or remediation step are present.

You MUST respond with valid JSON only. No prose outside the JSON structure.

Required output schema:
{
  "confidence": <float 0.0–1.0, how confident you are in the steps you wrote>,
  "reasoning": <string, brief note on what information was available and any gaps>,
  "runbook": {
    "title": <string>,
    "service": <string>,
    "severity": <"P1" | "P2" | "P3">,
    "last_seen": <ISO date string or "unknown">,
    "symptoms": [
      {
        "signal": <what monitoring/alerting surfaced this>,
        "description": <what the engineer will see>
      }
    ],
    "quick_triage": [
      <string — first 3–5 checks to narrow down the cause, with exact commands>
    ],
    "diagnosis_steps": [
      {
        "step": <integer>,
        "description": <what to do>,
        "command": <exact command or null>,
        "expected_output": <what a healthy system shows, or signs of the bug>
      }
    ],
    "remediation": [
      {
        "step": <integer>,
        "description": <what to do>,
        "command": <exact command or null>,
        "warning": <any destructive or irreversible caveats, or null>
      }
    ],
    "rollback": {
      "applicable": <boolean>,
      "command": <rollback command or null>,
      "notes": <string>
    },
    "prevention": [
      {
        "action": <what to do>,
        "owner": <team or role>,
        "priority": <"immediate" | "sprint" | "backlog">
      }
    ],
    "dashboards": [
      {"name": <string>, "url": <string or "TBD">}
    ],
    "related_alerts": [<alert name>],
    "escalation_path": <string>
  }
}
"""


@dataclass
class IncidentReport:
    """Structured incident report input for the runbook generator."""

    incident_id: str
    service: str
    title: str
    severity: str  # P1 / P2 / P3
    start_time: str
    end_time: str
    impact: str  # User-visible impact description
    root_cause: str  # Identified root cause
    timeline: list[str]  # Ordered list of timeline entries
    remediation_taken: str  # What was actually done to fix it
    contributing_factors: list[str] = field(default_factory=list)
    monitoring_signals: list[str] = field(default_factory=list)
    commands_run: list[str] = field(default_factory=list)
    prevention_todos: list[str] = field(default_factory=list)
    postmortem_url: str | None = None

    def to_prompt_text(self) -> str:
        lines = [
            f"Incident ID: {self.incident_id}",
            f"Service: {self.service}",
            f"Title: {self.title}",
            f"Severity: {self.severity}",
            f"Duration: {self.start_time} → {self.end_time}",
            "",
            "User impact:",
            f"{self.impact}",
            "",
            "Root cause:",
            f"{self.root_cause}",
            "",
            "Timeline:",
            *[f"  {t}" for t in self.timeline],
            "",
            "Remediation applied:",
            f"{self.remediation_taken}",
        ]
        if self.contributing_factors:
            lines += ["", "Contributing factors:", *[f"  - {f}" for f in self.contributing_factors]]
        if self.monitoring_signals:
            lines += ["", "Monitoring signals that fired:", *[f"  - {s}" for s in self.monitoring_signals]]
        if self.commands_run:
            lines += ["", "Commands run during incident:", *[f"  $ {c}" for c in self.commands_run]]
        if self.prevention_todos:
            lines += ["", "Prevention actions identified:", *[f"  - {t}" for t in self.prevention_todos]]
        if self.postmortem_url:
            lines += ["", f"Postmortem: {self.postmortem_url}"]
        return "\n".join(lines)


def generate_runbook(
    incident: IncidentReport,
    api_key: str | None = None,
) -> AgentResult:
    """
    Generate a runbook entry from a structured incident report.

    Args:
        incident: Structured incident report.
        api_key:  Anthropic API key (defaults to ANTHROPIC_API_KEY env var).

    Returns:
        AgentResult with runbook data in result.data["runbook"].
    """
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    daily = os.environ.get("AGENT_MAX_COST_USD_PER_DAY", "").strip()
    config = RunConfig(
        agent_name="runbook-generator",
        model="claude-sonnet-5-5",
        max_tokens=3000,
        min_confidence=0.60,
        max_cost_usd=0.50,
        max_cost_usd_per_day=float(daily) if daily else None,
        reasoning_effort=effort_for("runbook-generator"),
        caller_identity=caller_from_env(),
        audit_log_path=os.environ.get("AGENT_AUDIT_LOG"),
        lifecycle_dir=os.environ.get("AGENT_LIFECYCLE_DIR"),
    )
    harness = AgentHarness(config=config, api_key=api_key)

    result = harness.run(
        system_prompt=SYSTEM_PROMPT,
        user_message=incident.to_prompt_text(),
        context={
            "incident.id": incident.incident_id,
            "incident.service": incident.service,
            "incident.severity": incident.severity,
        },
    )
    result._harness = harness  # type: ignore[attr-defined]
    return result


def runbook_is_complete(data: dict) -> str | None:
    """Return a refusal reason, or None when the entry meets the completion criteria.

    Confidence and any "done" field from the model are not consulted.
    """
    runbook = data.get("runbook")
    if not isinstance(runbook, dict):
        return "runbook object missing"
    if not str(runbook.get("title") or "").strip():
        return "runbook title missing"
    if not str(runbook.get("service") or "").strip():
        return "runbook service missing"
    diagnosis = runbook.get("diagnosis_steps") or []
    remediation = runbook.get("remediation") or []
    if not isinstance(diagnosis, list) or not isinstance(remediation, list):
        return "diagnosis_steps and remediation must be lists"
    if len(diagnosis) + len(remediation) < 1:
        return "runbook needs a diagnosis step or a remediation step"
    return None


def settle_runbook(result: AgentResult) -> None:
    """Approve a structurally complete runbook, or close the run as blocked.

    Leaves an approved run in `approved` so the caller can write the file
    (a mutating effect) before completing.
    """
    life = result.lifecycle
    if life is None or life.phase != Phase.GATED:
        return
    try:
        validate_runbook_evidence(result.data)
        reason = runbook_is_complete(result.data)
    except EvidenceError as exc:
        reason = str(exc)
    if reason or not result.gate_passed:
        life.submit_decision("HOLD", require_human=True)
        life.close(reason or result.gate_reason or "runbook incomplete")
        return
    life.submit_decision("WRITE", require_human=False)
    life.approve("policy:runbook-sections", note="required sections present")


def finish_runbook(result: AgentResult) -> None:
    """Complete an approved runbook. Refuses when the completion criteria do not hold."""
    life = result.lifecycle
    if life is None or life.phase != Phase.APPROVED:
        return
    life.complete(CompletionCriteria(frozenset({"WRITE"}), require_human=False), gate_passed=True)


def _runbook_accepted(result: AgentResult) -> bool:
    """True once the lifecycle has approved the entry or completed it. Confidence is not enough."""
    life = result.lifecycle
    if life is None:
        return result.passed()
    if life.phase == Phase.APPROVED:
        return True
    return life.phase == Phase.DONE and life.outcome == "completed"


def render_markdown(result: AgentResult) -> str:
    """Render the runbook as Markdown suitable for committing to a docs repo."""
    if not _runbook_accepted(result):
        reason = result.gate_reason or result.reasoning
        if result.lifecycle and result.lifecycle.history:
            reason = str(result.lifecycle.history[-1].get("reason") or reason)
        return (
            f"# Runbook generation failed\n\n"
            f"**Reason:** {reason}\n\n"
            f"Please write this runbook manually based on the incident report.\n"
        )

    rb = result.data.get("runbook", {})

    lines = [
        f"# {rb.get('title', 'Incident Runbook')}",
        "",
        f"**Service:** `{rb.get('service', '')}`  ",
        f"**Severity:** {rb.get('severity', '')}  ",
        f"**Last seen:** {rb.get('last_seen', 'unknown')}  ",
        f"**Generated by:** runbook-generator agent (confidence: {result.confidence:.0%})",
        "",
        "---",
        "",
    ]

    # Symptoms
    symptoms = rb.get("symptoms", [])
    if symptoms:
        lines += ["## Symptoms\n"]
        for s in symptoms:
            lines += [f"- **{s.get('signal', '')}:** {s.get('description', '')}\n"]
        lines.append("")

    # Quick triage
    triage = rb.get("quick_triage", [])
    if triage:
        lines += ["## Quick triage (do these first)\n"]
        for i, t in enumerate(triage, 1):
            lines += [f"{i}. {t}\n"]
        lines.append("")

    # Diagnosis
    diag = rb.get("diagnosis_steps", [])
    if diag:
        lines += ["## Diagnosis\n"]
        for step in diag:
            lines += [f"**Step {step.get('step', '')}:** {step.get('description', '')}"]
            if step.get("command"):
                lines += [f"```bash\n{step['command']}\n```"]
            if step.get("expected_output"):
                lines += [f"*Expected:* {step['expected_output']}\n"]
        lines.append("")

    # Remediation
    rem = rb.get("remediation", [])
    if rem:
        lines += ["## Remediation\n"]
        for step in rem:
            lines += [f"**Step {step.get('step', '')}:** {step.get('description', '')}"]
            if step.get("command"):
                lines += [f"```bash\n{step['command']}\n```"]
            if step.get("warning"):
                lines += [f"> ⚠️ **Warning:** {step['warning']}\n"]
        lines.append("")

    # Rollback
    rollback = rb.get("rollback", {})
    if rollback.get("applicable"):
        lines += [
            "## Rollback\n",
            rollback.get("notes", ""),
        ]
        if rollback.get("command"):
            lines += [f"```bash\n{rollback['command']}\n```"]
        lines.append("")

    # Prevention
    prevention = rb.get("prevention", [])
    if prevention:
        lines += ["## Prevention\n", "| Action | Owner | Priority |", "|--------|-------|----------|"]
        for p in prevention:
            lines.append(f"| {p.get('action', '')} | {p.get('owner', '')} | {p.get('priority', '')} |")
        lines.append("")

    # Dashboards and alerts
    dashboards = rb.get("dashboards", [])
    if dashboards:
        lines += ["## Dashboards\n"]
        for d in dashboards:
            url = d.get("url", "TBD")
            name = d.get("name", "")
            lines.append(f"- [{name}]({url})" if url != "TBD" else f"- {name} (link TBD)")
        lines.append("")

    escalation = rb.get("escalation_path")
    if escalation:
        lines += [f"## Escalation\n\n{escalation}\n"]

    return "\n".join(lines)


if __name__ == "__main__":
    # Example: read incident JSON from stdin or a file
    import argparse

    parser = argparse.ArgumentParser(description="Generate runbook from incident report")
    parser.add_argument("--incident", help="Path to incident JSON file (or use stdin)")
    parser.add_argument("--output-md", help="Write Markdown runbook to this path")
    args = parser.parse_args()

    if args.incident:
        raw = open(args.incident).read()
    else:
        raw = sys.stdin.read()

    data = json.loads(raw)
    incident = IncidentReport(**data)

    result = generate_runbook(incident)
    settle_runbook(result)
    harness = getattr(result, "_harness", None)
    if harness is not None and result.lifecycle is not None:
        harness.save_lifecycle(result.lifecycle)
        harness.audit(result)
    md = render_markdown(result)

    life = result.lifecycle
    if life is None or life.phase != Phase.APPROVED:
        print(md)
        sys.exit(1)
    if args.output_md:
        key = incident.incident_id.strip()
        if not key:
            print("incident_id is required as the idempotency key for the runbook write")
            sys.exit(1)
        try:
            if life.perform_effect("write-runbook", key, mutating=True):
                with open(args.output_md, "w") as handle:
                    handle.write(md)
                life.commit_effect("write-runbook", key, mutating=True)
                print(f"Runbook written to {args.output_md}")
        except OSError as exc:
            life.fail_effect("write-runbook", key, str(exc))
            print(md)
            sys.exit(1)
    else:
        print(md)
    finish_runbook(result)
    sys.exit(0 if life.phase == Phase.DONE and life.outcome == "completed" else 1)
