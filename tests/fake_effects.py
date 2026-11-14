"""In-memory side-effect provider for lifecycle tests. No network."""

from __future__ import annotations

from enum import Enum

from harness.lifecycle import RunMachine


class EffectScenario(str, Enum):
    SUCCESS = "success"
    TIMEOUT_AFTER_COMMIT = "timeout_after_commit"
    PARTIAL = "partial"
    LATENT = "latent"
    DUPLICATE = "duplicate"


class FakeEffectProvider:
    """Drive perform_effect / commit_effect / fail_effect from a script of scenarios.

    The lifecycle machine stays the source of truth for the idempotency key.
    """

    def __init__(self, scenarios: list[EffectScenario | str]) -> None:
        self._queue = [EffectScenario(item) for item in scenarios]
        self.calls: list[tuple[str, str, str]] = []

    def apply(self, machine: RunMachine, name: str, key: str) -> str:
        scenario = self._queue.pop(0) if self._queue else EffectScenario.SUCCESS
        self.calls.append((scenario.value, name, key))
        should_run = machine.perform_effect(name, key, mutating=True)
        if not should_run:
            return EffectScenario.DUPLICATE.value
        if scenario is EffectScenario.PARTIAL:
            machine.fail_effect(name, key, "partial write")
            return EffectScenario.PARTIAL.value
        machine.commit_effect(name, key, mutating=True)
        if scenario is EffectScenario.TIMEOUT_AFTER_COMMIT:
            raise TimeoutError(f"{name} committed under {key} but the provider timed out")
        if scenario is EffectScenario.LATENT:
            return EffectScenario.LATENT.value
        if scenario is EffectScenario.DUPLICATE:
            return EffectScenario.DUPLICATE.value
        return EffectScenario.SUCCESS.value
