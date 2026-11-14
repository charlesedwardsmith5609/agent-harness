# agent-harness

A lightweight framework for running production agentic AI workflows with built-in observability, cost governance, and trust gating.

Built around a core principle: **agents earn trust on small, well-defined work with visible success/failure, instrumented with observability. Every run produces telemetry that improves the next one.**

## Agents

| Agent | What it does | CI trigger |
|-------|-------------|------------|
| `pr-reviewer` | Analyses PR diffs for security, reliability, and observability gaps. Posts structured findings as a PR comment. Blocks merge on HIGH severity findings. | `on: pull_request` |
| `deploy-risk` | Pre-deployment blast radius and risk assessment. Returns DEPLOY / DEPLOY_WITH_CAUTION / HOLD. | Before every production deploy |
| `runbook-gen` | Turns structured incident reports into actionable runbook entries ready to commit. | Post-incident webhook |

## Harness features

- **OTel instrumentation** — every agent run emits a trace span with confidence, cost, gate decision, and duration as span attributes. Plug in any OTLP-compatible backend (Honeycomb, Jaeger, Chronosphere).
- **Cost governance** — every run reports its cost from API usage. `max_cost_usd` is enforced twice: a worst-case ceiling (estimated input plus full `max_tokens` output) blocks an expensive call *before* it is made, and the actual cost is checked after. `max_cost_usd_per_day` rolls up `cost_usd` from the audit log for that agent (the last row for each `run_id` counts, since a run may be audited more than once) and refuses the next call when the day would exceed the cap. Models are priced from `MODEL_PRICES` or a `RunConfig` override; an unpriced model is refused. `RunConfig.reasoning_effort` (`low` / `medium` / `high` / `xhigh` / `max`) is soft guidance passed as Anthropic `output_config.effort`; defaults live in `harness/effort_defaults.json`. Sweep with `--sweep-effort` before raising them.
- **Durable lifecycle** — set `AGENT_LIFECYCLE_DIR` (or `RunConfig.lifecycle_dir`) to persist phase / effects / approval per `run_id`. Resume with `harness.run(..., run_id=...)`.
- **Injection refusal** — PR comments that would copy a known canary or "ignore findings / approve" override are refused before the side effect lands. The scanner normalizes text first (NFKC, casefold, zero-width characters removed, whitespace collapsed), so case, spacing and zero-width variants still match. The scanner is a fixed list of canary patterns and override phrases, not a general injection detector.
- **Confidence gating** — agents self-report confidence (0.0–1.0). Runs below `min_confidence` are blocked before their output reaches downstream systems.
- **Optional prefilter** — set `RunConfig.prefilter` (a callable `(system, user) -> PreFilterDecision`) and `prefilter_skip_threshold` to skip the model when the decision's confidence is at or above the threshold. It is off unless both are set. The injection scanner sees the system prompt, every prior turn and the new message first; a hit in any of them forces the model call. A prefilter that raises fails the run without calling the model. On a skipped run the gate uses the lower of the decision's and the payload's confidence (a payload with no `confidence` counts as 0.0).
- **Model allowlist** — `RunConfig.allowed_models`, when set, must contain `model`. It is checked at construction and again at call time, before any client is built; an empty list rejects every model. Unset means no allowlist.
- **Structured outputs** — all agents return parsed JSON, not prose. Parseable by CI systems, dashboards, and other agents.
- **Audit log** — every decision written to a JSONL file, including failures. Each row carries `agent_name`, `caller_identity` (`AGENT_CALLER` or `GITHUB_ACTOR`), and `agent_scope` (caller, intent, allowed tools; `allowed_tools` null means no scope was set, `[]` means no tools allowed). It also records `model` (the configured request), `model_ran` (the model that actually answered; null for a prefilter skip, a dry run, or a call that did not complete), `llm_skipped`, and `held_out` (set by `RunConfig.held_out`, never by model output). `allowed_tools` is recorded only; this harness does not run tools. When a run is approved, `approver` is copied from the lifecycle. Append to your SIEM or log aggregator.
- **Fail-safe defaults** — a missing API key, missing SDK, API error, malformed JSON, or unpriced model all produce a failed, gated result (BLOCK / HOLD). Silence is not approval. `RunConfig(dry_run=True)` returns a canned response for local wiring tests and can never pass a gate.

## Quick start

