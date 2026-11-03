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
- `agent.gate_passed`, domain-specific attributes (e.g. `github.pr_number`)

## Trust model

The harness implements a simple but explicit trust model:

```
agent call → parse JSON → confidence gate → cost gate → caller
                                                ↓
                                          audit log (always)
                                          OTel span (always)
```

If any step fails, the result has `gate_passed=False` and `passed()` returns `False`. Callers should treat `passed() == False` as a blocking condition, not a warning.

The `DEPLOY_OVERRIDE=true` environment variable provides a human escape hatch for the deploy gate — but it's logged in the audit trail.

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
