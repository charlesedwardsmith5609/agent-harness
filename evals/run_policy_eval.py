"""Offline policy evals for deploy-risk, runbook-gen, and scope adherence (no model calls).

These score the lifecycle settlement rules and out-of-scope tool refusal against
seeded fixtures, so CI can lock gate behaviour without spending tokens.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from harness.agent import AgentResult, RunConfig
from harness.lifecycle import RunMachine
from harness.scope import filter_in_scope, scope_held

ROOT = Path(__file__).parent
DEPLOY_DIR = ROOT / "cases_deploy"
RUNBOOK_DIR = ROOT / "cases_runbook"
SCOPE_DIR = ROOT / "cases_scope"


def _gated_result(agent_name: str, data: dict[str, Any], *, confidence: float | None = None) -> AgentResult:
    life = RunMachine(run_id="eval", agent_name=agent_name)
    life.start()
    life.succeed()
    conf = float(data.get("confidence", confidence if confidence is not None else 0.9))
    return AgentResult(
        agent_name=agent_name,
        run_id="eval",
        success=True,
        data=dict(data),
        confidence=conf,
        reasoning=str(data.get("reasoning", "")),
        cost_usd=0.01,
        duration_s=0.0,
        input_tokens=0,
        output_tokens=0,
        model=RunConfig(agent_name=agent_name).model,
        gate_passed=conf >= 0.7,
        lifecycle=life,
    )


def load_deploy_cases() -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted(DEPLOY_DIR.glob("*.json"))]


def load_runbook_cases() -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted(RUNBOOK_DIR.glob("*.json"))]


def load_scope_cases() -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted(SCOPE_DIR.glob("*.json"))]


def score_deploy_case(case: dict[str, Any]) -> dict[str, Any]:
    from agents.deploy_risk import DeploymentContext, settle_deploy

    ctx_raw = case["context"]
    ctx = DeploymentContext(
        service=ctx_raw["service"],
        environment=ctx_raw["environment"],
        diff_summary=ctx_raw["diff_summary"],
        files_changed=list(ctx_raw.get("files_changed") or []),
        lines_added=int(ctx_raw.get("lines_added") or 0),
        lines_deleted=int(ctx_raw.get("lines_deleted") or 0),
        has_db_migration=bool(ctx_raw.get("has_db_migration")),
        recent_incident=bool(ctx_raw.get("recent_incident")),
        in_freeze_window=bool(ctx_raw.get("in_freeze_window")),
        deploy_time_utc=datetime(2026, 11, 5, int(ctx_raw.get("hour_utc", 4)), tzinfo=timezone.utc),
    )
    result = _gated_result("deploy-risk", case["evidence"])
    settled = settle_deploy(result, ctx, allow_caution=True, override=False)
    return {
        "id": case["id"],
        "expected": case["expected_recommendation"],
        "got": settled.recommendation,
        "exact": settled.recommendation == case["expected_recommendation"],
    }


def score_runbook_case(case: dict[str, Any]) -> dict[str, Any]:
    from agents.runbook_gen import settle_runbook

    result = _gated_result("runbook-generator", case["evidence"])
    settle_runbook(result)
    life = result.lifecycle
    assert life is not None
    decision_ok = life.decision == case["expected_decision"]
    phase_ok = life.phase.value == case["expected_phase"]
    outcome_ok = case.get("expected_outcome") is None or life.outcome == case["expected_outcome"]
    return {
        "id": case["id"],
        "expected_decision": case["expected_decision"],
        "got_decision": life.decision,
        "exact": decision_ok and phase_ok and outcome_ok,
        "phase": life.phase.value,
        "outcome": life.outcome,
    }


def score_scope_case(case: dict[str, Any]) -> dict[str, Any]:
    """Score out-of-scope refusal. Held means no OOS action remains executable."""
    tools = list(case.get("allowed_tools") or [])
    targets = list(case.get("allowed_targets") or [])
    proposed = list(case.get("proposed") or [])
    _allowed, refused = filter_in_scope(proposed, allowed_tools=tools, allowed_targets=targets)
    held = scope_held(proposed, allowed_tools=tools, allowed_targets=targets)
    refused_tools = [str(a.get("tool") or "") for a in refused]
    expected_refused = list(case.get("expected_refused_tools") or [])
    # Order-independent multiset compare for expected refused tool names.
    refused_ok = sorted(refused_tools) == sorted(expected_refused)
    expected_held = bool(case.get("expected_held", True))
    return {
        "id": case["id"],
        "held": held,
        "expected_held": expected_held,
        "refused_tools": refused_tools,
        "exact": held == expected_held and refused_ok,
    }


def run_all() -> dict[str, Any]:
    deploy_rows = [score_deploy_case(c) for c in load_deploy_cases()]
    runbook_rows = [score_runbook_case(c) for c in load_runbook_cases()]
    scope_rows = [score_scope_case(c) for c in load_scope_cases()]
    scope_hold = sum(r["held"] and r["exact"] for r in scope_rows) / len(scope_rows) if scope_rows else 1.0
    return {
        "deploy": {
            "rows": deploy_rows,
            "exact_rate": sum(r["exact"] for r in deploy_rows) / len(deploy_rows) if deploy_rows else 0.0,
        },
        "runbook": {
            "rows": runbook_rows,
            "exact_rate": sum(r["exact"] for r in runbook_rows) / len(runbook_rows) if runbook_rows else 0.0,
        },
        "scope": {
            "rows": scope_rows,
            "exact_rate": sum(r["exact"] for r in scope_rows) / len(scope_rows) if scope_rows else 0.0,
            "scope_hold_rate": scope_hold,
        },
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    report = run_all()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for family in ("deploy", "runbook", "scope"):
            extra = ""
            if family == "scope":
                extra = f" scope_hold_rate={report[family]['scope_hold_rate']:.2f}"
            print(f"[{family}] exact_rate={report[family]['exact_rate']:.2f}{extra}")
            for row in report[family]["rows"]:
                print(f"  {row}")
    bad = any(report[f]["exact_rate"] < 1.0 for f in ("deploy", "runbook", "scope"))
    raise SystemExit(1 if bad else 0)
