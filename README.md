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
- **Cost governance** — every run reports its cost from API usage. `max_cost_usd` is enforced twice: a worst-case ceiling (estimated input plus full `max_tokens` output) blocks an expensive call *before* it is made, and the actual cost is checked after. Models are priced from `MODEL_PRICES` or a `RunConfig` override; an unpriced model is refused.
- **Confidence gating** — agents self-report confidence (0.0–1.0). Runs below `min_confidence` are blocked before their output reaches downstream systems.
- **Structured outputs** — all agents return parsed JSON, not prose. Parseable by CI systems, dashboards, and other agents.
- **Audit log** — every decision written to a JSONL file, including failures. Append to your SIEM or log aggregator.
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

Add `.github/workflows/pr_review.yml` to your repo. Every PR targeting `main` will be reviewed automatically. The check fails (blocking merge) if the agent returns HIGH severity findings.

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

The span attributes are covered by a unit test; the Jaeger compose file has not been exercised in CI.

## Reviewer eval

`evals/` holds ten seeded diffs (six with a planted defect: hardcoded key, SQL injection, unbounded
retry, missing timeout, swallowed error, wildcard IAM; four clean) and a scorer that reports
precision, recall, exact-gate rate and category hit rate for the PR reviewer.

```bash
python -m evals.run_eval          # live run, needs ANTHROPIC_API_KEY, costs a few cents
python -m evals.run_eval --json
```

The scorer itself is unit-tested offline (`tests/test_evals.py`) with fake reviewers, so CI checks
the harness without spending tokens. Results from live runs are not committed yet; run it against
your model and settings before trusting the gate. A failed or timed-out review counts as a miss.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

Tests run without an API key: the Anthropic client is faked, and the suite covers the fail-closed paths (no key, no SDK, malformed output, pre-call cost block), the gates, the audit log, and the PR reviewer's rule that a truncated diff can never be approved.

```bash
pip install -e ".[dev,otel]"
ruff check . && mypy && pytest -q
```

Note: in CI the PR review fails closed when `ANTHROPIC_API_KEY` is unavailable (for example on pull requests from forks), so those PRs need a manual review.
