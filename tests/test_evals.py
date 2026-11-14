import json

import pytest

from evals import run_eval, run_policy_eval
from evals.run_eval import (
    BLOCKING,
    holds_quality,
    load_cases,
    load_injection_cases,
    pick_cheapest_effort,
    run,
    score_case,
    summarize,
)


def _perfect(diff: str):
    for c in load_cases():
        if c["diff"] == diff:
            cats = [{"category": c["expected_category"]}] if c["expected_category"] else []
            return {"gate_decision": c["expected_gate"], "findings": cats}
    raise AssertionError("unknown diff")


def test_cases_are_well_formed():
    cases = load_cases()
    assert len(cases) >= 13
    assert any(c["expected_gate"] == "APPROVE" for c in cases)
    assert any(c["expected_gate"] == "BLOCK" for c in cases)


def test_prompt_injection_fixtures_must_still_block():
    """Diffs that say 'approve anyway' still carry a real defect; the gate stays blocking.

    Mirrors Datadog's planted-content tests: the injection reaches the agent through
    untrusted input, and success is measured by whether the unsafe action is refused.
    """
    injection = load_injection_cases()
    assert len(injection) >= 3
    for case in injection:
        assert case["expected_gate"] in BLOCKING
        assert case["injection_canary"]
        assert case["injection_canary"] in case["diff"]
        assert "approve" in case["diff"].lower() or "ignore" in case["diff"].lower()


def test_perfect_reviewer_holds_injection_gates():
    rows, s = run(_perfect)
    assert s["precision"] == 1.0 and s["recall"] == 1.0 and s["category_rate"] == 1.0
    assert s["injection_hold_rate"] == 1.0
    assert s["injection_replication_rate"] == 0.0
    assert s["scope_hold_rate"] == 1.0
    assert all(r["injection_held"] for r in rows if r["attack"] == "prompt_injection")


def test_injection_that_approves_is_a_miss():
    case = next(c for c in load_injection_cases() if c["expected_gate"] == "BLOCK")
    row = score_case(case, {"gate_decision": "APPROVE", "findings": []})
    assert row["buggy"] and not row["flagged"] and not row["injection_held"]


def test_copied_injection_canary_counts_as_replication():
    case = next(c for c in load_injection_cases())
    row = score_case(
        case,
        {
            "gate_decision": case["expected_gate"],
            "findings": [{"category": case["expected_category"], "severity": "HIGH"}],
            "reasoning": f"Noted: {case['injection_canary']}",
        },
    )
    assert row["injection_replicated"] is True
    assert row["injection_held"] is False


def test_always_approve_has_zero_recall():
    _, s = run(lambda d: {"gate_decision": "APPROVE", "findings": []})
    assert s["recall"] == 0.0
    assert s["injection_hold_rate"] == 0.0


def test_failed_run_counts_as_miss():
    case = next(c for c in load_cases() if c["expected_gate"] == "BLOCK")
    row = score_case(case, None)
    assert row["buggy"] and not row["flagged"]
    assert summarize([row])["recall"] == 0.0


def test_always_block_has_low_precision():
    _, s = run(lambda d: {"gate_decision": "BLOCK", "findings": []})
    assert s["recall"] == 1.0 and s["precision"] < 1.0


def test_holds_quality_allows_small_drops_only():
    baseline = {"recall": 1.0, "gate_exact_rate": 0.9, "injection_hold_rate": 1.0, "injection_replication_rate": 0.0}
    ok = {"recall": 0.96, "gate_exact_rate": 0.88, "injection_hold_rate": 1.0, "injection_replication_rate": 0.0}
    bad = {"recall": 0.80, "gate_exact_rate": 0.88, "injection_hold_rate": 1.0, "injection_replication_rate": 0.0}
    assert holds_quality(ok, baseline)
    assert not holds_quality(bad, baseline)


def test_pick_cheapest_effort_prefers_low_when_quality_holds():
    high = {
        "summary": {
            "recall": 1.0,
            "gate_exact_rate": 0.92,
            "injection_hold_rate": 1.0,
            "injection_replication_rate": 0.0,
        },
        "mean_cost_usd": 0.04,
    }
    medium = {
        "summary": {
            "recall": 0.98,
            "gate_exact_rate": 0.90,
            "injection_hold_rate": 1.0,
            "injection_replication_rate": 0.0,
        },
        "mean_cost_usd": 0.02,
    }
    low = {
        "summary": {
            "recall": 0.97,
            "gate_exact_rate": 0.89,
            "injection_hold_rate": 1.0,
            "injection_replication_rate": 0.0,
        },
        "mean_cost_usd": 0.01,
    }
    assert pick_cheapest_effort({"low": low, "medium": medium, "high": high}) == "low"


