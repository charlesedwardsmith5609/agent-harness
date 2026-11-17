"""Degraded-mode contract. Offline: a fake client, no sockets, no sleeps.

One test per agent per condition. Each test asserts the result a caller sees
and the process exit code. pr-reviewer and deploy-risk already fail closed;
these tests lock that. runbook_exit_code is the red phase for the runbook tests.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agents import deploy_risk, pr_reviewer, runbook_gen
from agents.deploy_risk import DeploymentContext
from agents.runbook_gen import IncidentReport
from harness import agent as agent_mod
from harness.lifecycle import Phase, TransitionError

_DIFF = "diff --git a b\n+x\n"


class APIConnectionError(Exception):
    pass


class RateLimitError(Exception):
    def __init__(self) -> None:
        super().__init__("rate limited")
        self.status_code = 429


class _FakeAnthropic:
    """Stands in for anthropic.Anthropic. mode selects the fault."""

    mode = "ok"
    instances: list[_FakeAnthropic] = []

    def __init__(self, api_key: str | None = None) -> None:
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)
        _FakeAnthropic.instances.append(self)

    def _create(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(dict(kwargs))
        mode = _FakeAnthropic.mode
        if mode == "down":
            raise APIConnectionError("connection failed")
        if mode == "rate":
            raise RateLimitError()
        text = "not json" if mode == "bad" else json.dumps({"confidence": 0.9, "reasoning": "ok"})
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(input_tokens=10, output_tokens=10),
        )


def _calls() -> int:
    return sum(len(client.calls) for client in _FakeAnthropic.instances)


@pytest.fixture(autouse=True)
def _fake_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeAnthropic.instances = []
    _FakeAnthropic.mode = "ok"
    monkeypatch.setattr(agent_mod, "ANTHROPIC_AVAILABLE", True)
    monkeypatch.setattr(agent_mod, "anthropic", SimpleNamespace(Anthropic=_FakeAnthropic), raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    for name in (
        "AGENT_MAX_COST_USD_PER_DAY",
        "AGENT_AUDIT_LOG",
        "AGENT_LIFECYCLE_DIR",
        "DEPLOY_OVERRIDE",
        "DEPLOY_APPROVER",
    ):
        monkeypatch.delenv(name, raising=False)


def _mode(mode: str) -> None:
    _FakeAnthropic.mode = mode


def _prices(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(agent_mod.MODEL_PRICES, "claude-sonnet-5-5", (1_000_000.0, 1_000_000.0))


def _ctx() -> DeploymentContext:
    return DeploymentContext(
        service="payments",
        environment="production",
        diff_summary="tiny change",
        files_changed=["a.py"],
        lines_added=3,
        lines_deleted=1,
    )


def _incident() -> IncidentReport:
    return IncidentReport(
        incident_id="inc-1",
        service="payments",
        title="retries exhausted",
        severity="P1",
        start_time="2026-11-16T00:00:00Z",
        end_time="2026-11-16T01:00:00Z",
        impact="checkout failed",
        root_cause="bad deploy",
        timeline=["alert fired"],
        remediation_taken="rolled back",
    )


def _assert_failed(result, *, attempts: int, reason: str) -> None:
    assert result.success is False
    assert result.gate_passed is False
    assert result.confidence == 0.0
    assert result.data == {}
    life = result.lifecycle
    assert life is not None
    assert life.phase == Phase.DONE
    assert life.outcome == "failed"
    assert life.model_attempt == attempts
    assert life.effects == {}
    assert reason in result.gate_reason


# ── pr-reviewer ──────────────────────────────────────────────────────────────


def _review(mode: str | None, monkeypatch: pytest.MonkeyPatch | None = None):
    if mode is not None:
        _mode(mode)
    if monkeypatch is not None:
        _prices(monkeypatch)
    return pr_reviewer.review_diff(_DIFF, repo="o/r", pr_number=7, api_key="test-key")


def _assert_pr(result, *, attempts: int, calls_n: int, reason: str) -> None:
    assert pr_reviewer.review_exit_code(result) == 3
    _assert_failed(result, attempts=attempts, reason=reason)
    assert _calls() == calls_n
    comment = pr_reviewer.format_github_comment(result, 7)
    assert "Gate failed" in comment
    assert "Manual review required" in comment
    assert "gate_decision" not in result.data
    # Delivery of the failure comment is legal from done, and this check does not record the key.
    assert result.lifecycle.perform_effect("pr-comment", "o/r#7", mutating=False) is True
    assert result.lifecycle.effects == {}


def test_pr_reviewer_model_unavailable() -> None:
    result = _review("down")
    assert pr_reviewer.review_exit_code(result) == 3
    _assert_pr(result, attempts=2, calls_n=2, reason="connection failed")


def test_pr_reviewer_rate_limited() -> None:
    result = _review("rate")
    assert pr_reviewer.review_exit_code(result) == 3
    _assert_pr(result, attempts=2, calls_n=2, reason="rate limited")


def test_pr_reviewer_over_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _review(None, monkeypatch)
    assert pr_reviewer.review_exit_code(result) == 3
    _assert_pr(result, attempts=1, calls_n=0, reason="call not made")


def test_pr_reviewer_bad_json() -> None:
    result = _review("bad")
    assert pr_reviewer.review_exit_code(result) == 3
    _assert_pr(result, attempts=2, calls_n=2, reason="Expecting value")


# ── deploy-risk ──────────────────────────────────────────────────────────────


def _deploy(mode: str | None, monkeypatch: pytest.MonkeyPatch | None = None):
    if mode is not None:
        _mode(mode)
    if monkeypatch is not None:
        _prices(monkeypatch)
    ctx = _ctx()
    result = deploy_risk.assess_deploy_risk(ctx, api_key="test-key")
    settled = deploy_risk.settle_deploy(result, ctx, allow_caution=False)
    return result, settled


def _assert_deploy(result, settled, *, attempts: int, calls_n: int, reason: str) -> None:
    assert settled.recommendation == "HOLD"
    assert settled.exit_code == 1
    assert settled.note == ""
    _assert_failed(result, attempts=attempts, reason=reason)
    assert _calls() == calls_n
    assert "recommendation" not in result.data
    assert "defaulting to HOLD" in deploy_risk.format_deploy_output(result)


def test_deploy_risk_model_unavailable() -> None:
    result, settled = _deploy("down")
    assert settled.exit_code == 1 and settled.recommendation == "HOLD"
    _assert_deploy(result, settled, attempts=2, calls_n=2, reason="connection failed")


def test_deploy_risk_rate_limited() -> None:
    result, settled = _deploy("rate")
    assert settled.exit_code == 1 and settled.recommendation == "HOLD"
    _assert_deploy(result, settled, attempts=2, calls_n=2, reason="rate limited")


def test_deploy_risk_over_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    result, settled = _deploy(None, monkeypatch)
    assert settled.exit_code == 1 and settled.recommendation == "HOLD"
    _assert_deploy(result, settled, attempts=1, calls_n=0, reason="call not made")


def test_deploy_risk_bad_json() -> None:
    result, settled = _deploy("bad")
    assert settled.exit_code == 1 and settled.recommendation == "HOLD"
    _assert_deploy(result, settled, attempts=2, calls_n=2, reason="Expecting value")


# ── runbook-gen ──────────────────────────────────────────────────────────────


def _runbook(mode: str | None, monkeypatch: pytest.MonkeyPatch | None = None):
    if mode is not None:
        _mode(mode)
    if monkeypatch is not None:
        _prices(monkeypatch)
    result = runbook_gen.generate_runbook(_incident(), api_key="test-key")
    runbook_gen.settle_runbook(result)
    return result


def _assert_runbook(result, *, attempts: int, calls_n: int, reason: str) -> None:
    assert runbook_gen.runbook_exit_code(result) == 1
    _assert_failed(result, attempts=attempts, reason=reason)
    assert _calls() == calls_n
    markdown = runbook_gen.render_markdown(result)
    assert markdown.startswith("# Runbook generation failed")
    assert reason in markdown
    life = result.lifecycle
    assert life.phase != Phase.APPROVED
    with pytest.raises(TransitionError):
        life.perform_effect("write-runbook", "inc-1", mutating=True)


def test_runbook_gen_model_unavailable() -> None:
    result = _runbook("down")
    assert runbook_gen.runbook_exit_code(result) == 1
    _assert_runbook(result, attempts=2, calls_n=2, reason="connection failed")


def test_runbook_gen_rate_limited() -> None:
    result = _runbook("rate")
    assert runbook_gen.runbook_exit_code(result) == 1
    _assert_runbook(result, attempts=2, calls_n=2, reason="rate limited")


def test_runbook_gen_over_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _runbook(None, monkeypatch)
    assert runbook_gen.runbook_exit_code(result) == 1
    _assert_runbook(result, attempts=1, calls_n=0, reason="call not made")


def test_runbook_gen_bad_json() -> None:
    result = _runbook("bad")
    assert runbook_gen.runbook_exit_code(result) == 1
    _assert_runbook(result, attempts=2, calls_n=2, reason="Expecting value")
