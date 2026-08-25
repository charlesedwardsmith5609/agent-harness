"""Tests for the agent harness core — runs without an Anthropic API key."""
import json
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from harness.agent import AgentHarness, RunConfig, AgentResult


@pytest.fixture
def config():
    return RunConfig(
        agent_name="test-agent",
        min_confidence=0.70,
        max_cost_usd=0.50,
    )


@pytest.fixture
def harness(config):
    # No API key — runs in stub mode
    return AgentHarness(config=config, api_key="")


class TestGates:
    def test_passes_when_confidence_above_threshold(self, harness):
        result = harness.run(
            system_prompt="Return JSON with confidence >= 0.8",
            user_message="test",
        )
        # Stub returns confidence=0.85, should pass
        assert result.confidence == 0.85
        assert result.gate_passed is True

    def test_blocks_when_confidence_below_threshold(self, config):
        config.min_confidence = 0.95   # Higher than stub's 0.85
        h = AgentHarness(config=config, api_key="")
        result = h.run(system_prompt="", user_message="test")
        assert result.gate_passed is False
        assert "Confidence" in result.gate_reason

    def test_blocks_when_cost_exceeds_max(self, config):
        config.max_cost_usd = 0.0001   # Impossibly low
        h = AgentHarness(config=config, api_key="")
        result = h.run(system_prompt="", user_message="test")
        # Stub cost = 100 input tokens * $3/M + 50 output * $15/M = $0.00105
        assert result.gate_passed is False
        assert "Cost" in result.gate_reason

    def test_passed_helper_requires_both_success_and_gate(self, config):
        config.min_confidence = 0.5
        h = AgentHarness(config=config, api_key="")
        result = h.run(system_prompt="", user_message="")
        # Gate should pass at 0.5 threshold with stub's 0.85 confidence
        assert result.passed() is True

    def test_cost_calculation(self, harness):
        result = harness.run(system_prompt="", user_message="test")
        # Stub: 100 input + 50 output
        expected = 100 / 1_000_000 * 3.00 + 50 / 1_000_000 * 15.00
        assert abs(result.cost_usd - expected) < 1e-8

    def test_result_serializes_to_dict(self, harness):
        result = harness.run(system_prompt="", user_message="test")
        d = result.to_dict()
        assert "agent_name" in d
        assert "cost_usd"   in d
        assert "confidence" in d
        # Should be JSON-serializable
        json.dumps(d)


class TestDeployRisk:
    def test_context_peak_traffic_detection(self):
        import datetime
        from agents.deploy_risk import DeploymentContext

        peak_ctx = DeploymentContext(
            service="test", environment="production",
            diff_summary="", files_changed=[],
            lines_added=10, lines_deleted=5,
            deploy_time_utc=datetime.datetime(2024, 1, 1, 18, 0),   # 18:00 UTC = peak
        )
        off_peak = DeploymentContext(
            service="test", environment="production",
            diff_summary="", files_changed=[],
            lines_added=10, lines_deleted=5,
            deploy_time_utc=datetime.datetime(2024, 1, 1, 6, 0),    # 06:00 UTC = off-peak
        )
        assert peak_ctx.peak_traffic() is True
        assert off_peak.peak_traffic() is False

    def test_context_to_prompt_includes_key_fields(self):
        import datetime
        from agents.deploy_risk import DeploymentContext

        ctx = DeploymentContext(
            service="payments-api",
            environment="production",
            diff_summary="Added retry logic",
            files_changed=["src/retry.py", "tests/test_retry.py"],
            lines_added=42,
            lines_deleted=5,
            has_db_migration=True,
            recent_incident=True,
        )
        prompt = ctx.to_prompt_context()
        assert "payments-api" in prompt
        assert "Database migration included: True" in prompt
        assert "Recent incident in this service: True" in prompt
        assert "42" in prompt
