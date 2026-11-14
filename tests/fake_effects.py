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

    The lifecycle machine stays the source of truth for the idempotency key. The provider
    keeps only a script, a call log, and what the outside system saw:

    - executed[key]: how many times the effect really ran.
    - is_visible(key): whether a reader can see it yet.

    Scenarios:
    - SUCCESS: runs once, visible at once.
    - TIMEOUT_AFTER_COMMIT: runs and commits, then raises.
    - PARTIAL: reserves, fails with fail_effect, does not commit.
    - LATENT: runs and commits, but is not visible until reconcile(key).
    - DUPLICATE: the outside system already holds this key (a lost reply). The effect does
      not run again; the machine records it as landed.
    """

    def __init__(self, scenarios: list[EffectScenario | str]) -> None:
        self._queue = [EffectScenario(item) for item in scenarios]
        self.calls: list[tuple[str, str, str]] = []
        self.executed: dict[str, int] = {}
        self._held: set[str] = set()  # keys the outside system already holds
        self._visible: set[str] = set()
        self._pending: set[str] = set()

    def is_visible(self, key: str) -> bool:
        return key in self._visible

    def reconcile(self, key: str) -> bool:
        """Make a latent effect visible. Returns False when nothing was pending."""
        if key not in self._pending:
            return False
        self._pending.discard(key)
        self._visible.add(key)
        return True

    def _execute(self, key: str) -> None:
        self.executed[key] = self.executed.get(key, 0) + 1
        self._held.add(key)

    def apply(self, machine: RunMachine, name: str, key: str) -> str:
        scenario = self._queue.pop(0) if self._queue else EffectScenario.SUCCESS
        self.calls.append((scenario.value, name, key))
        if scenario is EffectScenario.DUPLICATE:
            self._held.add(key)  # the earlier delivery landed; the reply was lost
            self._visible.add(key)
        should_run = machine.perform_effect(name, key, mutating=True)
        if not should_run:
            return EffectScenario.DUPLICATE.value
        if key in self._held:
            machine.commit_effect(name, key, mutating=True)
            return EffectScenario.DUPLICATE.value
        if scenario is EffectScenario.PARTIAL:
            machine.fail_effect(name, key, "partial write")
            return EffectScenario.PARTIAL.value
        self._execute(key)
        machine.commit_effect(name, key, mutating=True)
        if scenario is EffectScenario.TIMEOUT_AFTER_COMMIT:
            self._visible.add(key)
            raise TimeoutError(f"{name} committed under {key} but the provider timed out")
        if scenario is EffectScenario.LATENT:
            self._pending.add(key)
            return EffectScenario.LATENT.value
        self._visible.add(key)
        return EffectScenario.SUCCESS.value
