# Step 5 — approval wait-until, and the two closed questions

Charles answered the three open questions on 2026-11-17.

1. A gated approval that sits too long expires. Build the lifecycle wait-until.
2. HOLD stays the deploy-risk result when the model is down. Do not build a rules-only fallback.
3. The opt-in audit-log refusal (`max_cost_usd_per_day`) is enough. Do not build the store counter.

## Timer

A human decision can stay in `gated` until someone calls `approve` or `close`. That wait gets a deadline. Nothing in this process sleeps or schedules a wake-up. The deadline is a field on the machine, and the machine enforces it when it is loaded or asked to move.

### Types

- `RunMachine.approval_ttl_s: float | None`. `None` means no deadline. A negative value is `ValueError`.
- `RunMachine.wait_until: float | None`. Unix time (UTC seconds). `None` when no deadline is armed.
- `RunConfig.approval_ttl_s: float | None = 86400.0`. New harness runs pass this into a new `RunMachine`. A loaded snapshot keeps the value it stored.
- New transition `(gated, "expire") → done`. Outcome `expired`. Not `blocked`.

### When it arms

`submit_decision(..., require_human=True)` sets `wait_until = now + approval_ttl_s` when `approval_ttl_s` is not `None` and `wait_until` is not already set. A second submit does not move the deadline. `require_human=False` clears `wait_until`. `now` is keyword-only on `submit_decision`, `approve`, `close`, `expire_if_due`, `perform_effect`, `commit_effect`, and `fail_effect`. The default is `time.time()`.

`None` is the off switch for a caller who sets it, and the value of a snapshot that predates the field. The harness default is 86400, which is the decision. `NaN`, infinities, and booleans are `ValueError` on `RunConfig` and on `from_dict`.

Bare `RunMachine()` keeps `approval_ttl_s=None`, so existing tests that approve immediately do not grow a deadline. `RunConfig` defaults to 24 hours, so a harness run that leaves a human decision in `gated` will expire.

The three agents still `close` or `complete` in the same call. A same-call close happens before the deadline, so exit codes do not change. The timer matters for a machine that is saved in `gated` and touched later.

### When it fires

`expire_if_due(*, now=None) -> bool`. If the phase is `gated`, `wait_until` is set, and `now >= wait_until`, set outcome `expired`, transition `expire`, return True. Otherwise return False.

Called at the start of `approve`, `close`, `submit_decision`, `perform_effect`, `commit_effect`, and `fail_effect`.

- `approve` after the deadline expires the run and raises `TransitionError`. No `Approval` is recorded.
- `close` after the deadline expires the run and returns. Outcome is `expired`, not `blocked`.
- A mutating effect after the deadline is refused, because the phase is `done`.

`AgentHarness.load_lifecycle` and `_begin_machine` call `expire_if_due` and save when it returns True. `_begin_machine` then refuses to resume, with the reason that the approval wait elapsed. `LifecycleStore.load` stays a pure read so a test can inspect a snapshot before the check.

`to_dict` / `from_dict` round-trip both fields. A snapshot that lacks them loads as `None` and does not expire.

### Shared state

The deadline lives on the one `RunMachine`. The caller thread that loads or approves is the only writer, same as every other lifecycle transition. No timer thread, no second lock. Two processes that hit the same file can race, as they already can on `LifecycleStore.save`. Both outcomes of that race are `expired` or a single `approve` that landed first; this step does not add a lock.

### Decisions recorded, not built

- README degraded-mode section: HOLD is the deploy-risk outage default. Delete the open question. No fallback.
- `plans/step-4.md`: the store counter stays a rejected design. The audit-log cap remains opt-in.
- `docs/always-on-agents.md`: durable timers are partial. This wait-until is what exists. An inbox is still missing. Nothing wakes a process by itself.
- `DECISIONS.md` records all three answers.

## Tests (no sleeps, `now=` is the clock)

1. `require_human=True` and `approval_ttl_s=60` at `now=1000` sets `wait_until` to 1060.
2. `approve` at `now=1059` records the actor.
3. `approve` at `now=1060` leaves phase `done`, outcome `expired`, `approval is None`, and raises `TransitionError`.
4. `close` at `now=1060` leaves outcome `expired`.
5. `require_human=False` leaves `wait_until` None. `approval_ttl_s=None` with `require_human=True` leaves `wait_until` None.
6. A snapshot with no `wait_until` loads and stays `gated`.
7. Harness `load_lifecycle` of a past deadline saves phase `done` / outcome `expired`.
8. `perform_effect(..., mutating=True)` at the deadline raises, `effects` stays empty, phase is `done`, outcome is `expired`, and history contains `expire`.
9. `RunConfig` defaults `approval_ttl_s` to 86400 and rejects a negative, a `NaN`, and an infinity.
10. A second `submit_decision` before the deadline does not change `wait_until`.
11. `to_dict` / `from_dict` round-trips both fields. `from_dict` rejects a non-finite `wait_until`.
12. `run(run_id=...)` on a due gated snapshot makes no model call, saves `expired`, and raises with the elapsed-wait reason.

## Review disposition

`scripts/plan-review.sh` exited 2 (no reviewer). A different model reviewed the plan.

- Two processes can overwrite approve and expire (HIGH): disagree for this step. That race is every lifecycle save today. The caller thread remains the only writer. A new lock is a different change.
- `None` never expires (HIGH): the harness default is 86400. `None` stays an explicit off switch and the legacy-snapshot value.
- Resubmit extends the deadline (HIGH): agree. Arm once.
- Non-finite numbers (MEDIUM): agree. Reject them.
- `now` missing on `approve` (MEDIUM): agree. Keyword-only `now` on every method that checks.
- Mutating-effect test passes without expiry (MEDIUM): agree. Assert `expired` and the history event.
- `_begin_machine` not tested (MEDIUM): agree. Test `run(run_id=...)`.
- Round-trip (LOW): agree. Add it.

Code review (a different model, after the implementation):

- `commit_effect` returned before `expire_if_due` when the key was already recorded (MEDIUM): agree. Expire first. A repeat commit of a due wait still finishes as `expired`.
- `now + approval_ttl_s` can be infinity (LOW): agree. `finite_seconds` checks the sum before the decision is stored.
- `plans/step-4.md` still called the three answers open (LOW): agree. Those lines now say answered 2026-11-17.

## Files

- harness/lifecycle.py
- harness/agent.py
- tests/test_approval_ttl.py
- docs/always-on-agents.md
- README.md
- DECISIONS.md
- CHANGELOG.md
- plans/step-4.md
