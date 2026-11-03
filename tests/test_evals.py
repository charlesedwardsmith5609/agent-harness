from evals.run_eval import load_cases, run, score_case, summarize


def _perfect(diff: str):
    for c in load_cases():
        if c["diff"] == diff:
            cats = [{"category": c["expected_category"]}] if c["expected_category"] else []
            return {"gate_decision": c["expected_gate"], "findings": cats}
    raise AssertionError("unknown diff")


def test_cases_are_well_formed():
    cases = load_cases()
    assert len(cases) >= 10
    assert any(c["expected_gate"] == "APPROVE" for c in cases)
    assert any(c["expected_gate"] == "BLOCK" for c in cases)


def test_perfect_reviewer_scores_one():
    _, s = run(_perfect)
    assert s["precision"] == 1.0 and s["recall"] == 1.0 and s["category_rate"] == 1.0


def test_always_approve_has_zero_recall():
    _, s = run(lambda d: {"gate_decision": "APPROVE", "findings": []})
    assert s["recall"] == 0.0


def test_failed_run_counts_as_miss():
    case = next(c for c in load_cases() if c["expected_gate"] == "BLOCK")
    row = score_case(case, None)
    assert row["buggy"] and not row["flagged"]
    assert summarize([row])["recall"] == 0.0


def test_always_block_has_low_precision():
    _, s = run(lambda d: {"gate_decision": "BLOCK", "findings": []})
    assert s["recall"] == 1.0 and s["precision"] < 1.0
