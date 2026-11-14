"""Idempotency keys and lifecycle transitions through the fake side-effect provider."""

from __future__ import annotations

import pytest

from harness.lifecycle import Phase, RunMachine, TransitionError
from tests.fake_effects import EffectScenario, FakeEffectProvider


def _approved() -> RunMachine:
    machine = RunMachine(run_id="r", agent_name="t")
    machine.start()
    machine.succeed()
    machine.submit_decision("SHIP", require_human=False)
    machine.approve("person")
    return machine


def test_success_then_duplicate_reuses_the_key() -> None:
    machine = _approved()
    provider = FakeEffectProvider([EffectScenario.SUCCESS, EffectScenario.DUPLICATE])
    assert provider.apply(machine, "deploy", "key-1") == "success"
    assert provider.apply(machine, "deploy", "key-1") == "duplicate"
    assert machine.effects["deploy"] == "key-1"
    assert machine.phase == Phase.APPROVED


def test_timeout_after_commit_is_not_replayed() -> None:
    machine = _approved()
    provider = FakeEffectProvider([EffectScenario.TIMEOUT_AFTER_COMMIT, EffectScenario.SUCCESS])
    with pytest.raises(TimeoutError):
        provider.apply(machine, "deploy", "key-1")
    assert machine.effects["deploy"] == "key-1"
    assert provider.apply(machine, "deploy", "key-1") == "duplicate"
    assert [call[0] for call in provider.calls] == ["timeout_after_commit", "success"]


def test_partial_failure_retries_only_with_the_same_key() -> None:
    machine = _approved()
    provider = FakeEffectProvider([EffectScenario.PARTIAL, EffectScenario.SUCCESS])
    assert provider.apply(machine, "deploy", "key-1") == "partial"
    assert "deploy" not in machine.effects
    assert machine.phase == Phase.FAILED_RETRYABLE
    with pytest.raises(TransitionError):
        machine.retry(idempotency_key="other-key")
    machine.retry(idempotency_key="key-1")
    assert machine.phase == Phase.APPROVED
    assert provider.apply(machine, "deploy", "key-1") == "success"
    assert machine.effects["deploy"] == "key-1"


def test_latent_commit_is_already_applied() -> None:
    machine = _approved()
    provider = FakeEffectProvider([EffectScenario.LATENT, EffectScenario.SUCCESS])
    assert provider.apply(machine, "deploy", "key-1") == "latent"
    assert machine.effects["deploy"] == "key-1"
    assert provider.apply(machine, "deploy", "key-1") == "duplicate"


def test_effect_from_running_is_refused() -> None:
    machine = RunMachine(run_id="r", agent_name="t")
    machine.start()
    provider = FakeEffectProvider([EffectScenario.SUCCESS])
    with pytest.raises(TransitionError):
        provider.apply(machine, "deploy", "key-1")
