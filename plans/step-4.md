# Step 4 — always-on map, degraded-mode contract, daily cap note

Three items from the 2026-11-16 request. Items 1 and 3 are documents and a question. Item 2 locks the fail-closed contract that already exists. Nothing in this step adds an inbox, a timer, a rules-only fallback, or a new spend counter.

Review of this plan (another model; `scripts/plan-review.sh` had no provider configured) is folded in below. Disposition is at the end.

## Item 1 — docs only

Add `docs/always-on-agents.md`. Map the five primitives Anthropic names for an always-on agent. Cite a file and a symbol for every "has" claim. Say the gap in the same section when the mapping is partial. Do not add code.

Checked against the tree (no listener, no scheduler, no `asyncio`, no `threading`):

| Primitive | Verdict | What the doc will cite |
|-----------|---------|------------------------|
| Event inbox | Missing | No queue and no server. `agents/runbook_gen.py` module docstring says a PagerDuty or Rootly webhook; the CLI reads a JSON file or stdin. `fetch_pr_diff` / `assess_deploy_risk` run only when a caller starts them. `FakeEffectProvider._queue` is a test script, not an inbox. |
| Durable timers | Missing | `RunMachine._entry` writes a UTC timestamp on a transition that already happened. Nothing reads it to fire a later event. `approve` and `close` run only when a caller invokes them, so a `gated` approval can sit forever. `examples/observability/demo.py` `time.sleep(6)` flushes a local OTel batch; it is not durable and not a lifecycle timer. |
| Persistent memory | Partial | Has durable run state: `LifecycleStore` (one JSON file per `run_id`, atomic replace) and `AgentHarness.load_lifecycle` / `_begin_machine` (resume only from `planned` or `failed-retryable`). Has an append-only audit JSONL (`AgentHarness.audit`) that the next prompt does not read; `sum_audit_cost` reads it for money only. `prior_messages` is passed by the caller. `use_message_threads` / `thread_id` set an Anthropic beta header and are not stored here. The poisoned-memory eval is a fixture (`evals/cases_scope/`), not a memory store. No API loads arbitrary memory on wake. |
| Permission system | Has, with a stated limit | `check_model_allowed` in `RunConfig.__post_init__` and again at the top of `AgentHarness._call_api` (before dry-run and before any client). Empty allowlist rejects every model; unset means no allowlist. `AgentScope` is written on every audit row (`caller`, `intent`, `allowed_tools`). The harness does not run tools: `allowed_tools` is recorded only. `harness/scope.py` `filter_in_scope` is what the offline scope eval enforces. `_validate_actor` refuses a model actor. `require_human` refuses `policy:` for HOLD. `CompletionCriteria.require_human` refuses completion without a person. `deploy_risk._person` refuses `DEPLOY_OVERRIDE` from the model or from policy. `refuse_if_replicated` runs before a PR comment is posted. |
| Outbound outbox | Nearest thing, not an outbox | `perform_effect` / `commit_effect` / `fail_effect` key effects on one `RunMachine`. Same key already landed on that machine → do not run again. A different key for the same name is refused. Mutating effects only from `approved`. `review_pr` posts `pr-comment` under `repo#pr`. The runbook CLI writes `write-runbook` under `incident_id`. |

Outbox limits the doc will state, because the key is not an outbox:

- The key lives on that run's machine. `review_diff` and `generate_runbook` mint a new `run_id` every call, so a second process has an empty `effects` map and will post or write again.
- `post_pr_comment` sends `{"body": ...}` and no idempotency key. GitHub creates another comment. The same is true of a crash after the HTTP call returns and before `commit_effect`: `perform_effect` still returns true, because the key is recorded only in `commit_effect`.
- `review_diff` saves the snapshot before `review_pr` posts, and `review_pr` does not save after `commit_effect`. The runbook CLI saves after `settle_runbook` and does not save after a successful `commit_effect` (`note_effect_failure` does save on `OSError`). A later process cannot see the key.
- `tests/fake_effects.py` DUPLICATE means the outside system already holds the key. The GitHub poster and the runbook write do not consult an outside system; they only consult the machine.

