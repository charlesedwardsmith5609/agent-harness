# Effect reversibility (design note, not built)

Status: proposal. Nothing in `harness/`, `agents/` or `tests/` changes. The source is an opinion essay arguing that agents need a reliable undo (Sagas, four tool classes, an undo log in the underlying system): https://quaspar.com/resources/agents-need-an-undo-button (TLDR IT, 2026-11-17). Treat it as a claim, not a finding. Line numbers below are from branch `improve/tldr-2026-11-17` and were checked against it.

## What an effect is today

- An effect has one property, `mutating: bool`. `perform_effect(name, key, *, mutating, now=None)` (`harness/lifecycle.py:276`) and `commit_effect(...)` (`:292`) take it. `fail_effect` (`:304`) has no `mutating` parameter and is legal only from `approved` (`:307`).
- A mutating effect is legal only from `approved`. A non-mutating one is legal from `gated`, `approved` or `done` (`_check_effect`, `:321-337`).
- `approved` can be reached by a policy actor (`policy:<rule>`, `_validate_actor`, `:122-132`) unless the decision set `require_human`, in which case policy is refused (`approve`, `:249-250`). So "mutating" today means "needs an approval", not "needs a person". Example: `settle_runbook` approves `write-runbook` as `policy:runbook-sections` (`agents/runbook_gen.py:239`), and the CLI then performs it with `mutating=True` (`:424-427`).
- The PR comment is posted with `mutating=False` (`agents/pr_reviewer.py:359-361`), so it is legal from `done`. GitHub keeps the comment. The harness cannot remove it.
- The machine stores only `effects: dict[name, key]` (`:148`, written at `:301`) and `effect_attempts` (`:149`, `:312`). Both are persisted by `to_dict` (`:398-399`) and `LifecycleStore.save` (`harness/store.py:30-38`, atomic replace). There is no undo record, no compensation, and no reverse transition in `_TRANSITIONS` (`:40-52`).
- The owner of `effects` is `RunMachine`: only its methods write it, and the caller thread that holds the machine is the only writer (`plans/step-5.md`, "Shared state"). Two processes on one snapshot file can still race on `save`; nothing locks it.
- `deploy-risk` performs no effect through the machine (`agents/deploy_risk.py` has no `perform_effect` call).

## Proposed four classes

Replace the bool, in a later step if approved, with `reversibility` (a `str` enum with four values). The class is not inferred from the bool: each call site is classified by hand, and a call that names no class is `irreversible`. Today `write-runbook` is `reversible` only if the undo record keeps a preimage (below), otherwise `irreversible`; `pr-comment` is `compensable` at best, never `reversible`, because the harness cannot delete the comment.

| Class | Meaning | Approval | Undo record |
|-------|---------|----------|-------------|
| `reversible` | The harness can undo it exactly, leaving nothing outside behind (a local file it owns; a draft). | May run from `gated` after a policy decision, without `approve()`. | Enough to restore the prior state exactly. For a file: the path plus a preimage (prior bytes, or a backup the lifecycle store owns and names), or the marker `absent` when the file did not exist, so undo deletes it. A path and a hash cannot restore overwritten content. |
| `compensable` | It cannot be undone, but a second effect cancels its business meaning (a retracting comment; a refund). | `approve()` by policy or a person. | The compensating effect and its own idempotency key, derived from the original (`<key>:compensate`). |
| `delayable` | Not run now. Recorded, then run later unless cancelled before its time (a scheduled deploy). | `approve()` before it is armed. Cancel is legal until it fires. | The pending intent itself: name, key, `not_before`. Cancel deletes it. |
| `irreversible` | Nothing undoes it (a production deploy, an email, a page). | `approve()` by a person (`require_human`); policy is refused. | None. The record says no undo exists. |

### Where the undo record lives, and who owns it

On the `RunMachine`, next to `effects`, as `undo: dict[name, dict]`. `LifecycleStore` saves it with the rest of the snapshot. It never goes into the model's prompt, `prior_messages` or `result.data`; the model cannot read or write it.

One writer: the lifecycle machine. Only `commit_effect` writes an undo record (in the same call that records the key), and only a new `compensate(name)` or `cancel(name)` event removes it. Everything else reads it through `RunMachine.to_dict()` or `LifecycleStore.load(run_id)`. No second lock is added. The cross-process `save` race above stays, and is named here.

## How the fake effect cases behave under each class

Scenarios are in `tests/fake_effects.py` (`EffectScenario`, `:11-15`; `apply`, `:58-83`).

| Scenario | reversible | compensable | delayable | irreversible |
|----------|------------|-------------|-----------|--------------|
| SUCCESS (runs once, visible) | Undo record written at commit. | Compensating key recorded. | The fake would record intent only; `executed[key]` stays 0 until the time passes. | Committed, no undo. |
| TIMEOUT_AFTER_COMMIT (commit, then raise; `:76-78`) | The machine already holds the key and the record, so undo is possible and a retry with the same key does not run again (`lifecycle.py:288-289`). | Same: the compensation is known. | Dispatched, so same as SUCCESS. | The caller sees an error for an effect that happened. This is the case that most needs a record, and here there is none. |
| PARTIAL (`fail_effect`, no commit; `:71-73`) | No key, so no record. The fake models no half-written outside state. | A real compensable effect would need a compensation even here; the fake cannot show it. | Not dispatched; nothing to cancel. | Failure is visible; no record either way. |
| DUPLICATE (outside system already holds the key; `:62-70`) | The machine commits without running, so the record is written without the harness having seen the original run. It may not match what the outside system holds. | Same caveat. | Same caveat. | Committed, no undo. |
| LATENT (commit, visible only after `reconcile`; `:47-54`, `:79-81`) | Undo must wait until the effect is visible, or it can run first and leave the effect in place. | Compensation also waits for visibility. | LATENT is not delayable: it is already dispatched and committed. Cancel would leave the outside action in place; it needs compensation. | Committed, no undo. |

A delayable effect has two states: pending intent (not dispatched; cancel is safe) and dispatched. Every scenario in today's fake is dispatched. The fake cannot model: pending intent, cancel before dispatch, half-written outside state for PARTIAL, or a compensating effect.

## Open questions (recorded, unanswered)

- B1-Q1. Build the four classes? Recommendation: not yet. Today there are two effects, both covered by the bool. Build when a real compensable or delayable effect appears.
- B1-Q2. If built, start with one flag on `perform_effect`? Recommendation: yes, a `reversibility=` keyword that defaults from `mutating`, so callers do not change. In that step `irreversible` requires `require_human`, and `delayable` waits for a scheduler.
