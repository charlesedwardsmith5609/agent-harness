"""The run lifecycle owns approvals, retries, and completion. Prompts do not."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from harness import agent as agent_mod
from harness.lifecycle import (
    CompletionCriteria,
    Phase,
    RunMachine,
    TransitionError,
    is_model_retryable,
)
from tests.test_harness import FakeAnthropic, make, response


@pytest.fixture(autouse=True)
def fake_sdk(monkeypatch):
    FakeAnthropic.instances = []
    FakeAnthropic.script = None
    FakeAnthropic.reply = response({"confidence": 0.9, "reasoning": "ok", "x": 1})
    monkeypatch.setattr(agent_mod, "ANTHROPIC_AVAILABLE", True)
    monkeypatch.setattr(agent_mod, "anthropic", SimpleNamespace(Anthropic=FakeAnthropic), raising=False)


def machine() -> RunMachine:
    life = RunMachine(run_id="ab", agent_name="t", max_attempts=2, max_effect_attempts=2)
    life.start()
    life.succeed()
    return life


def _ctx(**overrides):
    from agents.deploy_risk import DeploymentContext

    fields = dict(
        service="payments",
        environment="production",
        diff_summary="tiny change",
        files_changed=["a.py"],
        lines_added=3,
        lines_deleted=1,
        deploy_time_utc=datetime(2026, 11, 5, 4, tzinfo=timezone.utc),
    )
    fields.update(overrides)
    return DeploymentContext(**fields)


def _evidence(**overrides):
    data = {
        "confidence": 0.9,
        "reasoning": "quiet",
        "recommendation": "DEPLOY",
        "risk_level": "LOW",
        "rollback_confidence": "HIGH",
        "risk_factors": [{"factor": "diff size", "severity": "LOW", "detail": "small"}],
    }
    data.update(overrides)
    return data


class TestMachine:
    def test_happy_path_reaches_done_only_through_completion(self):
        life = machine()
        assert life.phase == Phase.GATED
        life.submit_decision("APPROVE", require_human=False)
        with pytest.raises(TransitionError):
            life.complete(CompletionCriteria(frozenset({"APPROVE"})), gate_passed=True)
        life.approve("policy:pr-findings")
        life.complete(CompletionCriteria(frozenset({"APPROVE"})), gate_passed=True)
        assert life.phase == Phase.DONE and life.outcome == "completed"
        with pytest.raises(TransitionError):
            life.approve("charles")

    def test_model_cannot_approve(self):
        life = machine()
        life.submit_decision("HOLD", require_human=True)
        with pytest.raises(TransitionError):
            life.approve("model")
        assert life.phase == Phase.GATED

    def test_policy_cannot_approve_a_human_decision(self):
        life = machine()
        life.submit_decision("HOLD", require_human=True)
        with pytest.raises(TransitionError):
            life.approve("policy:deploy-risk")
        assert life.approval is None

    def test_hold_completes_only_with_a_person(self):
        life = machine()
        life.submit_decision("HOLD", require_human=True)
        with pytest.raises(TransitionError):
            life.complete(
                CompletionCriteria(frozenset({"HOLD"}), require_human=True, require_gate_passed=False),
                gate_passed=False,
            )
        life.approve("charles", note="accepted")
        life.complete(
            CompletionCriteria(frozenset({"HOLD"}), require_human=True, require_gate_passed=False),
            gate_passed=False,
        )
        assert life.outcome == "completed"
        assert life.approval is not None and life.approval.actor == "charles"

    def test_completion_does_not_read_a_model_done_flag(self):
        life = machine()
        life.submit_decision("HOLD", require_human=True)
        assert life.phase == Phase.GATED  # {"done": true} is not an event this machine accepts

    def test_model_retry_rejects_an_idempotency_key(self):
        life = RunMachine(run_id="ab", agent_name="t", max_attempts=2)
        life.start()
        life.fail_model("429", retryable=True)
        assert life.phase == Phase.FAILED_RETRYABLE
        with pytest.raises(TransitionError):
            life.retry(idempotency_key="invoice-9")
        life.retry()
        assert life.phase == Phase.RUNNING and life.model_attempt == 2

    def test_exhausted_model_failure_is_terminal(self):
        life = RunMachine(run_id="ab", agent_name="t", max_attempts=1)
        life.start()
        life.fail_model("429", retryable=True)
        assert life.phase == Phase.DONE and life.outcome == "failed"

    def test_mutating_effect_requires_approval_and_a_stable_key(self):
        life = machine()
        with pytest.raises(TransitionError):
            life.perform_effect("write-runbook", "inc-1", mutating=True)
        life.submit_decision("WRITE", require_human=False)
        life.approve("policy:runbook-sections")
        with pytest.raises(TransitionError):
            life.perform_effect("write-runbook", "", mutating=True)
        assert life.perform_effect("write-runbook", "inc-1", mutating=True) is True
        life.commit_effect("write-runbook", "inc-1", mutating=True)
        assert life.perform_effect("write-runbook", "inc-1", mutating=True) is False
        with pytest.raises(TransitionError):
            life.perform_effect("write-runbook", "inc-2", mutating=True)

    def test_side_effect_retry_must_reuse_the_key(self):
        life = machine()
        life.submit_decision("WRITE", require_human=False)
        life.approve("policy:runbook-sections")
        life.fail_effect("write-runbook", "inc-1", "disk full")
        assert life.phase == Phase.FAILED_RETRYABLE
        with pytest.raises(TransitionError):
            life.retry()
        with pytest.raises(TransitionError):
            life.retry(idempotency_key="inc-2")
        life.retry(idempotency_key="inc-1")
        assert life.phase == Phase.APPROVED
        life.fail_effect("write-runbook", "inc-1", "disk full again")
        assert life.phase == Phase.DONE and life.outcome == "failed"

    def test_delivery_is_legal_once_a_decision_exists(self):
        life = machine()
        life.submit_decision("BLOCK", require_human=True)
        life.close("high severity finding")
        assert life.perform_effect("pr-comment", "o/r#1", mutating=False) is True
        life.commit_effect("pr-comment", "o/r#1", mutating=False)
        assert life.perform_effect("pr-comment", "o/r#1", mutating=False) is False


class TestRetryClassification:
    def test_provider_throttle_is_retryable_and_a_bad_request_is_not(self):
        assert is_model_retryable(TimeoutError("slow"))
        throttled = type("APIStatusError", (Exception,), {"status_code": 429})()
        rejected = type("APIStatusError", (Exception,), {"status_code": 400})()
        assert is_model_retryable(throttled)
        assert not is_model_retryable(rejected)
        assert not is_model_retryable(RuntimeError("ANTHROPIC_API_KEY is not set"))


class TestDeployPolicy:
    def _settle(self, data, ctx=None, *, allow_caution=False, **kwargs):
        from agents.deploy_risk import settle_deploy

        FakeAnthropic.reply = response(data)
        result = make().run("s", "u")
        return settle_deploy(result, ctx or _ctx(), allow_caution=allow_caution, **kwargs), result

    def test_clean_deploy_is_approved_by_policy(self):
        settled, result = self._settle(_evidence())
        assert settled.exit_code == 0
        assert settled.recommendation == "DEPLOY"
        assert settled.approved_by == "policy:deploy-risk"
        assert result.lifecycle.outcome == "completed"
        assert result.data["recommendation"] == "DEPLOY"

    def test_freeze_holds_even_when_the_model_says_deploy(self):
        settled, result = self._settle(_evidence(), _ctx(in_freeze_window=True))
        assert settled.recommendation == "HOLD" and settled.exit_code == 1
        assert result.lifecycle.outcome == "blocked"
        assert result.lifecycle.approval is None

    def test_recent_incident_and_two_mediums_hold(self):
        incident, _ = self._settle(_evidence(), _ctx(recent_incident=True))
        assert incident.recommendation == "HOLD"
        factors = [
            {"factor": "a", "severity": "MEDIUM", "detail": ""},
            {"factor": "b", "severity": "MEDIUM", "detail": ""},
        ]
        doubled, _ = self._settle(_evidence(risk_factors=factors, risk_level="MEDIUM"))
        assert doubled.recommendation == "HOLD"

    def test_one_medium_or_peak_is_caution_and_cli_does_not_proceed(self):
        factors = [{"factor": "timing", "severity": "MEDIUM", "detail": ""}]
        cli, _ = self._settle(_evidence(risk_factors=factors, risk_level="MEDIUM"), allow_caution=False)
        assert cli.recommendation == "DEPLOY_WITH_CAUTION" and cli.exit_code == 1
        workflow, result = self._settle(
            _evidence(),
            _ctx(deploy_time_utc=datetime(2026, 11, 5, 15, tzinfo=timezone.utc)),
            allow_caution=True,
        )
        assert workflow.recommendation == "DEPLOY_WITH_CAUTION" and workflow.exit_code == 0
        assert result.lifecycle.outcome == "completed"

    def test_override_requires_a_person_and_does_not_rescue_a_failure(self):
        held, result = self._settle(_evidence(), _ctx(in_freeze_window=True), override=True, approver="")
        assert held.exit_code == 1 and result.lifecycle.outcome == "blocked"
        assert "DEPLOY_APPROVER" in held.note

        approved, result = self._settle(_evidence(), _ctx(in_freeze_window=True), override=True, approver="charles")
        assert approved.exit_code == 0 and approved.approved_by == "charles"
        assert result.lifecycle.outcome == "completed"

        refused, _ = self._settle(_evidence(), _ctx(in_freeze_window=True), override=True, approver="model")
        assert refused.exit_code == 1

        from harness.agent import AgentHarness, RunConfig

        failed = AgentHarness(RunConfig(agent_name="deploy-risk"), api_key="").run("s", "u")
        from agents.deploy_risk import settle_deploy

        crashed = settle_deploy(failed, _ctx(), allow_caution=False, override=True, approver="charles")
        assert crashed.exit_code == 1
        assert crashed.note == "DEPLOY_OVERRIDE does not approve a failed run"
        assert failed.lifecycle.outcome == "failed"

    def test_low_confidence_cannot_complete_a_deploy(self):
        settled, result = self._settle(_evidence(confidence=0.1))
        assert settled.recommendation == "DEPLOY" and settled.exit_code == 1
        assert result.lifecycle.outcome == "blocked"

    def test_missing_evidence_holds(self):
        settled, _ = self._settle(_evidence(risk_factors="nope"))
        assert settled.recommendation == "HOLD"


class TestRunbookCompletion:
    def test_missing_steps_are_not_complete_even_if_the_model_says_done(self):
        from agents.runbook_gen import render_markdown, settle_runbook

        FakeAnthropic.reply = response(
            {
                "confidence": 0.99,
                "reasoning": "ready",
                "done": True,
                "runbook": {"title": "Payments", "service": "payments"},
            }
        )
        result = make().run("s", "u")
        settle_runbook(result)
        assert result.lifecycle.outcome == "blocked"
        assert "diagnosis" in render_markdown(result)

    def test_required_sections_approve_and_a_write_is_idempotent(self, tmp_path):
        from agents.runbook_gen import finish_runbook, render_markdown, settle_runbook

        FakeAnthropic.reply = response(
            {
                "confidence": 0.9,
                "reasoning": "ok",
                "runbook": {
                    "title": "Payments retry",
                    "service": "payments",
                    "remediation": [
                        {"step": 1, "description": "restart", "command": "kubectl rollout undo", "warning": None}
                    ],
                },
            }
        )
        result = make().run("s", "u")
        settle_runbook(result)
        assert result.lifecycle.phase == Phase.APPROVED
        text = render_markdown(result)
        assert "Payments retry" in text
        key = "inc-9"
        path = tmp_path / "runbook.md"
        assert result.lifecycle.perform_effect("write-runbook", key, mutating=True) is True
        path.write_text(text)
        result.lifecycle.commit_effect("write-runbook", key, mutating=True)
        assert result.lifecycle.perform_effect("write-runbook", key, mutating=True) is False
        finish_runbook(result)
        assert result.lifecycle.outcome == "completed"


class TestReviewPolicy:
    def test_malformed_findings_block(self):
        from agents.pr_reviewer import pr_decision

        assert pr_decision(None, truncated=False) == ("REQUEST_CHANGES", "findings missing")
        assert pr_decision({"severity": "HIGH"}, truncated=False)[0] == "BLOCK"
        assert pr_decision([{"severity": "MEDIUM"}], truncated=True) == ("REQUEST_CHANGES", "medium severity finding")
        assert pr_decision([], truncated=True)[0] == "REQUEST_CHANGES"
        assert pr_decision([{"severity": "LOW"}], truncated=False)[0] == "APPROVE"
