# Always-on agents and this harness

Anthropic describes five primitives an always-on agent needs: an event inbox, durable timers, persistent memory, a permission system, and an outbound outbox ("Building always-on agents", 2026-11-16, https://claude.com/resources/articles/building-always-on-agents). kagent 1.0 suspends idle agent actors and restores them on demand, which is the same durable-timer problem at cluster scale (https://www.solo.io/blog/announcing-kagent-1-0-ga, 2026-11-16).

This note maps each primitive onto the code in this repo. "Has" means the code does the job, and the citation is the symbol that does it. A partial match says what is missing in the same paragraph.

| Primitive | This repo |
|-----------|-----------|
| Event inbox | Missing |
| Durable timers | Missing |
| Persistent memory | Partial: durable state for one run, not memory the agent reads on wake |
| Permission system | Has, with the limits below |
| Outbound outbox | No outbox. Idempotency keys on one run are the nearest thing |

## Event inbox — missing

Nothing in this process receives an event. There is no queue, no subscriber, and no HTTP server.

`agents/runbook_gen.py` says it is designed to run from a PagerDuty or Rootly webhook. The code that ships reads a JSON file or stdin when someone starts the CLI. `agents/pr_reviewer.py` `fetch_pr_diff` and `agents/deploy_risk.py` `assess_deploy_risk` run only when a caller invokes them. A GitHub Action or a human is the inbox, and it lives outside this repo.

`tests/fake_effects.py` `FakeEffectProvider._queue` is a script of test scenarios. It is not an event inbox.

## Durable timers — missing

Nothing wakes a run later.

`harness/lifecycle.py` `RunMachine._entry` stores a UTC timestamp on a transition that has already happened. No code reads that timestamp to fire a follow-up. `RunMachine.approve` and `RunMachine.close` run only when a caller invokes them. A run that is `gated` and waiting on a person stays there until that call. There is no wait-until, no deadline, and no scheduler.

`examples/observability/demo.py` calls `time.sleep(6)` so a local OpenTelemetry batch can flush. That sleep is not saved, and it is not a lifecycle timer.

`harness/agent.py` `AgentHarness._begin_machine` resumes `planned`. It also loads `failed-retryable`, and for a model-call retry it calls `retry()` immediately. A side-effect retry loaded the same way raises `TransitionError`, because `retry()` then requires the idempotency key and `_begin_machine` does not pass one. Inside `AgentHarness.run`, a model failure that stops in `failed-retryable` is retried immediately. None of these is a "wait until" state.

## Persistent memory — partial

The harness remembers the lifecycle of one run. It does not have a memory the next wake reads back.

Has:

- `harness/store.py` `LifecycleStore` writes one JSON file per `run_id`. `save` writes a temp file and `os.replace`s it. It does not `fsync` the file or the directory, so a power loss can drop the snapshot after the replace. `load` rebuilds a `RunMachine` with `RunMachine.from_dict`.
- `harness/agent.py` `AgentHarness.save_lifecycle` / `load_lifecycle` use that store when `RunConfig.lifecycle_dir` (or `AGENT_LIFECYCLE_DIR`) is set. `_begin_machine` resumes `planned`, and it resumes `failed-retryable` only when that retry does not need an idempotency key (a model-call retry). A side-effect failure that is still under `max_effect_attempts` sets `_retry_to` to `approved` and enters `failed-retryable`. Loading that snapshot calls `retry()` with no key, and `RunMachine.retry` raises `TransitionError`. `note_effect_failure` saves that snapshot; nothing in this repo can resume it.
- `AgentHarness.audit` appends a JSONL row (the audit log). The next prompt does not read it. `harness/budget.py` `sum_audit_cost` reads it to add up `cost_usd`.

Does not have:

- `RunConfig` `prior_messages` is whatever the caller passes in. The harness does not store the transcript.
- `use_message_threads` and `thread_id` add an Anthropic beta header inside `_call_api`. The thread is not stored here.
- `evals/cases_scope/` includes a poisoned-memory fixture. That is an eval case, not a memory store.

There is no API that loads an arbitrary note on the next run.

## Permission system — has, with limits

Has:

- Model allowlist. `harness/agent.py` `check_model_allowed` runs in `RunConfig.__post_init__` and again at the top of `AgentHarness._call_api`, before the dry-run branch and before any client is built. `allowed_models` unset means no allowlist. An empty list rejects every model.
- Scope on the audit row. `harness/schemas.py` `AgentScope` records `caller`, `intent`, and `allowed_tools` from `AgentHarness.audit`. `allowed_tools` null means the caller set no scope. `[]` means no tools are allowed.
- Who may approve. `harness/lifecycle.py` `_validate_actor` rejects an actor whose name is `model`, `agent`, `assistant`, `system`, or `llm`. `RunMachine.approve` rejects `policy:` when `require_human` is set. `CompletionCriteria.refusal` rejects completion that requires a person when the actor is missing, is `policy:`, or is a model actor.
- Deploy override. `agents/deploy_risk.py` `_person` ignores `DEPLOY_OVERRIDE` unless `DEPLOY_APPROVER` or `GITHUB_ACTOR` names a person. `settle_deploy` does not apply an override to a failed run.
- Injection on the way out. `harness/injection.py` `refuse_if_replicated` runs in `review_pr` before `post_pr_comment`.

Limits, stated so the "has" is not wider than the code:

- The harness does not run tools. `allowed_tools` is recorded and not enforced on a live run. `harness/scope.py` `filter_in_scope` is what the offline scope eval uses to drop out-of-scope proposals.
- A model may still return any JSON. `settle_review` drops `gate_decision` from the payload, and `settle_deploy` drops `recommendation`, before the policy assigns one. That is the gate refusing to trust the model, not a permission check on a tool call.

## Outbound outbox — the nearest thing, not an outbox

Has, on a single run:

- `harness/lifecycle.py` `RunMachine.perform_effect` returns false when this machine already has the same idempotency key for that effect name, and raises when the name was committed under a different key. `commit_effect` records the key; a second commit of the same key returns without writing again. `fail_effect` is only from `approved`, and it stops after `max_effect_attempts`.
- Mutating effects (`mutating=True`) are legal only from `approved`. Delivery of an already-decided result (`mutating=False`) is legal from `gated`, `approved`, or `done`.
- `agents/pr_reviewer.py` `review_pr` posts `pr-comment` with key `repo#pr` when `perform_effect` returns true.
- `agents/runbook_gen.py` writes `write-runbook` with key `incident_id` when the run is `approved`.

That is not an outbox.

- The key is on that `RunMachine` only. `review_diff` and `generate_runbook` call `AgentHarness.run` without a `run_id`, so each invocation mints a new id and a new empty `effects` map. A second process posts or writes again for the same pull request or the same incident.
- `post_pr_comment` JSON-encodes `{"body": body}` and does not send the idempotency key. GitHub's comment API creates another comment. If the process dies after the POST returns and before `commit_effect`, `perform_effect` on that same machine still returns true, because the key is stored only by `commit_effect`.
- The snapshot is saved too early to remember the key. `review_diff` calls `save_lifecycle` after `settle_review` and before `review_pr` posts. `review_pr` does not save after `commit_effect`. The runbook CLI saves after `settle_runbook` and does not save after a successful `commit_effect`. `note_effect_failure` does save, and it runs on `OSError`, not on success. A later process cannot load the key. `_begin_machine` would also refuse to resume a run that had already left `planned` / `failed-retryable`.
- `tests/fake_effects.py` `EffectScenario.DUPLICATE` means the outside system already holds the key, so the effect does not run again. The GitHub poster and the runbook file write do not ask an outside system. They only ask the in-memory machine.

There is no durable queue of outbound work for a later drainer to send.

## Question

Should an approval that sits too long expire (a lifecycle wait-until timer), or is that out of scope?