The doc ends with one question, unanswered: should an approval that sits too long expire (a lifecycle wait-until timer), or is that out of scope?

## Item 2 — degraded-mode contract

The four conditions, probed against the current agents (fake client, no network):

- Unavailable: exception type `APIConnectionError`. Retryable. Default `max_attempts` is 2, then the run is `done` / `failed`, `success` false, `gate_passed` false, `confidence` 0.0, `data` empty, `effects` empty.
- Rate limited: exception type `RateLimitError` (status 429). Same end state as unavailable. The tests stay separate and each checks its own reason string.
- Over budget: the per-run worst-case ceiling (`max_cost_usd`: pr-reviewer 0.30, deploy-risk 0.20, runbook-generator 0.50). The call is not made (`calls == 0`, `model_attempt == 1`). Same failed end state. This is the always-on cap. `max_cost_usd_per_day`, when set, raises `BudgetExceeded`, which is not retried, and reaches the same exit. The post-call actual-cost check is different: the model already answered and the run is `gated` with `gate_passed` false. The table says that in one sentence. The tested row is the pre-call ceiling.
- Bad JSON: body `not json`. `ModelOutputError` is retryable, so two calls, then the same failed end state. Reason contains `Expecting value`.

What downstream sees after that end state:

| Agent | Downstream | Exit |
|-------|------------|------|
| pr-reviewer | `review_exit_code` is 3 (not BLOCK/2, not REQUEST_CHANGES/1). `data` has no `gate_decision`. `format_github_comment` says the gate failed and manual review is required. Phase `done` still allows a non-mutating `pr-comment`, so a run that has a GitHub token posts that failure comment. `review_diff` itself does not post. | 3 |
| deploy-risk | `settle_deploy` returns recommendation HOLD, exit 1, because `success` is false. `result.data` is not given a recommendation. `format_deploy_output` says the agent failed and is defaulting to HOLD. | 1 |
| runbook-gen (`agent_name` `runbook-generator`) | `settle_runbook` returns without deciding, because the phase is not `gated`. `render_markdown` is `# Runbook generation failed` plus the reason. `runbook_exit_code` is 1. A mutating `write-runbook` is legal only from `approved`, so the failed run cannot reserve the write. | 1 |

README: the fail-safe bullet links to a `## Degraded mode` section that holds a table per agent, the retry note, the post-call cost note, and the daily-cap note. The bullet stops saying every failure is BLOCK or HOLD, because pr-reviewer exits 3 with no gate decision.

Question at the end of that section, unanswered: is HOLD on a model outage the intended default for deploy-risk, or do you want an optional rules-only fallback? The fallback is not built.

### Tests

`tests/test_degraded_mode.py`. Twelve tests: one per agent per condition. Offline. No sleep, no socket, no wait. Each test asserts an outcome a caller sees, in the test body.

Shared fixture: in-process fake of `anthropic.Anthropic`. A mode raises `APIConnectionError("connection failed")`, raises `RateLimitError("rate limited")`, or returns the text `not json`. Reset before every test. `ANTHROPIC_API_KEY=test-key` so a missing key does not masquerade as an outage. `AGENT_MAX_COST_USD_PER_DAY`, `AGENT_AUDIT_LOG`, `AGENT_LIFECYCLE_DIR`, and `DEPLOY_OVERRIDE` unset. The key string is not a real credential.

- pr-reviewer: `review_diff` on a short diff, then `review_exit_code` and `format_github_comment`. Assert exit 3, empty `data`, `success` false, `gate_passed` false, phase `done`, outcome `failed`, and the manual-review comment. Also assert `perform_effect("pr-comment", ..., mutating=False)` is true from `done` and that this call does not record an effect. Unavailable and rate-limit: `model_attempt == 2` and two client calls. Bad JSON: two calls, `Expecting value` in the gate reason. Over budget: `monkeypatch.setitem` on `MODEL_PRICES["claude-sonnet-5-5"]` to a huge pair so the real ceiling in `_call_api` exceeds the agent's own `max_cost_usd`; zero client calls; `call not made` in the reason.
- deploy-risk: `assess_deploy_risk` then `settle_deploy(..., allow_caution=False)` and `format_deploy_output`. Assert HOLD, exit 1, the "defaulting to HOLD" line, empty `data` (no recommendation key), failed lifecycle, and the same attempt/call counts.
- runbook-gen: `generate_runbook`, `settle_runbook`, `render_markdown`, `runbook_exit_code`. Assert exit 1, the failure heading and the reason, phase not `approved`, and `perform_effect("write-runbook", id, mutating=True)` raises `TransitionError`. Same attempt/call counts.

