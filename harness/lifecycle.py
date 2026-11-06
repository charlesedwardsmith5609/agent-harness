"""Run lifecycle. Approvals, retries, and completion live here, not in prompts.

The model may return evidence (findings, risk factors, a draft). It may not
approve its own output, retry a side effect, or declare the run finished.
Callers propose a decision; this machine accepts or refuses the transition.

States: planned → running → gated → approved → done
A retryable failure from running or approved enters failed-retryable.
Model retries are side-effect free. A side-effect retry must present the
same idempotency key, so a second attempt cannot post a second credit note.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Phase(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    GATED = "gated"
    APPROVED = "approved"
    FAILED_RETRYABLE = "failed-retryable"
    DONE = "done"


class TransitionError(Exception):
    """The event is not legal in the current phase, or a guard refused it."""


class ModelOutputError(Exception):
    """The model returned something we could not parse. No side effect has happened."""


# Fixed destinations. "retry" is None because it returns to whichever phase failed.
_TRANSITIONS: dict[tuple[Phase, str], Phase | None] = {
    (Phase.PLANNED, "start"): Phase.RUNNING,
    (Phase.RUNNING, "succeed"): Phase.GATED,
    (Phase.RUNNING, "fail_retryable"): Phase.FAILED_RETRYABLE,
    (Phase.RUNNING, "fail"): Phase.DONE,
    (Phase.FAILED_RETRYABLE, "retry"): None,
    (Phase.GATED, "approve"): Phase.APPROVED,
    (Phase.GATED, "close"): Phase.DONE,
    (Phase.APPROVED, "complete"): Phase.DONE,
    (Phase.APPROVED, "fail_retryable"): Phase.FAILED_RETRYABLE,
    (Phase.APPROVED, "fail"): Phase.DONE,
}

_MODEL_ACTORS = frozenset({"model", "agent", "assistant", "system", "llm"})

# Model-call failures that did not change a business system. Status codes are the
# ones a provider uses for "try again"; a 400 is the caller's bug and is not retried.
_RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 529})
_RETRYABLE_TYPES = frozenset({"APIConnectionError", "APITimeoutError", "RateLimitError", "InternalServerError"})


def is_model_retryable(exc: BaseException) -> bool:
    """True when the model call failed before any side effect and may be repeated."""
    if isinstance(exc, (ModelOutputError, TimeoutError, ConnectionError)):
        return True
    if type(exc).__name__ in _RETRYABLE_TYPES:
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and status in _RETRYABLE_STATUS


@dataclass(frozen=True)
class Approval:
    actor: str
    decision: str
    note: str = ""


@dataclass(frozen=True)
class CompletionCriteria:
    """What "done" means. Checked by the machine; a model field cannot satisfy it."""

    allowed_decisions: frozenset[str]
    require_human: bool = False
    require_gate_passed: bool = True

    def refusal(self, machine: RunMachine, *, gate_passed: bool) -> str | None:
        if machine.phase != Phase.APPROVED:
            return f"completion is only allowed from approved (currently {machine.phase.value})"
        if machine.decision not in self.allowed_decisions:
            return f"decision {machine.decision!r} does not meet completion criteria"
        if self.require_gate_passed and not gate_passed:
            return "confidence or cost gate did not pass"
        if self.require_human:
            actor = machine.approval.actor if machine.approval else ""
            head = actor.split(":", 1)[0].lower()
            if not actor or actor.startswith("policy:") or head in _MODEL_ACTORS:
                return "completion requires approval from a person"
        return None


def _validate_actor(actor: str) -> str:
    cleaned = actor.strip()
    if not cleaned:
        raise TransitionError("approval requires a named actor")
    head = cleaned.split(":", 1)[0].lower()
    if head in _MODEL_ACTORS or cleaned.lower() in _MODEL_ACTORS:
        raise TransitionError("the model cannot approve its own output")
    if head == "policy" and ":" not in cleaned:
        raise TransitionError("policy approval must name the rule (policy:<rule>)")
    return cleaned


@dataclass
class RunMachine:
    """Authoritative lifecycle for one agent run. Persist the history via the audit log."""

    run_id: str
    agent_name: str
    max_attempts: int = 2
    max_effect_attempts: int = 2
    phase: Phase = Phase.PLANNED
    outcome: str = ""
    decision: str = ""
    require_human: bool = False
    model_attempt: int = 0
    approval: Approval | None = None
    effects: dict[str, str] = field(default_factory=dict)
    effect_attempts: dict[str, int] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)
    _retry_to: Phase = field(default=Phase.RUNNING, repr=False)
    _effect_retry: tuple[str, str] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.max_effect_attempts < 1:
            raise ValueError("max_effect_attempts must be >= 1")

    # ── Events ────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._transition("start")
        self.model_attempt = 1

    def succeed(self) -> None:
        """Model call returned parseable evidence. The run is now waiting on a decision."""
        self._transition("succeed")

    def fail_model(self, reason: str, *, retryable: bool) -> None:
        """End a model attempt. Retryable failures stop in failed-retryable; the rest finish."""
        if self.phase != Phase.RUNNING:
            raise TransitionError(f"fail_model is not allowed from {self.phase.value}")
        if retryable and self.model_attempt < self.max_attempts:
            self._retry_to = Phase.RUNNING
            self._effect_retry = None
            self._transition("fail_retryable", reason=reason)
            return
        self.outcome = "failed"
        self._transition("fail", reason=reason)

    def retry(self, *, idempotency_key: str | None = None) -> None:
        """Leave failed-retryable. A side-effect retry must reuse its business key."""
        if self.phase != Phase.FAILED_RETRYABLE:
            raise TransitionError(f"retry is not allowed from {self.phase.value}")
        if self._retry_to == Phase.APPROVED:
            expected = self._effect_retry[1] if self._effect_retry else ""
            if not idempotency_key or idempotency_key != expected:
                raise TransitionError(f"side-effect retry requires idempotency key {expected!r}")
        else:
            if idempotency_key:
                raise TransitionError("model retries are side-effect free and do not take an idempotency key")
            if self.model_attempt >= self.max_attempts:
                raise TransitionError("model attempts exhausted")
            self.model_attempt += 1
        self._transition("retry")

    def submit_decision(self, decision: str, *, require_human: bool) -> None:
        """Record the decision the policy (not the model) assigned. Stays in gated."""
        if self.phase != Phase.GATED:
            raise TransitionError(f"a decision can only be submitted from gated (currently {self.phase.value})")
        cleaned = decision.strip()
        if not cleaned:
            raise TransitionError("decision is required")
        self.decision = cleaned
        self.require_human = require_human
        self._note("decide", reason=cleaned)

    def approve(self, actor: str, note: str = "") -> None:
        """Record who accepted the decision. Policy rules use an actor of policy:<rule>."""
        if self.phase != Phase.GATED:
            raise TransitionError(f"approve is not allowed from {self.phase.value}")
        if not self.decision:
            raise TransitionError("submit a decision before approving")
        cleaned = _validate_actor(actor)
        if self.require_human and cleaned.startswith("policy:"):
            raise TransitionError(f"{self.decision} requires a person; policy cannot approve it")
        self.approval = Approval(actor=cleaned, decision=self.decision, note=note)
        self._transition("approve", actor=cleaned, reason=note)

    def close(self, reason: str) -> None:
        """Finish a gated run that was not approved. Outcome is blocked, not a pass."""
        if self.phase != Phase.GATED:
            raise TransitionError(f"close is not allowed from {self.phase.value}")
        self.outcome = "blocked"
        self._transition("close", reason=reason)

    def complete(self, criteria: CompletionCriteria, *, gate_passed: bool) -> None:
        """Move approved → done only when the criteria hold. The model is not asked."""
        reason = criteria.refusal(self, gate_passed=gate_passed)
        if reason:
            raise TransitionError(reason)
        self.outcome = "completed"
        self._transition("complete", reason=self.decision)

    # ── Side effects ──────────────────────────────────────────────────────

    def perform_effect(self, name: str, key: str, *, mutating: bool) -> bool:
        """Reserve a side effect.

        Returns True if the caller should perform it, False if this key already landed.
        Mutating effects (writes, deploys, posts that change a business system) are
        legal only from approved. Delivery of an already-decided result may happen
        from gated, approved, or done. Nothing is legal from running or failed-retryable:
        retry the transition first, and bring the same key.
        """
        self._check_effect(name, key, mutating=mutating)
        if self.effects.get(name) == key:
            return False
        return True

    def commit_effect(self, name: str, key: str, *, mutating: bool) -> None:
        """Record that the effect landed. A second commit of the same key is a no-op."""
        if self.effects.get(name) == key:
            return
        self._check_effect(name, key, mutating=mutating)
        self.effects[name] = key
        self._note("effect", reason=f"{name}:{key}")

    def fail_effect(self, name: str, key: str, reason: str) -> None:
        """A mutating effect failed. Retry is possible only with the same key, and only up to the cap."""
        if self.phase != Phase.APPROVED:
            raise TransitionError(f"fail_effect is not allowed from {self.phase.value}")
        if not key:
            raise TransitionError(f"side effect {name!r} requires an idempotency key")
        used = self.effect_attempts.get(name, 0) + 1
        self.effect_attempts[name] = used
        self._effect_retry = (name, key)
        if used >= self.max_effect_attempts:
            self.outcome = "failed"
            self._transition("fail", reason=reason)
            return
        self._retry_to = Phase.APPROVED
        self._transition("fail_retryable", reason=reason)

    def _check_effect(self, name: str, key: str, *, mutating: bool) -> None:
        if not name or not str(key).strip():
            raise TransitionError("a side effect requires a name and an idempotency key")
        previous = self.effects.get(name)
        if previous is not None and previous != key:
            raise TransitionError(f"side effect {name!r} already applied with idempotency key {previous!r}")
        if mutating:
            if self.phase != Phase.APPROVED:
                raise TransitionError(
                    f"mutating side effect {name!r} is only allowed from approved (currently {self.phase.value})"
                )
            return
        if self.phase not in {Phase.GATED, Phase.APPROVED, Phase.DONE}:
            raise TransitionError(
                f"side effect {name!r} is not allowed from {self.phase.value}; resolve the retry first"
            )

    # ── Internals ─────────────────────────────────────────────────────────

    def _transition(self, event: str, *, reason: str = "", actor: str = "") -> None:
        key = (self.phase, event)
        if key not in _TRANSITIONS:
            legal = sorted(e for (phase, e) in _TRANSITIONS if phase == self.phase)
            allowed = ", ".join(legal) if legal else "none"
            raise TransitionError(f"{event} is not allowed from {self.phase.value} (allowed: {allowed})")
        dest = _TRANSITIONS[key]
        if dest is None:
            dest = self._retry_to
        previous = self.phase
        self.phase = dest
        self.history.append(self._entry(event, previous, dest, reason=reason, actor=actor))

    def _note(self, event: str, *, reason: str = "", actor: str = "") -> None:
        self.history.append(self._entry(event, self.phase, self.phase, reason=reason, actor=actor))

    def _entry(
        self,
        event: str,
        previous: Phase,
        dest: Phase,
        *,
        reason: str,
        actor: str,
    ) -> dict[str, Any]:
        return {
            "event": event,
            "from": previous.value,
            "to": dest.value,
            "attempt": self.model_attempt,
            "reason": reason,
            "actor": actor,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    def to_dict(self) -> dict[str, Any]:
        approval = None
        if self.approval is not None:
            approval = {
                "actor": self.approval.actor,
                "decision": self.approval.decision,
                "note": self.approval.note,
            }
        return {
            "phase": self.phase.value,
            "outcome": self.outcome,
            "decision": self.decision,
            "require_human": self.require_human,
            "model_attempt": self.model_attempt,
            "approval": approval,
            "effects": dict(self.effects),
            "history": list(self.history),
        }
