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
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from harness.agent import AgentHarness, RunConfig, AgentResult


SYSTEM_PROMPT = """\
You are a senior site reliability engineer writing a runbook entry based on an incident report.

Your goal is to produce documentation that a junior on-call engineer can follow at 3am
to diagnose and resolve a repeat of this incident — without needing to understand the full system.

Be specific. Use exact command syntax. Include expected outputs so the engineer knows if
the command worked. Prefer commands over UI instructions wherever possible.

You MUST respond with valid JSON only. No prose outside the JSON structure.

Required output schema:
{
  "confidence": <float 0.0–1.0, how complete and actionable this runbook is>,
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
    incident_id:   str
    service:       str
    title:         str
    severity:      str             # P1 / P2 / P3
    start_time:    str
    end_time:      str
    impact:        str             # User-visible impact description
    root_cause:    str             # Identified root cause
    timeline:      List[str]       # Ordered list of timeline entries
    remediation_taken: str         # What was actually done to fix it
    contributing_factors: List[str] = field(default_factory=list)
    monitoring_signals:   List[str] = field(default_factory=list)
    commands_run:         List[str] = field(default_factory=list)
    prevention_todos:     List[str] = field(default_factory=list)
    postmortem_url:       Optional[str] = None

    def to_prompt_text(self) -> str:
        lines = [
            f"Incident ID: {self.incident_id}",
            f"Service: {self.service}",
            f"Title: {self.title}",
            f"Severity: {self.severity}",
            f"Duration: {self.start_time} → {self.end_time}",
            f"",
            f"User impact:",
            f"{self.impact}",
            f"",
            f"Root cause:",
            f"{self.root_cause}",
            f"",
            f"Timeline:",
            *[f"  {t}" for t in self.timeline],
            f"",
            f"Remediation applied:",
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
    api_key:  Optional[str] = None,
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

    config = RunConfig(
        agent_name="runbook-generator",
        model="claude-sonnet-4-6",
        max_tokens=3000,
        temperature=0.3,
        min_confidence=0.60,
        max_cost_usd=0.50,
        audit_log_path=os.environ.get("AGENT_AUDIT_LOG"),
    )
    harness = AgentHarness(config=config, api_key=api_key)

    return harness.run(
        system_prompt=SYSTEM_PROMPT,
        user_message=incident.to_prompt_text(),
        context={
            "incident.id":       incident.incident_id,
            "incident.service":  incident.service,
            "incident.severity": incident.severity,
        },
    )


def render_markdown(result: AgentResult) -> str:
    """Render the runbook as Markdown suitable for committing to a docs repo."""
    if not result.passed():
        return (
            f"# Runbook generation failed\n\n"
            f"**Reason:** {result.gate_reason or result.reasoning}\n\n"
            f"Please write this runbook manually based on the incident report.\n"
        )

    rb = result.data.get("runbook", {})

    lines = [
        f"# {rb.get('title', 'Incident Runbook')}",
        f"",
        f"**Service:** `{rb.get('service', '')}`  ",
        f"**Severity:** {rb.get('severity', '')}  ",
        f"**Last seen:** {rb.get('last_seen', 'unknown')}  ",
        f"**Generated by:** runbook-generator agent (confidence: {result.confidence:.0%})",
        f"",
        f"---",
        f"",
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

    md = render_markdown(result)
    if args.output_md:
        with open(args.output_md, "w") as f:
            f.write(md)
        print(f"Runbook written to {args.output_md}")
    else:
        print(md)

    sys.exit(0 if result.passed() else 1)
