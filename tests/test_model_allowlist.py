"""Every audit row carries the model id. An allowlist rejects anything off the list."""

from __future__ import annotations

import json

import pytest

from harness.agent import AgentHarness, RunConfig


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
