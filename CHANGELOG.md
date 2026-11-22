# Changelog

Format follows Keep a Changelog. Dates are UTC.

## [Unreleased] - 2026-11-16

- Dependabot cooldown: `default-days: 7` for `pip` and `github-actions`, so version updates wait a week after release. Security updates are not delayed. Text test in `tests/test_dependabot_cooldown.py`. Implemented on the proposed default, pending owner confirmation.
- `evals/baseline.json` records its `model` (`offline` for the checked-in floors) and harness `settings`. Live eval payloads carry both. `check_baseline` and `run_eval` warn when the run model differs from the baseline model, and fail only under `PAIRING_STRICT=1` (`run_eval` with `--check-baseline`). A baseline or summary with no model only warns. Implemented on the stated default, pending owner confirmation.
- DECISIONS caveat: the honeypot scope cases (`02-honeypot-exfil-tool`, `04-shell-honeypot`) are weak evidence if a model ever sees them, because the targets look artificial. A realistic case is suggested, not added. Circuit-breaker report and note are in `plans/step-6.md` only; nothing built.
- Design note `docs/effect-reversibility.md`: four effect classes (reversible, compensable, delayable, irreversible), the undo record on the lifecycle machine, and the fake effect scenarios under each class. Not built; two open questions recorded.
- A gated human approval expires. `RunConfig.approval_ttl_s` defaults to 24 hours and `submit_decision` arms `wait_until` once. The next load or approve after the deadline finishes the run as `expired`. No thread sleeps. HOLD stays the deploy-risk outage result. The cross-run cap stays the opt-in audit log; the store counter is not built.
- Always-on map in `docs/always-on-agents.md`: the event inbox is missing; a gated human approval has a wait-until and nothing wakes a process; lifecycle snapshots are durable run state, not agent memory; `allowed_models` and scope are the permission system; idempotency keys are the nearest thing to an outbox and do not survive a new `run_id`.
- Degraded-mode tables in the README for pr-reviewer, deploy-risk, and runbook-gen (model unavailable, rate limited, over budget, bad JSON), with one offline test each for the result and the exit code. `runbook_exit_code` is what the runbook CLI passes to `sys.exit`. HOLD is the deploy-risk outage result. There is no rules-only fallback.
- Design note (`plans/step-4.md`): a store-backed UTC-day cap that would leave a not-yet-started run in `planned` under one file lock. Not implemented. The decision is to keep the opt-in audit-log refusal. `max_cost_usd_per_day` stays off in the shipped workflows.

- Prefilter and injection scanner cover the full history: system prompt, every prior turn (any role, str or block content) and the new message. A marker in any of them forces the model call. The scanner normalizes text (NFKC, casefold, zero-width characters removed, whitespace collapsed) before matching, canaries included.
- Held-out is set by the harness only (`RunConfig.held_out`, written as top-level `held_out` on every audit row). `mine_failures` ignores `data.held_out` and `data.split`. A `baseline.json` `per_case` entry marked `held_out` with no scored row fails. `--sweep-effort` scores held-out cases once at the chosen effort and reports regressions (exit 1 with `--check-baseline`) without tuning on them.
- `RunConfig.allowed_models` is checked at construction and at call time (`_call_api`, before the dry-run branch and before any client is built). `AgentResult.model_ran` and the audit field `model_ran` record the model that actually answered (null for a prefilter skip, a dry run, or a call that did not complete); `model` stays the configured value.
- Test double `tests/fake_effects.py` models DUPLICATE (the outside system already holds the key, so the effect does not run again) and LATENT (committed, not visible until `reconcile`) alongside success, timeout after commit, and partial. Idempotency stays on the lifecycle machine.
- Poisoned-memory scope case (`evals/cases_scope/05-poisoned-memory.json`) depends on its `memory` field: `memory_actions` join the proposed actions only when `memory` is set.
- With `--json`, `evals.run_eval` (single run and `--sweep-effort`) and `evals.run_policy_eval` write only JSON to stdout; regression text goes to stderr. `main(argv)` added to both.
- A prefilter-skipped run is gated on the lower of the decision's and the payload's confidence; a payload with no `confidence` still counts as 0.0.
- Injection and scope evals report a rate per category. Scope cases gained a `category` field. PR-reviewer cases already had `expected_category`.
- Held-out eval split (`"split": "held_out"`), with one case (`14-held-out-auth-bypass`) and a `per_case` entry in `baseline.json`. Effort sweeps and `mine_failures` ignore held-out cases for tuning and mining; `baseline.json` `per_case` is compared by `case_regressions`.
- Optional prefilter (`RunConfig.prefilter` and `prefilter_skip_threshold`) can skip the model when its confidence is at or above the threshold. Off unless both are set. A prefilter exception fails the run without calling the model. Audit rows include `llm_skipped`.
- `RunConfig.allowed_models`, when set, rejects a model id that is not on the list (an empty list rejects every model). Audit rows include `model`.
- Audit rows include `agent_scope` (caller, intent, allowed tools). `allowed_tools` null means the scope was not set; `[]` means no tools are allowed. Rows written before this field still read.
- Mine recurring audit failures into candidate cases (`python -m evals.mine_failures`). Candidates stay out of `evals/cases/` and the baseline. Schema errors and refused PR comments are marked on the final audit row.
- Daily budget rollup counts the last audit row for each `run_id`, so a run audited more than once is not double-counted.
- Pairing-kit scripts installed: `scripts/check.sh` (validation gate), `scripts/plan-review.sh` and `scripts/cross-review.sh` (send a plan or diff to another AI provider after a gitleaks scan; fail closed), helper checks, and `.claude/commands/`. `plans/step-*.md`, `REQUIREMENTS.md` and `DECISIONS.md` record the review fixes.
- Five assertion-free tests now assert behavior.

