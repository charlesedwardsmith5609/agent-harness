"""Harness tests. No network and no API key: the Anthropic client is faked."""

import json
from types import SimpleNamespace

import pytest

from harness import agent as agent_mod
from harness.agent import AgentHarness, RunConfig


def response(payload, in_tok=1000, out_tok=200):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok),
    )


class FakeAnthropic:
    """Stands in for anthropic.Anthropic and records every call."""

    instances: list["FakeAnthropic"] = []
    reply = None

    def __init__(self, api_key=None):
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)
        FakeAnthropic.instances.append(self)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeAnthropic.reply


@pytest.fixture(autouse=True)
def fake_sdk(monkeypatch):
    FakeAnthropic.instances = []
    FakeAnthropic.reply = response({"confidence": 0.9, "reasoning": "ok", "x": 1})
    monkeypatch.setattr(agent_mod, "ANTHROPIC_AVAILABLE", True)
    monkeypatch.setattr(agent_mod, "anthropic", SimpleNamespace(Anthropic=FakeAnthropic), raising=False)


def make(**overrides):
    cfg = RunConfig(agent_name="t", **overrides)
    return AgentHarness(cfg, api_key=overrides.pop("api_key", "k"))


def calls():
    return sum(len(i.calls) for i in FakeAnthropic.instances)


class TestFailClosed:
    def test_missing_key_fails_closed_and_makes_no_call(self):
        r = AgentHarness(RunConfig(agent_name="t"), api_key="").run("s", "u")
        assert not r.success and not r.passed()
        assert "ANTHROPIC_API_KEY" in r.gate_reason
        assert calls() == 0

    def test_missing_sdk_fails_closed(self, monkeypatch):
        monkeypatch.setattr(agent_mod, "ANTHROPIC_AVAILABLE", False)
        r = make().run("s", "u")
        assert not r.passed() and "not installed" in r.gate_reason

    def test_dry_run_never_passes(self):
        r = AgentHarness(RunConfig(agent_name="t", dry_run=True, min_confidence=0.0), api_key="").run("s", "u")
        assert r.success and not r.passed()
        assert r.cost_usd == 0 and calls() == 0

    def test_malformed_json_is_blocked(self):
        FakeAnthropic.reply = response("not json at all")
        r = make().run("s", "u")
        assert not r.success and not r.passed()

    def test_unknown_model_without_prices_is_refused(self):
        r = make(model="mystery-model").run("s", "u")
        assert not r.passed() and "No pricing" in r.gate_reason and calls() == 0


class TestGates:
    def test_passes_with_good_response(self):
        r = make().run("s", "u")
        assert r.passed() and r.confidence == 0.9 and r.data["x"] == 1
        assert calls() == 1

    def test_low_confidence_blocked(self):
        FakeAnthropic.reply = response({"confidence": 0.3, "reasoning": "unsure"})
        r = make().run("s", "u")
        assert r.success and not r.gate_passed and "Confidence" in r.gate_reason

    def test_cost_ceiling_blocks_before_calling(self):
        r = make(max_cost_usd=0.0001).run("s" * 5000, "u" * 5000)
        assert not r.passed() and "call not made" in r.gate_reason
        assert calls() == 0

    def test_cost_is_computed_from_reported_usage(self):
        r = make(price_per_m_input=2.0, price_per_m_output=10.0, model="any").run("s", "u")
        assert r.cost_usd == pytest.approx(1000 / 1e6 * 2.0 + 200 / 1e6 * 10.0)

    def test_markdown_fenced_json_is_parsed(self):
        FakeAnthropic.reply = response('```json\n{"confidence": 0.8, "reasoning": "r"}\n```')
        assert make().run("s", "u").passed()


def test_audit_log_written_for_pass_and_fail(tmp_path):
    log = tmp_path / "audit.jsonl"
    make(audit_log_path=str(log)).run("s", "u")
    AgentHarness(RunConfig(agent_name="t", audit_log_path=str(log)), api_key="").run("s", "u")
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert [r["gate_passed"] for r in records] == [True, False]
    assert all("timestamp" in r for r in records)


class TestPrReviewer:
    """The CI gate: exit-code mapping and the rule that a partial diff never approves."""

    def _run(self, monkeypatch, diff, decision):
        from agents import pr_reviewer

        FakeAnthropic.reply = response({"confidence": 0.9, "reasoning": "r", "gate_decision": decision, "findings": []})
        monkeypatch.setattr(pr_reviewer, "fetch_pr_diff", lambda **kw: diff)
        return pr_reviewer.review_pr(1, "o/r", token="", api_key="k", post_comment=False)

    def test_small_diff_keeps_approval(self, monkeypatch):
        r = self._run(monkeypatch, "diff --git a b", "APPROVE")
        assert r.data["gate_decision"] == "APPROVE" and "truncated" not in r.data

    def test_truncated_diff_cannot_approve(self, monkeypatch):
        r = self._run(monkeypatch, "x" * 50_000, "APPROVE")
        assert r.data["gate_decision"] == "REQUEST_CHANGES" and r.data["truncated"] is True

    def test_truncated_diff_keeps_block(self, monkeypatch):
        r = self._run(monkeypatch, "x" * 50_000, "BLOCK")
        assert r.data["gate_decision"] == "BLOCK"

    def test_comment_for_failed_gate_demands_manual_review(self):
        from agents.pr_reviewer import format_github_comment

        failed = AgentHarness(RunConfig(agent_name="t"), api_key="").run("s", "u")
        assert "Manual review required" in format_github_comment(failed, 1)


def test_default_model_is_priced():
    from harness.agent import MODEL_PRICES

    assert RunConfig(agent_name="x").model in MODEL_PRICES