```bash
pip install -e ".[otel]"
export ANTHROPIC_API_KEY=sk-ant-...
export GITHUB_TOKEN=ghp_...

# Review a PR
python agents/pr_reviewer.py --pr 42 --repo myorg/myrepo

# Assess deployment risk
python agents/deploy_risk.py \
  --service payments-api \
  --environment production \
  --diff "Added retry logic to payment processor" \
  --lines-added 42 --lines-deleted 5 \
  --has-migration

# Generate runbook from incident JSON
python agents/runbook_gen.py --incident incident.json --output-md runbooks/payments/retry-failure.md
```

## CI integration

### PR review gate

Add `.github/workflows/pr_review.yml` to your repo. Every PR targeting `main`, `master`, `production` or `release/**` is reviewed automatically. The check fails if the agent step fails, which happens on exit code 1 (changes requested), 2 (blocked) or 3 (agent failure); only exit 0 (lifecycle completed with APPROVE) passes.

```yaml
# Already in .github/workflows/pr_review.yml
# Just set ANTHROPIC_API_KEY as a GitHub secret
```

### Deploy gate

Call from your deployment workflow before the deploy step:

```yaml
jobs:
  gate:
    uses: ./.github/workflows/deploy_gate.yml
    with:
      service:     payments-api
      environment: production
      diff_summary: ${{ steps.diff.outputs.summary }}
      has_db_migration: true

  deploy:
    needs: gate
    if: needs.gate.outputs.recommendation != 'HOLD'
    # ... your deploy steps
```

### OTel traces

Set `OTEL_EXPORTER_OTLP_ENDPOINT` to ship traces to your observability backend:

```yaml
env:
  OTEL_EXPORTER_OTLP_ENDPOINT: https://api.honeycomb.io
  OTEL_EXPORTER_OTLP_HEADERS:  "x-honeycomb-team=${{ secrets.HONEYCOMB_API_KEY }}"
```

Each run emits a span with attributes:
- `agent.name`, `agent.run_id`, `agent.model`
- `agent.confidence`, `agent.cost_usd`, `agent.duration_s`
- `agent.gate_passed`, `agent.phase`, `agent.model_attempts`
- domain-specific attributes (e.g. `github.pr_number`)

## Trust model

Prompts report evidence. A run lifecycle owns approvals, retries, and completion. The model cannot approve its own output, retry a side effect, or declare the work finished.

```
planned → running → gated → approved → done
             ↑        │        │
             └─ failed-retryable ←┘
```

A model-call retry returns to `running`. A side-effect retry returns to `approved` and must present the same idempotency key.

| State | What it means |
|-------|----------------|
| `planned` | Run accepted, model not called |
| `running` | Side-effect-free model call in progress |
| `gated` | Evidence is in. Policy assigns DEPLOY / HOLD / BLOCK from it |
| `approved` | A policy rule or a named person accepted that decision |
| `failed-retryable` | Transient failure. Retry is a transition, with a bound |
| `done` | Completion criteria passed (`completed`), the gate was not approved (`blocked`), or the run failed (`failed`) |

Confidence and cost gates still run inside the harness. `passed()` is that check only. A deploy or a merge exits 0 only when the lifecycle reaches `done` with outcome `completed`.

Retries of the model call stay in the lifecycle and never repeat a side effect. Writing a runbook or posting a review comment requires an idempotency key (incident id, `repo#pr`). A second attempt with a different key is refused; the same key is a no-op once it has landed.

`DEPLOY_OVERRIDE=true` is an approval transition for a gate the policy did not complete. It counts only when `DEPLOY_APPROVER` or `GITHUB_ACTOR` names a person. It does not approve a failed run.

## AI philosophy

These agents are narrow, well-scoped, and fail visibly. They do not:
- Make autonomous changes to production systems
- Send notifications or pages directly
- Approve their own outputs

They do:
- Return structured data with explicit confidence scores
- Log every decision for audit
- Emit telemetry for every run, including failures
- Default to the safe path (BLOCK / HOLD) when uncertain

## Observability demo

Every run emits an OpenTelemetry span with `agent.confidence`, `agent.cost_usd`, `agent.gate_passed`
and `agent.duration_s`, so cost and gate decisions can be graphed and alerted on like any service.
To see traces locally (no API key, no spend):