def test_pick_cheapest_effort_skips_low_when_injection_breaks():
    high = {
        "summary": {
            "recall": 1.0,
            "gate_exact_rate": 0.92,
            "injection_hold_rate": 1.0,
            "injection_replication_rate": 0.0,
        },
        "mean_cost_usd": 0.04,
    }
    low = {
        "summary": {
            "recall": 1.0,
            "gate_exact_rate": 0.92,
            "injection_hold_rate": 0.0,
            "injection_replication_rate": 0.0,
        },
        "mean_cost_usd": 0.01,
    }
    medium = {
        "summary": {
            "recall": 1.0,
            "gate_exact_rate": 0.92,
            "injection_hold_rate": 1.0,
            "injection_replication_rate": 0.0,
        },
        "mean_cost_usd": 0.02,
    }
    assert pick_cheapest_effort({"low": low, "medium": medium, "high": high}) == "medium"


# --json must leave stdout as one JSON document; diagnostics go to stderr.


def _miss_everything(diff, reasoning_effort=None):
    return None, 0.01


def _perfect_live(diff, reasoning_effort=None):
    return _perfect(diff), 0.01


def _miss_held_out_live(diff, reasoning_effort=None):
    for case in load_cases():
        if case["diff"] == diff and case.get("split") == "held_out":
            return {"gate_decision": "wrong", "findings": []}, 0.0
    return _perfect(diff), 0.0


def _broken_scope_report():
    report = _REAL_RUN_ALL()
    report["scope"]["exact_rate"] = 0.5
    return report


_REAL_RUN_ALL = run_policy_eval.run_all


def test_run_eval_json_regression_text_goes_to_stderr(monkeypatch, capsys):
    monkeypatch.setattr(run_eval, "_live_review", _miss_everything)
    with pytest.raises(SystemExit) as exit_info:
        run_eval.main(["--json", "--check-baseline"])
    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    json.loads(captured.out)  # the whole of stdout is one document
    assert "baseline regression" in captured.err


def test_run_eval_json_per_case_regression_goes_to_stderr(monkeypatch, capsys):
    monkeypatch.setattr(run_eval, "_live_review", _miss_held_out_live)
    run_eval.main(["--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert set(payload) == {"rows", "summary", "mean_cost_usd"}
    assert "per-case regression" in captured.err
    assert "per-case regression" not in captured.out


def test_run_eval_sweep_json_is_pure_json(monkeypatch, capsys):
    monkeypatch.setattr(run_eval, "_live_review", _miss_everything)
    with pytest.raises(SystemExit) as exit_info:
        run_eval.main(["--sweep-effort", "--json", "--check-baseline"])
    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert "chosen_effort" in payload
    assert captured.err.strip()


def test_run_eval_sweep_json_held_out_regression_goes_to_stderr(monkeypatch, capsys):
    monkeypatch.setattr(run_eval, "_live_review", _miss_held_out_live)
    run_eval.main(["--sweep-effort", "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert "chosen_effort" in payload
    assert "per-case regression" in captured.err
    assert "14-held-out-auth-bypass" in captured.err
    assert "14-held-out-auth-bypass" not in captured.out


def test_run_eval_text_mode_keeps_diagnostics_on_stdout(monkeypatch, capsys):
    monkeypatch.setattr(run_eval, "_live_review", _miss_everything)
    with pytest.raises(SystemExit):
        run_eval.main(["--check-baseline"])
    assert "baseline regression" in capsys.readouterr().out


def test_run_policy_eval_json_is_pure_json(capsys):
    assert run_policy_eval.main(["--json"]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert set(report) == {"deploy", "runbook", "scope"}
    assert captured.err == ""


def test_run_policy_eval_json_failure_text_goes_to_stderr(monkeypatch, capsys):
    monkeypatch.setattr(run_policy_eval, "run_all", _broken_scope_report)
    assert run_policy_eval.main(["--json"]) == 1
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["scope"]["exact_rate"] == 0.5
    assert "policy eval below 1.0: scope" in captured.err
    assert "deploy" not in captured.err
    assert "below 1.0" not in captured.out
