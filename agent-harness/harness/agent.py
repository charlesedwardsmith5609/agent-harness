"""
agent-harness: A lightweight framework for running production agentic AI
workflows with built-in observability, cost governance, and trust gating.

Design principles:
  - Every agent run emits an OTel trace span (failures included)
  - Cost is tracked per-run and per-model; callers see the bill
  - Agents return structured JSON, not prose — parseable by downstream systems
  - A confidence gate blocks low-quality outputs before they cause harm
  - Audit records are written for every decision, win or lose

Philosophy: agents earn trust on small, well-defined work with visible
success/failure. Every run produces telemetry that improves the next one.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False

logger = logging.getLogger(__name__)

# ── Cost constants (USD per million tokens, claude-sonnet-4-6) ──────────────
COST_PER_M_INPUT  = 3.00   # $3.00 / 1M input tokens
COST_PER_M_OUTPUT = 15.00  # $15.00 / 1M output tokens


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class AgentResult:
    """Structured output from a single agent run."""
    agent_name:   str
    run_id:       str
    success:      bool
    data:         dict                        # The parsed JSON payload
    confidence:   float                       # 0.0–1.0, agent self-reported
    reasoning:    str                         # Agent's chain-of-thought summary
    cost_usd:     float                       # Exact API cost for this run
    duration_s:   float                       # Wall-clock time in seconds
    input_tokens: int
    output_tokens: int
    model:        str
    gate_passed:  bool = True                 # Set by gate evaluation
    gate_reason:  str  = ""

    def passed(self) -> bool:
        return self.success and self.gate_passed

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class RunConfig:
    """Configuration for a single agent run."""
    agent_name:          str
    model:               str   = "claude-sonnet-4-6"
    max_tokens:          int   = 2048
    temperature:         float = 0.2           # Low temp for consistent structured output
    min_confidence:      float = 0.7           # Gate: reject below this
    max_cost_usd:        float = 0.50          # Gate: reject if estimated cost exceeds this
    otel_endpoint:       Optional[str] = None  # OTLP gRPC endpoint
    audit_log_path:      Optional[str] = None  # Path to append audit JSONL


# ── OTel setup ────────────────────────────────────────────────────────────────

def _setup_tracer(service_name: str, endpoint: Optional[str]) -> Any:
    if not OTEL_AVAILABLE:
        return None
    resource  = Resource({"service.name": service_name, "service.version": "0.1.0"})
    provider  = TracerProvider(resource=resource)
    if endpoint:
        exporter  = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        processor = BatchSpanProcessor(exporter)
        provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    return trace.get_tracer(service_name)


# ── Core harness ─────────────────────────────────────────────────────────────

class AgentHarness:
    """
    Runs Claude-powered agents with OTel tracing, cost tracking, and trust gating.

    Args:
        config:      RunConfig controlling model, gates, and observability.
        api_key:     Anthropic API key (defaults to ANTHROPIC_API_KEY env var).
    """

    def __init__(self, config: RunConfig, api_key: Optional[str] = None) -> None:
        self.config = config
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._tracer  = _setup_tracer(
            service_name=f"agent-harness.{config.agent_name}",
            endpoint=config.otel_endpoint,
        )
        if not ANTHROPIC_AVAILABLE:
            logger.warning("anthropic package not installed — using stub mode")

    def run(
        self,
        system_prompt: str,
        user_message:  str,
        context:       Optional[dict] = None,
    ) -> AgentResult:
        """
        Execute one agent turn and return a structured AgentResult.

        The system_prompt MUST instruct the model to return JSON with at minimum:
          { "confidence": float, "reasoning": str, ...task-specific fields... }

        Args:
            system_prompt: Instructions and output schema for the agent.
            user_message:  The actual task payload (diff, incident data, etc.)
            context:       Optional metadata added to OTel span attributes.

        Returns:
            AgentResult — always returned, even on failure (success=False).
        """
        run_id = str(uuid.uuid4())[:8]
        start  = time.monotonic()

        span_ctx = (
            self._tracer.start_as_current_span(
                f"agent.{self.config.agent_name}.run",
                attributes={
                    "agent.name":   self.config.agent_name,
                    "agent.run_id": run_id,
                    "agent.model":  self.config.model,
                    **(context or {}),
                },
            )
            if self._tracer else _NullSpan()
        )

        with span_ctx as span:
            try:
                raw, input_tok, output_tok = self._call_api(system_prompt, user_message)
                data, confidence, reasoning = self._parse_response(raw)
                cost  = self._calculate_cost(input_tok, output_tok)
                dur   = time.monotonic() - start

                result = AgentResult(
                    agent_name=self.config.agent_name,
                    run_id=run_id,
                    success=True,
                    data=data,
                    confidence=confidence,
                    reasoning=reasoning,
                    cost_usd=cost,
                    duration_s=dur,
                    input_tokens=input_tok,
                    output_tokens=output_tok,
                    model=self.config.model,
                )

                # Trust gate evaluation
                result = self._evaluate_gates(result)

                if hasattr(span, "set_attribute"):
                    span.set_attribute("agent.confidence",  confidence)
                    span.set_attribute("agent.cost_usd",    cost)
                    span.set_attribute("agent.gate_passed", result.gate_passed)
                    span.set_attribute("agent.duration_s",  dur)

            except Exception as exc:
                dur = time.monotonic() - start
                logger.exception("Agent %s run %s failed", self.config.agent_name, run_id)
                if hasattr(span, "record_exception"):
                    span.record_exception(exc)
                result = AgentResult(
                    agent_name=self.config.agent_name,
                    run_id=run_id,
                    success=False,
                    data={},
                    confidence=0.0,
                    reasoning=str(exc),
                    cost_usd=0.0,
                    duration_s=dur,
                    input_tokens=0,
                    output_tokens=0,
                    model=self.config.model,
                    gate_passed=False,
                    gate_reason="Agent execution failed",
                )

        self._audit(result)
        return result

    # ── Private helpers ───────────────────────────────────────────────────────

    def _call_api(self, system: str, user: str) -> tuple[str, int, int]:
        """Call the Anthropic API and return (raw_text, input_tokens, output_tokens)."""
        if not ANTHROPIC_AVAILABLE or not self._api_key:
            # Stub for environments without the SDK or a key
            stub = json.dumps({
                "confidence": 0.85,
                "reasoning": "Stub response — set ANTHROPIC_API_KEY to use real API",
                "findings": [],
                "recommendation": "STUB",
            })
            return stub, 100, 50

        client   = anthropic.Anthropic(api_key=self._api_key)
        response = client.messages.create(
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        raw = response.content[0].text
        return raw, response.usage.input_tokens, response.usage.output_tokens

    def _parse_response(self, raw: str) -> tuple[dict, float, str]:
        """Parse the model's JSON response. Raises on malformed output."""
        # Strip markdown fences if present
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text  = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

        data = json.loads(text)
        confidence = float(data.get("confidence", 0.0))
        reasoning  = str(data.get("reasoning", ""))
        return data, confidence, reasoning

    def _calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens  / 1_000_000 * COST_PER_M_INPUT
            + output_tokens / 1_000_000 * COST_PER_M_OUTPUT
        )

    def _evaluate_gates(self, result: AgentResult) -> AgentResult:
        """Apply confidence and cost gates. Updates result in place."""
        if result.confidence < self.config.min_confidence:
            result.gate_passed = False
            result.gate_reason = (
                f"Confidence {result.confidence:.2f} below threshold "
                f"{self.config.min_confidence:.2f}"
            )
        elif result.cost_usd > self.config.max_cost_usd:
            result.gate_passed = False
            result.gate_reason = (
                f"Cost ${result.cost_usd:.4f} exceeds max ${self.config.max_cost_usd:.2f}"
            )
        return result

    def _audit(self, result: AgentResult) -> None:
        """Append a JSONL audit record for this run."""
        record = {**result.to_dict(), "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        logger.info("agent_run %s", json.dumps(record))
        if self.config.audit_log_path:
            try:
                with open(self.config.audit_log_path, "a") as f:
                    f.write(json.dumps(record) + "\n")
            except OSError:
                logger.warning("Could not write audit log to %s", self.config.audit_log_path)


class _NullSpan:
    """No-op span for when OTel is not available."""
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def set_attribute(self, *_): pass
    def record_exception(self, *_): pass