`runbook_exit_code(result) -> int` is new. It returns 0 only when the lifecycle is `done` with outcome `completed`, and 1 otherwise. The branch that skips the file write stays `phase != approved` and still `sys.exit(1)` (a completed run is not `approved`, so calling the helper there would turn that guard into exit 0). The helper is what `sys.exit` gets for the empty `incident_id`, the `OSError` path, and the exit after `finish_runbook`. The write path is not moved. No policy change. For a failed model call the helper returns 1, which is the same code that guard exits with.

These twelve tests are characterization tests of the contract this request asks to lock. pr-reviewer and deploy-risk already behave this way, so those tests pass on the first run. The runbook tests fail until `runbook_exit_code` exists. That is the red phase. Production gates are not broken on purpose to manufacture a failure. The request to lock the current contract, and the instruction to continue after review, is the exception to a red-then-green cycle for behavior that already ships.

A failing assertion on pr-reviewer or deploy-risk means the table is wrong, and the table is fixed to match the code.

### Changelog entries

Under `[Unreleased]`, date 2026-11-16:

- Always-on map in `docs/always-on-agents.md`. Inbox and durable timers are missing. Question left open: should a gated approval expire?
- Degraded-mode tables and offline tests for pr-reviewer, deploy-risk, and runbook-gen. Question left open: is HOLD the deploy-risk outage default?
- Design note only, in this file. Not implemented. `max_cost_usd_per_day` already refuses the next call from the audit log and fails the run.

## Item 3 — design note, nothing built

`max_cost_usd` is per run (`RunConfig.max_cost_usd`, ceiling in `_call_api`, actual in `_evaluate_gates`). It does not look at other runs.

The only cross-run mechanism in code is `max_cost_usd_per_day` (`AGENT_MAX_COST_USD_PER_DAY`). `check_daily_budget` / `sum_audit_cost` roll up `cost_usd` from the audit JSONL for that agent and that UTC calendar day; the last row for each `run_id` counts. The check runs in `_call_api` before a client is built. `BudgetExceeded` is not retried, and the run ends `done` / `failed`. It does not pause. It does nothing when the cap or the audit path is unset. `AgentHarness.audit` appends with no lock, so two processes can both pass and both spend. The counter is not in `LifecycleStore`. Deleting or omitting the log resets the cap to zero.

It is off in the shipped workflows. `.github/workflows/pr_review.yml` and `deploy_gate.yml` do not set `AGENT_MAX_COST_USD_PER_DAY` or `AGENT_LIFECYCLE_DIR`. Both set `AGENT_AUDIT_LOG` to a `/tmp` file on a short-lived runner. Nothing in that configuration caps spend across runs, and a counter kept in the lifecycle directory would also vanish with the runner unless the operator points both paths at durable shared storage.

### Rolling daily cap (not implemented)

File `spend-daily.json` in the lifecycle directory:

```json
{
  "day": "YYYY-MM-DD",
  "runs": {
    "<agent_name>|<run_id>": {"actual_usd": 0.0, "reserved_usd": 0.0}
  }
}
```

`day` is the UTC calendar date, the same key as `budget._day_key`. This is not a sliding 24 hour window: a run at 23:59 and a run at 00:01 are different days, which is how the audit rollup already works. There is no second stored total. The cap reads `sum(actual_usd + reserved_usd)` under the lock. Per agent: sum only that agent's keys. A day field that is not today is replaced with an empty document for today (the old day does not carry). The file has to live on durable shared storage. A `/tmp` path, which is what the workflows use for the audit log, does not cap anything across jobs.

`actual_usd` is the sum of completed attempts' reported cost. A later attempt adds to it. It is never replaced by a later attempt. `reserved_usd` is only the in-flight attempt's worst case, or 0 when nothing is in flight.

