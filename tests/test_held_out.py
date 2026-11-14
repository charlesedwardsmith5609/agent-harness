"""Held-out cases are scored and reported, and kept out of tuning and mining."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from evals.check_baseline import case_regressions, load_baseline
from evals.mine_failures import mine
from evals.run_eval import (
    held_out_cases,
    held_out_problems,
    is_held_out,
    load_cases,
    run,
    run_with_costs,
    tuning_cases,
)
from harness import agent as agent_mod
from harness.agent import AgentHarness, RunConfig
from tests.test_harness import FakeAnthropic, response


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


def _gate_block_row(run_id: str, **overrides: object) -> dict:
    row: dict = {
        "agent_name": "pr-reviewer",
        "run_id": run_id,
        "success": True,
        "gate_passed": False,
        "gate_reason": "Confidence 0.10 below threshold 0.70",
        "data": {},
    }
    row.update(overrides)
    return row


def test_model_output_cannot_set_held_out(tmp_path: Path) -> None:
    """data.* comes from the model. It must never exclude a row from mining."""
    log = tmp_path / "audit.jsonl"
    rows = [
        _gate_block_row("m1", data={"held_out": True}),
        _gate_block_row("m2", data={"split": "held_out"}),
    ]
    log.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    written, _skipped = mine(log, tmp_path / "review")
    assert [path.name for path in written] == ["gate-block.json"]


@pytest.fixture
def fake_sdk(monkeypatch):
    FakeAnthropic.instances = []
    FakeAnthropic.script = None
    FakeAnthropic.reply = response({"confidence": 0.1, "reasoning": "unsure", "findings": []})
    monkeypatch.setattr(agent_mod, "ANTHROPIC_AVAILABLE", True)
    monkeypatch.setattr(agent_mod, "anthropic", SimpleNamespace(Anthropic=FakeAnthropic), raising=False)


def _real_audit_rows(log: Path, *, held_out: bool, count: int = 2) -> None:
    for _ in range(count):
        harness = AgentHarness(
            RunConfig(agent_name="pr-reviewer", audit_log_path=str(log), held_out=held_out),
            api_key="k",
        )
        harness.run("system", "review this")  # run() audits to audit_log_path


def test_real_audit_row_from_held_out_run_is_dropped_by_mining(fake_sdk, tmp_path: Path) -> None:
    log = tmp_path / "audit.jsonl"
    _real_audit_rows(log, held_out=True)
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert all(row["held_out"] is True for row in rows)
    written, _skipped = mine(log, tmp_path / "review")
    assert written == []


def test_real_audit_row_from_normal_run_is_still_mined(fake_sdk, tmp_path: Path) -> None:
    log = tmp_path / "audit.jsonl"
    _real_audit_rows(log, held_out=False)
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert all(row["held_out"] is False for row in rows)
    written, _skipped = mine(log, tmp_path / "review")
    assert [path.name for path in written] == ["gate-block.json"]


def test_baseline_held_out_id_with_no_scored_row_is_a_problem() -> None:
    rows, _summary = run(_perfect)
    without = [row for row in rows if row["id"] != "14-held-out-auth-bypass"]
    problems = case_regressions(without, load_baseline())
    assert any("14-held-out-auth-bypass" in problem and "no scored row" in problem for problem in problems)
    assert case_regressions(rows, load_baseline()) == []


def test_scored_row_without_baseline_entry_still_passes() -> None:
    rows, _summary = run(_perfect)
    baseline = {"per_case": {}}
    assert case_regressions(rows, baseline) == []


def test_sweep_reports_held_out_regression_without_tuning_on_it() -> None:
    seen: list[str] = []

    def miss_held_out(diff: str):
        for case in load_cases():
            if case["diff"] == diff and is_held_out(case):
                seen.append(case["id"])
                return {"gate_decision": "wrong", "findings": []}, 0.0
        return _perfect(diff), 0.0

    tuning_rows, _summary, _cost = run_with_costs(miss_held_out, tuning=True)
    assert seen == []  # tuning never reviewed a held-out case
    assert all(not is_held_out(case) for case in tuning_cases())
    problems = held_out_problems(miss_held_out, load_baseline())
    assert any("14-held-out-auth-bypass" in problem for problem in problems)
    assert seen == [case["id"] for case in held_out_cases()]
    assert held_out_problems(lambda diff: (_perfect(diff), 0.0), load_baseline()) == []
