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