Before an attempt, under the lock: if this run already has `reserved_usd > 0`, keep that reservation (a crash already reserved; do not add a second one). Otherwise set `reserved_usd` to this attempt's worst case. If `sum(actual + reserved)` including this reservation would pass the cap, write no reservation for this attempt and do not call the model. After the attempt returns, add that attempt's `cost_usd` to `actual_usd` and set `reserved_usd` to 0. A retry is another attempt: it sees the earlier actual and reserves its own worst case on top. A malformed body that still cost tokens stays in `actual_usd` when the next attempt reserves.

A crash after reserve and before reconcile leaves `reserved_usd` at the worst case. `_begin_machine` cannot resume `running`, so that reservation sits until the UTC day rolls and the document is replaced. That over-counts and fails closed. It does not reset the file to zero.

Owner of the counter: **one mutex**, an exclusive `fcntl.flock(LOCK_EX)` on a stable lock file `spend-daily.lock` that is never replaced. The process that holds the lock, on the same thread that called `AgentHarness.run` (the harness is synchronous), is the only code that reads or writes the JSON. It releases the lock in a `finally`, before any model call or other I/O. The JSON itself is written by a temp file and `os.replace` while the lock is held, same as `LifecycleStore.save`. The lock is not taken on the JSON file: `os.replace` changes the inode, so a lock on that inode would not cover the next writer. There is no writer task and no second lock. An in-process `threading.Lock` would not cover separate CI processes, and a background writer would be a second owner.

Corrupt JSON, or a document that is not this object, refuses the call. It is not rewritten as zero spend.

Pause applies only before `start`. `failed-retryable` is not a pause: `AgentHarness.run` retries a model failure in that phase immediately, and `_begin_machine` calls `retry()` with no idempotency key (a side-effect retry then raises `TransitionError`). On cap, before `start`, leave the machine in `planned`, record a history note, save it, and do not call the model. This invocation still returns `success` false and `gate_passed` false, so today's exit codes stay (pr-reviewer 3, deploy-risk HOLD/1, runbook 1). The snapshot stays `planned`, which is the only phase `_begin_machine` will later `start()` without an automatic retry. No process sleeps. Nothing in this repo passes `run_id` back in, and there is no timer, so that file is not resumed unless an operator calls `run(run_id=...)` later. Without that caller the invocation is a failed exit plus an orphaned `planned` snapshot. A run already in `running` whose next attempt would exceed the cap ends `done` / `failed`, the same as `BudgetExceeded` today, because there is no resumable pause from `running`. No new phase.

Not built. No new file, no lock, no change to `check_daily_budget`.

Question: the shipped workflows cap nothing across runs. The code can refuse the next call when `AGENT_MAX_COST_USD_PER_DAY` is set and the audit log is a durable file, and that refusal fails the run. Do you want the store-backed counter above (UTC calendar day, one file lock, a not-yet-started run left in `planned` for a caller who retries that `run_id`), or is the current opt-in refusal enough?

## Shared state

No new shared state in code. Tests do not share a harness. The fake client's class-level mode is reset by the fixture before each test. `MODEL_PRICES` is restored by `monkeypatch`. The design note's counter, if it is ever built, has one owner: the file lock above. This step does not create it.

## Assumptions

- "Model unavailable" is a retryable connection failure (`APIConnectionError`), not a missing API key and not a missing SDK. Those already have tests in `tests/test_harness.py` and are not given a second table row.
- "Over budget" in the table is the per-run pre-call ceiling, using each agent's own `max_cost_usd`. The daily cap is reported in the table as the same exit when it is configured, and is the subject of item 3.
- "runbook-gen" is the module `agents/runbook_gen.py`. The configured `agent_name` is `runbook-generator`.
- The approval-expiry question, the deploy-risk fallback question, and the store-cap question are asked and not answered here.
- No new dependency. `fcntl` is named only in the note.

## Not in this step

- An event inbox, a durable timer, or an approval expiry transition.
- A rules-only deploy fallback.
- A store-backed spend counter, a new lifecycle phase, or a change to budget enforcement.
- Moving the runbook write path into a new `main`.
- New packages.