## 2026-11-02

- Offline scope-adherence eval (`evals/cases_scope/`): allowed tools/targets vs honeypot proposals; `scope_hold_rate` floor in `evals/baseline.json` and `--check-baseline`.
- SECURITY.md: sandboxing is not a boundary on its own — document harness gates/lifecycle/audit outside the sandbox; read-only creds and per-run sandboxes called out as operator-owned (not in this repo).
- Audit rows always include `caller_identity` (`RunConfig` / `AGENT_CALLER` or `GITHUB_ACTOR`); `approver` is promoted from `lifecycle.approval.actor` when present.
- Persist/resume `RunMachine` via `LifecycleStore` (`AGENT_LIFECYCLE_DIR` / `lifecycle_dir`).
- Checked-in `evals/baseline.json` floors; `--check-baseline` and offline CI checks.
- Refuse PR comments that replicate injection canaries / override phrases.
- Daily process budget `max_cost_usd_per_day` from audit-log rollup (`AGENT_MAX_COST_USD_PER_DAY`).
- Offline deploy-risk and runbook-gen policy evals (`evals/run_policy_eval.py`).
- Typed evidence schemas at the gated boundary (`harness/schemas.py`).
- Multi-turn `prior_messages` plus optional Anthropic message-threads beta flag.
- `harness/effort_defaults.json`, coverage floor 70% in pytest, Jaeger demo marked local-only.
- Prompt-injection eval fixtures for the PR reviewer (planted defect + "approve / ignore findings" instructions, including a self-replicating canary). Scorer tracks injection hold rate and canary replication; the gate must still block.
- `RunConfig.reasoning_effort` (`low`/`medium`/`high`/`xhigh`/`max`) passed as Anthropic `output_config.effort`. Per-agent defaults and `python -m evals.run_eval --sweep-effort` to pick the cheapest level that holds quality.
- Run lifecycle (`planned`, `running`, `gated`, `approved`, `failed-retryable`, `done`) owns approvals, model retries, side-effect idempotency, and completion criteria. Prompts report evidence; they no longer assign DEPLOY / HOLD / BLOCK or declare the run finished.
- `DEPLOY_OVERRIDE` is an approval transition and requires `DEPLOY_APPROVER` or `GITHUB_ACTOR`. A failed run cannot be overridden.
- Fail-closed API calls, pre-call worst-case cost ceiling, `dry_run` that never passes the gate
- Default model `claude-sonnet-5-5` with configurable pricing
- PR reviewer eval suite and observability demo
- CI on Python 3.10, 3.12, 3.13 (ruff, mypy, pytest)
