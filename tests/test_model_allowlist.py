"""Every audit row carries the model id. An allowlist rejects anything off the list."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from harness import agent as agent_mod
from harness.agent import AgentHarness, RunConfig
from harness.prefilter import PreFilterDecision
from tests.test_harness import FakeAnthropic, response


@pytest.fixture
def fake_sdk(monkeypatch):
    FakeAnthropic.instances = []
    FakeAnthropic.script = None
    FakeAnthropic.reply = response({"confidence": 0.9, "reasoning": "ok", "findings": []})
    monkeypatch.setattr(agent_mod, "ANTHROPIC_AVAILABLE", True)
    monkeypatch.setattr(agent_mod, "anthropic", SimpleNamespace(Anthropic=FakeAnthropic), raising=False)


def test_audit_row_includes_model_id(tmp_path):
    log = tmp_path / "audit.jsonl"
    harness = AgentHarness(
        RunConfig(agent_name="t", audit_log_path=str(log), dry_run=True, min_confidence=0.0)
    )
    result = harness.run("system", "user")
    assert result.model == "claude-sonnet-5-5"
    row = json.loads(log.read_text().strip())
    assert row["model"] == "claude-sonnet-5-5"


def test_allowlist_rejects_unknown_and_empty():
    RunConfig(agent_name="t", model="claude-sonnet-5-5", allowed_models=["claude-sonnet-5-5"])
    with pytest.raises(ValueError, match="not on the allowlist"):
        RunConfig(agent_name="t", model="other-model", allowed_models=["claude-sonnet-5-5"])
    with pytest.raises(ValueError, match="not on the allowlist"):
        RunConfig(agent_name="t", allowed_models=[])


def test_allowlist_is_enforced_at_call_time(fake_sdk):
    config = RunConfig(agent_name="t", allowed_models=["claude-sonnet-5-5"])
    harness = AgentHarness(config, api_key="k")
    config.model = "other-model"  # changed after construction
    with pytest.raises(ValueError, match="not on the allowlist"):
        harness._call_api("system", [{"role": "user", "content": "hi"}])
    assert FakeAnthropic.instances == []


def test_run_fails_closed_when_model_leaves_the_allowlist(fake_sdk):
    config = RunConfig(agent_name="t", allowed_models=["claude-sonnet-5-5"])
    harness = AgentHarness(config, api_key="k")
    config.model = "other-model"
    result = harness.run("system", "user")
    assert not result.success
    assert "allowlist" in result.reasoning
    assert FakeAnthropic.instances == []
    assert result.model_ran is None


def test_call_time_check_allows_a_listed_model(fake_sdk):
    config = RunConfig(agent_name="t", allowed_models=["claude-sonnet-5-5"], min_confidence=0.0)
    result = AgentHarness(config, api_key="k").run("system", "user")
    assert result.success
    assert len(FakeAnthropic.instances) == 1


def test_audit_records_the_model_that_ran(fake_sdk, tmp_path):
    log = tmp_path / "audit.jsonl"
    harness = AgentHarness(RunConfig(agent_name="t", audit_log_path=str(log), min_confidence=0.0), api_key="k")
    harness.run("system", "user")
    row = json.loads(log.read_text().strip())
    assert row["model"] == "claude-sonnet-5-5"  # configured
    assert row["model_ran"] == "claude-sonnet-5-5"
    assert row["llm_skipped"] is False


def test_audit_says_no_model_ran_when_the_prefilter_skipped(fake_sdk, tmp_path):
    log = tmp_path / "audit.jsonl"

    def confident(system: str, user: str) -> PreFilterDecision:
        return PreFilterDecision(
            confidence=0.95, reason="clear", data={"confidence": 0.95, "reasoning": "p", "findings": []}
        )

    config = RunConfig(
        agent_name="t",
        audit_log_path=str(log),
        prefilter=confident,
        prefilter_skip_threshold=0.5,
        min_confidence=0.0,
    )
    harness = AgentHarness(config, api_key="k")
    harness.run("system", "user")
    row = json.loads(log.read_text().strip())
    assert row["llm_skipped"] is True
    assert row["model_ran"] is None
    assert row["model"] == "claude-sonnet-5-5"
    assert FakeAnthropic.instances == []


def test_dry_run_records_no_model_ran(tmp_path):
    log = tmp_path / "audit.jsonl"
    harness = AgentHarness(RunConfig(agent_name="t", audit_log_path=str(log), dry_run=True))
    harness.run("system", "user")
    assert json.loads(log.read_text().strip())["model_ran"] is None
