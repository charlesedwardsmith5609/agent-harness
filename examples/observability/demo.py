"""Emit agent-harness spans to a local OTLP endpoint without calling the Anthropic API.

    docker compose -f examples/observability/docker-compose.yml up -d
    python examples/observability/demo.py
    open http://localhost:16686   # service: agent-harness.demo-agent

Uses dry_run, so no API key or spend. A dry run never passes the gate; the span records that.
"""

from __future__ import annotations

import time

from harness.agent import AgentHarness, RunConfig

ENDPOINT = "http://localhost:4317"

if __name__ == "__main__":
    harness = AgentHarness(RunConfig(agent_name="demo-agent", otel_endpoint=ENDPOINT, dry_run=True))
    for i in range(3):
        result = harness.run("Return JSON.", f"demo task {i}", context={"demo.iteration": i})
        print(result.run_id, "gate_passed=", result.gate_passed)
    time.sleep(6)  # BatchSpanProcessor flushes on a timer
