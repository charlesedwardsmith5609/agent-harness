"""The baseline names the model it was measured on. A mismatch warns; strict mode fails.

Every test sets or clears PAIRING_STRICT so a gate run under PAIRING_STRICT=1 does not change the outcome.
"""

import json

import pytest

from agents.pr_reviewer import REVIEW_MODEL
from evals import check_baseline, run_eval
from evals.check_baseline import model_pairing
from tests.test_evals import _perfect


@pytest.fixture(autouse=True)
def _clear_strict(monkeypatch):
    monkeypatch.delenv("PAIRING_STRICT", raising=False)


def _perfect_live(diff, reasoning_effort=None):
    return _perfect(diff), 0.01


def _baseline(model):
    base = {"floors": {}, "ceilings": {}, "per_case": {}}
    if model is not None:
        base["model"] = model
    return base


def test_mismatch_warns_and_names_both_models():
    warnings, problems = model_pairing(_baseline("model-a"), "model-b", strict=False)
    assert problems == []
    assert len(warnings) == 1
    assert "model-a" in warnings[0] and "model-b" in warnings[0]


def test_same_model_is_silent():
    assert model_pairing(_baseline("model-a"), "model-a", strict=False) == ([], [])
    assert model_pairing(_baseline("model-a"), "model-a", strict=True) == ([], [])


def test_mismatch_under_strict_is_a_problem():
    warnings, problems = model_pairing(_baseline("model-a"), "model-b", strict=True)
    assert warnings == []
    assert len(problems) == 1


@pytest.mark.parametrize("strict", [False, True])
def test_baseline_without_model_only_warns(strict):
    warnings, problems = model_pairing(_baseline(None), "model-b", strict=strict)
    assert len(warnings) == 1 and "no model" in warnings[0]
    assert problems == []


@pytest.mark.parametrize("bad", ["", 3, None])
def test_baseline_with_unusable_model_only_warns(bad):
    base = _baseline(None)
    base["model"] = bad
    warnings, problems = model_pairing(base, "model-b", strict=True)
    assert len(warnings) == 1 and problems == []


def test_run_without_model_warns():
    warnings, problems = model_pairing(_baseline("model-a"), None, strict=True)
    assert len(warnings) == 1 and problems == []


def _write_summary(tmp_path, model):
    payload = {"summary": {}, "model": model} if model else {"summary": {}}
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_check_baseline_main_old_baseline_and_summary_do_not_crash(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(check_baseline, "load_baseline", lambda *a, **k: _baseline(None))
    monkeypatch.setattr("sys.argv", ["check_baseline", "--summary", _write_summary(tmp_path, None)])
    assert check_baseline.main() == 0
    assert "no model" in capsys.readouterr().out


def test_check_baseline_main_strict_mismatch_fails(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PAIRING_STRICT", "1")
    monkeypatch.setattr(check_baseline, "load_baseline", lambda *a, **k: _baseline("model-a"))
    monkeypatch.setattr("sys.argv", ["check_baseline", "--summary", _write_summary(tmp_path, "model-b")])
    assert check_baseline.main() == 1


def test_check_baseline_main_mismatch_not_strict_passes_with_warning(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(check_baseline, "load_baseline", lambda *a, **k: _baseline("model-a"))
    monkeypatch.setattr("sys.argv", ["check_baseline", "--summary", _write_summary(tmp_path, "model-b")])
    assert check_baseline.main() == 0
    assert "model-a" in capsys.readouterr().out


def test_run_eval_warns_on_stderr_and_stdout_stays_json(monkeypatch, capsys):
    monkeypatch.setattr(run_eval, "_live_review", _perfect_live)
    monkeypatch.setattr(run_eval, "load_baseline", lambda: _real_baseline_with("other-model"))
    run_eval.main(["--json", "--check-baseline"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["model"] == REVIEW_MODEL
    assert payload["settings"]["max_tokens"] == 2048
    assert "other-model" in captured.err


def test_run_eval_strict_mismatch_exits_1_with_json_stdout(monkeypatch, capsys):
    monkeypatch.setenv("PAIRING_STRICT", "1")
    monkeypatch.setattr(run_eval, "_live_review", _perfect_live)
    monkeypatch.setattr(run_eval, "load_baseline", lambda: _real_baseline_with("other-model"))
    with pytest.raises(SystemExit) as exit_info:
        run_eval.main(["--json", "--check-baseline"])
    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    json.loads(captured.out)
    assert "other-model" in captured.err


def test_run_eval_strict_without_check_baseline_still_returns(monkeypatch, capsys):
    monkeypatch.setenv("PAIRING_STRICT", "1")
    monkeypatch.setattr(run_eval, "_live_review", _perfect_live)
    monkeypatch.setattr(run_eval, "load_baseline", lambda: _real_baseline_with("other-model"))
    run_eval.main(["--json"])
    assert json.loads(capsys.readouterr().out)["model"] == REVIEW_MODEL


def test_sweep_payload_records_model_and_chosen_effort(monkeypatch, capsys):
    monkeypatch.setattr(run_eval, "_live_review", _perfect_live)
    run_eval.main(["--sweep-effort", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["model"] == REVIEW_MODEL
    assert payload["settings"]["reasoning_effort"] == payload["chosen_effort"]


def test_checked_in_baseline_is_labelled_offline():
    baseline = check_baseline.load_baseline()
    assert baseline["model"] == check_baseline.OFFLINE_MODEL == "offline"
    assert "settings" in baseline


def _real_baseline_with(model):
    base = check_baseline.load_baseline()
    base["model"] = model
    return base
