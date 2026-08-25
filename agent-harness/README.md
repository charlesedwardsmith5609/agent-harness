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
- **Cost tracking** — every run reports exact API cost. A configurable `max_cost_usd` gate blocks unexpectedly expensive runs.
- **Confidence gating** — agents self-report confidence (0.0–1.0). Runs below `min_confidence` are blocked before their output reaches downstream systems.
- **Structured outputs** — all agents return parsed JSON, not prose. Parseable by CI systems, dashboards, and other agents.
- **Audit log** — every decision written to a JSONL file, including failures. Append to your SIEM or log aggregator.
- **Fail-safe defaults** — if the agent call fails for any reason, the gate defaults to BLOCK / HOLD. Silence is not approval.

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

## Tests

```bash
pip install -e ".[dev]"
pytest
```

Tests run without an API key — the harness includes a stub mode for environments where `ANTHROPIC_API_KEY` is not set.