## Tests (the list item 2 locks)

1. pr-reviewer, model unavailable → exit 3, gate-failed comment, failed lifecycle, effect not recorded, two calls.
2. pr-reviewer, rate limited → exit 3, same shape, reason `rate limited`, two calls.
3. pr-reviewer, over budget → exit 3, same shape, zero calls, `call not made`.
4. pr-reviewer, bad JSON → exit 3, same shape, `Expecting value`, two calls.
5. deploy-risk, model unavailable → HOLD, exit 1, "defaulting to HOLD", two calls.
6. deploy-risk, rate limited → HOLD, exit 1, reason `rate limited`, two calls.
7. deploy-risk, over budget → HOLD, exit 1, zero calls, `call not made`.
8. deploy-risk, bad JSON → HOLD, exit 1, `Expecting value`, two calls.
9. runbook-gen, model unavailable → exit 1, failure markdown, mutating write refused, two calls.
10. runbook-gen, rate limited → exit 1, reason `rate limited`, two calls.
11. runbook-gen, over budget → exit 1, zero calls, `call not made`, write refused.
12. runbook-gen, bad JSON → exit 1, `Expecting value`, two calls, write refused.

## Files

- docs/always-on-agents.md
- README.md
- CHANGELOG.md
- tests/test_degraded_mode.py
- agents/runbook_gen.py

## Review disposition

Plan review could not be sent with `scripts/plan-review.sh` (no reviewer configured, exit 2). A different model reviewed the plan against `REQUIREMENTS.md` and the code.

- Pause semantics (HIGH, WRONG): agree. `failed-retryable` auto-retries. The note now pauses only by staying in `planned` before `start`.
- Counter schema (HIGH, WRONG): agree. Per-run `actual_usd` plus a separate `reserved_usd`, so a retry cannot drop an earlier attempt's cost.
- Lock and crash (MEDIUM, MISSING): agree. Stable lock file, atomic replace of the JSON while the lock is held, corrupt JSON fails closed.
- Outbox limits (MEDIUM, MISSING): agree. The doc states that the key is per run, GitHub is not given the key, and the snapshot is not saved after a successful commit.
- Runbook success-path tests (MEDIUM, MISSING): disagree with moving `main` and adding those tests. The write path stays where it is. Only `runbook_exit_code` is added, and the twelve degraded tests call it. The success path is not rewritten, so it does not need a new characterization suite in this step.
- Test-first (MEDIUM, WRONG): agree that pr-reviewer and deploy-risk tests pass immediately. They are characterization tests of the contract this request says to lock. The runbook tests fail until `runbook_exit_code` exists. Gates are not broken to invent a red phase.
- CHANGELOG listed with no entry (LOW, SCOPE): disagree. The request says to update `CHANGELOG.md`. The three entries are specified above.

Code review (`scripts/cross-review.sh` had no provider; a different model read the diff):

- SDK retries inside one harness attempt (MEDIUM): agree. The README now says two harness attempts, and that `_call_api` does not pass `max_retries` or `timeout`. The client is unchanged.
- Runbook failure exit does not call the helper (MEDIUM): agree. The not-approved branch now exits with `runbook_exit_code`. `settle_runbook` does not complete a run, so that is 1.
- GitHub `urlopen` has no timeout (MEDIUM): agree the table overclaimed. The README now says a token reaches `post_pr_comment`, and that a GitHub error is not caught and is not exit 3. No new timeout in this step.
- Cap is off in the workflows (MEDIUM): agree. The note says so, and that `/tmp` does not survive the job.
- Effect-failure resume raises (LOW): agree. The always-on doc narrows resume to model-call retries.
- Calendar day, and nothing resumes `planned` (LOW): agree. The note says UTC calendar day, not a sliding 24 hours, and that no caller in this repo passes `run_id`.
- `gate_reason` on the PR comment (LOW): agree it is what downstream sees. The README says the comment includes `gate_reason`. The formatter is unchanged.
- `save` does not `fsync` (LOW): agree. The doc says atomic replace, not fsynced. No `fsync` added.
