from evals.check_baseline import load_baseline, regressions
from evals.run_policy_eval import run_all


def test_deploy_and_runbook_policy_evals_are_exact():
    report = run_all()
    assert report["deploy"]["exact_rate"] == 1.0
    assert report["runbook"]["exact_rate"] == 1.0


def test_baseline_floors_accept_perfect_and_reject_injection_miss():
    baseline = load_baseline()
    perfect = {
        "precision": 1.0,
        "recall": 1.0,
        "gate_exact_rate": 1.0,
        "category_rate": 1.0,
        "injection_hold_rate": 1.0,
        "injection_replication_rate": 0.0,
    }
    assert regressions(perfect, baseline) == []
    bad = {**perfect, "injection_hold_rate": 0.0}
    assert any("injection_hold_rate" in p for p in regressions(bad, baseline))
