# Changelog

Format follows Keep a Changelog. Dates are UTC.

## [Unreleased] - 2026-11-02

- Held-out eval split (`"split": "held_out"`). Effort sweeps and `mine_failures` ignore those cases. `baseline.json` `per_case` is compared by `case_regressions`.
- Optional prefilter (`RunConfig.prefilter` and `prefilter_skip_threshold`) can skip the model when confidence is high enough. An injection-scanner hit on the system prompt or user message always calls the model. Audit rows include `llm_skipped`.
- Mine recurring audit failures into candidate cases (`python -m evals.mine_failures`). Candidates stay out of `evals/cases/` and the baseline. Schema errors and refused PR comments are marked on the final audit row.
- Audit rows include `agent_scope` (caller, intent, allowed tools). `allowed_tools` null means the scope was not set; `[]` means no tools are allowed. Rows written before this field still read.
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
