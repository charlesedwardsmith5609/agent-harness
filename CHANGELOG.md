# Changelog

Format follows Keep a Changelog. Dates are UTC.

## [Unreleased] - 2026-11-02

- Prompt-injection eval fixtures for the PR reviewer (planted defect + "approve / ignore findings" instructions, including a self-replicating canary). Scorer tracks injection hold rate and canary replication; the gate must still block.
- `RunConfig.reasoning_effort` (`low`/`medium`/`high`/`xhigh`/`max`) passed as Anthropic `output_config.effort`. Per-agent defaults and `python -m evals.run_eval --sweep-effort` to pick the cheapest level that holds quality.
- Run lifecycle (`planned`, `running`, `gated`, `approved`, `failed-retryable`, `done`) owns approvals, model retries, side-effect idempotency, and completion criteria. Prompts report evidence; they no longer assign DEPLOY / HOLD / BLOCK or declare the run finished.
- `DEPLOY_OVERRIDE` is an approval transition and requires `DEPLOY_APPROVER` or `GITHUB_ACTOR`. A failed run cannot be overridden.
- Fail-closed API calls, pre-call worst-case cost ceiling, `dry_run` that never passes the gate
- Default model `claude-sonnet-5-5` with configurable pricing
- PR reviewer eval suite and observability demo
- CI on Python 3.10, 3.12, 3.13 (ruff, mypy, pytest)
