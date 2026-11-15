"""Injection and scope evals report a rate per category. Scope cases carry category."""

from __future__ import annotations

from evals.run_eval import injection_by_category, load_injection_cases, run
from evals.run_policy_eval import load_scope_cases, run_all


def _perfect(diff: str):
    from evals.run_eval import load_cases

    for case in load_cases():
        if case["diff"] == diff:
            cats = [{"category": case["expected_category"]}] if case.get("expected_category") else []
            return {"gate_decision": case["expected_gate"], "findings": cats}
    raise AssertionError("unknown diff")


def test_scope_cases_have_categories_including_poisoned_memory() -> None:
    cases = load_scope_cases()
    assert all(case.get("category") for case in cases)
    poisoned = next(case for case in cases if case["id"] == "05-poisoned-memory")
    assert poisoned["category"] == "poisoned-memory"
    report = run_all()["scope"]["by_category"]
    assert report["poisoned-memory"]["cases"] == 1
    assert report["poisoned-memory"]["rate"] == 1.0


def test_injection_cases_report_by_existing_category() -> None:
    assert all("expected_category" in case for case in load_injection_cases())
    rows, _summary = run(_perfect)
    report = injection_by_category(rows)
    assert report["security"]["rate"] == 1.0
    assert report["security"]["cases"] >= 1
