"""Gated human approvals expire at wait_until. The clock is an argument. No sleeps."""

from __future__ import annotations

import pytest

from harness.agent import AgentHarness, RunConfig
from harness.lifecycle import Phase, RunMachine, TransitionError
from harness.store import LifecycleStore


def _gated(*, ttl: float | None = 60, decision: str = "") -> RunMachine:
    return RunMachine(
        run_id="ab",
        agent_name="deploy-risk",
        phase=Phase.GATED,
        decision=decision,
        approval_ttl_s=ttl,
    )


def test_human_decision_arms_wait_until_once() -> None:
    life = _gated()
    life.submit_decision("HOLD", require_human=True, now=1000)
    assert life.wait_until == 1060
    life.submit_decision("HOLD", require_human=True, now=1059)
    assert life.wait_until == 1060


def test_approve_before_the_deadline() -> None:
    life = _gated(decision="HOLD")
    life.require_human = True
    life.wait_until = 1060
    life.approve("ada", now=1059)
    assert life.phase == Phase.APPROVED
    assert life.approval is not None and life.approval.actor == "ada"
    assert life.outcome == ""


def test_approve_at_the_deadline_expires() -> None:
    life = _gated(decision="HOLD")
    life.require_human = True
    life.wait_until = 1060
    with pytest.raises(TransitionError, match="approval wait elapsed"):
        life.approve("ada", now=1060)
    assert life.phase == Phase.DONE
    assert life.outcome == "expired"
    assert life.approval is None
    assert any(step["event"] == "expire" for step in life.history)


def test_close_at_the_deadline_is_expired_not_blocked() -> None:
    life = _gated(decision="HOLD")
    life.wait_until = 1060
    life.close("operator gave up", now=1060)
    assert life.phase == Phase.DONE
    assert life.outcome == "expired"


def test_policy_decision_and_missing_ttl_do_not_arm() -> None:
    policy = _gated()
    policy.submit_decision("APPROVE", require_human=False, now=1000)
    assert policy.wait_until is None
    human = _gated(ttl=None)
    human.submit_decision("HOLD", require_human=True, now=1000)
    assert human.wait_until is None


def test_legacy_snapshot_without_a_deadline_stays_gated() -> None:
    life = RunMachine.from_dict(
        {"run_id": "old", "agent_name": "t", "phase": "gated", "decision": "HOLD", "require_human": True}
    )
    assert life.wait_until is None
    assert life.approval_ttl_s is None
    assert life.expire_if_due(now=10**12) is False
    assert life.phase == Phase.GATED


def test_round_trip_keeps_the_deadline() -> None:
    life = _gated(decision="HOLD")
    life.submit_decision("HOLD", require_human=True, now=1000)
    again = RunMachine.from_dict(life.to_dict())
    assert again.approval_ttl_s == 60
    assert again.wait_until == 1060
    assert again.phase == Phase.GATED


def test_non_finite_ttl_and_deadline_are_refused() -> None:
    with pytest.raises(ValueError):
        RunConfig(agent_name="t", approval_ttl_s=float("nan"))
    with pytest.raises(ValueError):
        RunConfig(agent_name="t", approval_ttl_s=float("inf"))
    with pytest.raises(ValueError):
        RunConfig(agent_name="t", approval_ttl_s=-1)
    with pytest.raises(ValueError):
        RunMachine.from_dict(
            {"run_id": "r", "agent_name": "t", "phase": "gated", "wait_until": float("nan")}
        )
    assert RunConfig(agent_name="t").approval_ttl_s == 86400


def test_mutating_effect_at_the_deadline_expires_and_does_not_run() -> None:
    life = _gated(decision="HOLD")
    life.wait_until = 1060
    with pytest.raises(TransitionError):
        life.perform_effect("write-runbook", "inc-1", mutating=True, now=1060)
    assert life.phase == Phase.DONE
    assert life.outcome == "expired"
    assert life.effects == {}
    assert any(step["event"] == "expire" for step in life.history)


def test_repeat_commit_still_expires_a_due_wait() -> None:
    life = _gated(decision="HOLD")
    life.wait_until = 1060
    life.effects["note"] = "k1"
    life.commit_effect("note", "k1", mutating=False, now=1060)
    assert life.phase == Phase.DONE
    assert life.outcome == "expired"


def test_deadline_that_overflows_is_refused() -> None:
    life = _gated(ttl=1e308)
    with pytest.raises(ValueError):
        life.submit_decision("HOLD", require_human=True, now=1e308)
    assert life.wait_until is None
    assert life.decision == ""


def test_load_and_resume_persist_expiry(tmp_path) -> None:
    life = RunMachine(
        run_id="r1",
        agent_name="deploy-risk",
        phase=Phase.GATED,
        decision="HOLD",
        require_human=True,
        approval_ttl_s=60,
        wait_until=1.0,
    )
    store = LifecycleStore(tmp_path)
    store.save(life)
    harness = AgentHarness(
        RunConfig(agent_name="deploy-risk", lifecycle_dir=str(tmp_path), approval_ttl_s=60),
        api_key="",
    )
    loaded = harness.load_lifecycle("r1")
    assert loaded is not None
    assert loaded.phase == Phase.DONE and loaded.outcome == "expired"
    assert LifecycleStore(tmp_path).load("r1").outcome == "expired"  # type: ignore[union-attr]

    store.save(life)
    with pytest.raises(RuntimeError, match="approval wait elapsed"):
        harness.run("s", "u", run_id="r1")
    assert store.load("r1").outcome == "expired"  # type: ignore[union-attr]
