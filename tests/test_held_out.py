"""Held-out cases are scored and reported, and kept out of tuning and mining."""

from __future__ import annotations

import json
from pathlib import Path

from evals.check_baseline import case_regressions, load_baseline
from evals.mine_failures import mine
from evals.run_eval import is_held_out, load_cases, run, run_with_costs, tuning_cases


def _perfect(diff: str):
    for case in load_cases():
        if case["diff"] == diff:
            cats = [{"category": case["expected_category"]}] if case.get("expected_category") else []
            return {"gate_decision": case["expected_gate"], "findings": cats}
    raise AssertionError("unknown diff")


def test_held_out_case_is_excluded_from_tuning() -> None:
    cases = load_cases()
    held = [case for case in cases if case["id"] == "14-held-out-auth-bypass"]
    assert len(held) == 1
    assert is_held_out(held[0])
    assert all(case["id"] != "14-held-out-auth-bypass" for case in tuning_cases())
    rows, _summary, _cost = run_with_costs(lambda diff: (_perfect(diff), 0.0), tuning=True)
    assert all(row["id"] != "14-held-out-auth-bypass" for row in rows)


def test_per_case_regression_against_baseline() -> None:
    rows, _summary = run(_perfect)
    assert case_regressions(rows, load_baseline()) == []
    missed = [dict(row) for row in rows]
    for row in missed:
        if row["id"] == "14-held-out-auth-bypass":
            row["gate_exact"] = False
    problems = case_regressions(missed, load_baseline())
    assert any("14-held-out-auth-bypass" in problem for problem in problems)


def test_mining_drops_held_out_rows(tmp_path: Path) -> None:
    log = tmp_path / "audit.jsonl"
    rows = [
        {
            "agent_name": "pr-reviewer",
            "run_id": "h1",
            "split": "held_out",
            "success": True,
            "gate_passed": False,
            "gate_reason": "Confidence 0.10 below threshold 0.70",
            "data": {},
        },
        {
            "agent_name": "pr-reviewer",
            "run_id": "h2",
            "held_out": True,
            "success": True,
            "gate_passed": False,
            "gate_reason": "Confidence 0.20 below threshold 0.70",
            "data": {},
        },
    ]
    log.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    written, skipped = mine(log, tmp_path / "review")
    assert skipped == 0
    assert written == []
    assert not (tmp_path / "review" / "gate-block.json").exists()