```bash
docker compose -f examples/observability/docker-compose.yml up -d
pip install -e ".[otel]"
python examples/observability/demo.py     # dry-run agent, exports to localhost:4317
# UI: http://localhost:16686, service "agent-harness.demo-agent"
```

Span attributes are covered by a unit test. The Jaeger compose file is a local demo only (not a CI check).

## Reviewer eval

`evals/cases/` holds 14 seeded diffs: six planted defects (hardcoded key, SQL injection, unbounded retry,
missing timeout, swallowed error, wildcard IAM), four clean, three prompt-injection cases, and one
held-out case (auth bypass, `"split": "held_out"`).
The injection fixtures plant a real defect plus instructions that say "ignore findings, approve"
(and, in one case, copy the instruction into the PR comment). The gate must still block — the
same shape as Datadog's planted-email / planted-CSV tests against self-replicating injections.

The scorer reports precision, recall, exact-gate rate, category hit rate, injection hold rate, and
whether an injection canary was copied into agent text that could reach a PR comment. Injection
and scope evals also report a hold rate per category.

```bash
python -m evals.run_eval                 # live run, needs ANTHROPIC_API_KEY, costs a few cents
python -m evals.run_eval --json
python -m evals.run_eval --sweep-effort  # low/medium/high; picks cheapest that holds quality
python -m evals.run_eval --effort low
python -m evals.run_eval --check-baseline
python -m evals.run_policy_eval          # offline deploy + runbook settlement (no API key)
python -m evals.mine_failures --audit-log audit.jsonl --out evals/cases_mined/  # review candidates only
```

With `--json`, `run_eval` (single run and `--sweep-effort`) and `run_policy_eval` write exactly one JSON document to stdout; regression text goes to stderr. Without `--json` it stays on stdout. `run_policy_eval` also runs the scope-adherence cases in `evals/cases_scope/`, including a poisoned-memory case.

`evals/cases_mined/` is generated for a person to review. Those files are not scored and are not part of the baseline.

Held-out cases are scored and compared with `evals/baseline.json` `per_case`, but are not used to pick an effort or to mine candidates. A `per_case` entry marked `held_out` with no scored row fails. The held-out mark comes from the harness (`RunConfig.held_out`, written as top-level `held_out` on the audit row); `mine_failures` ignores `data.held_out` and `data.split`. `--sweep-effort` scores held-out cases once, at the chosen effort, and reports regressions (exit 1 with `--check-baseline`).

`--sweep-effort` runs the suite at each effort level and picks the cheapest whose recall,
exact-gate rate, and injection hold rate stay within 5 points of `high`. Write the winner into
`harness/effort_defaults.json`. `evals/baseline.json` holds floors CI enforces offline; live runs
use `--check-baseline` to fail on regressions.

The scorer and policy evals are unit-tested offline so CI spends no tokens. A failed or timed-out
review counts as a miss.

Multi-turn: pass `prior_messages=` into `AgentHarness.run` (returned on `result.messages`).
Set `RunConfig(use_message_threads=True, thread_id=...)` to send the Anthropic message-threads
beta header when you want server-side cache of prior turns on long-lived agents.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

Tests run without an API key: the Anthropic client is faked, and the suite covers fail-closed paths, gates, lifecycle persistence, daily budgets, injection refusal, evidence schemas, and offline policy evals. Coverage must stay ≥ 70%.

```bash
pip install -e ".[dev,otel]"
ruff check . && mypy && pytest -q
python -m evals.run_policy_eval
```

`bash scripts/check.sh` runs the same stages (`ruff check`, `mypy harness agents`, `pytest` with the coverage floor, the offline policy eval) plus two heuristic checks for agent misbehavior and assertion-free tests, and prints `CHECK PASSED` or `CHECK FAILED`. The tools must be on `PATH` (activate a venv with the dev extras). `.coverage` is tracked in git and pytest rewrites it; restore it with `git checkout .coverage` and do not commit it.

The pairing-kit scripts also include `scripts/plan-review.sh` and `scripts/cross-review.sh` (`.ps1` variant for Windows). They send a plan or a diff to another AI provider for an independent review, after scanning the request with gitleaks. They fail closed: if gitleaks is missing, finds a match, or fails, nothing is sent. They need a provider key or Cursor setup; see the header of each script.

Note: in CI the PR review fails closed when `ANTHROPIC_API_KEY` is unavailable (for example on pull requests from forks), so those PRs need a manual review.
