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
from dataclasses import dataclass
from typing import Any

from harness.budget import BudgetExceeded, check_daily_budget
from harness.lifecycle import ModelOutputError, Phase, RunMachine, is_model_retryable
from harness.schemas import AgentScope
from harness.store import LifecycleStore

try:
    import anthropic

    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

try:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False

logger = logging.getLogger(__name__)

# ── Pricing (USD per million tokens: input, output) ─────────────────────────
# Keep this table current, or pass price_per_m_input/output in RunConfig. A model that is
# in neither place cannot be priced, so the run is refused rather than guessed at.
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "claude-sonnet-5-5": (2.00, 10.00),  # claude.com/pricing, checked 2026-11-02
    "claude-sonnet-4-6": (3.00, 15.00),
}
CHARS_PER_TOKEN = 3.5  # conservative estimate for the pre-call cost ceiling
# Soft guidance for how hard the model thinks. Lower usually cuts cost more than quality
# (Vals Index, 2026-11). None leaves the model's default. Passed as output_config.effort.
REASONING_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})


# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass
class AgentResult:
    """Structured output from a single agent run."""

    agent_name: str
    run_id: str
    success: bool
    data: dict  # The parsed JSON payload
    confidence: float  # 0.0–1.0, agent self-reported
    reasoning: str  # Agent's chain-of-thought summary
    cost_usd: float  # Exact API cost for this run
    duration_s: float  # Wall-clock time in seconds
    input_tokens: int
    output_tokens: int
    model: str
    gate_passed: bool = True  # Set by gate evaluation
    gate_reason: str = ""
    reasoning_effort: str | None = None
    lifecycle: RunMachine | None = None  # Authoritative phase; not decided by the model
    # Prior turns + this turn's assistant reply (for multi-turn / message-thread resumes).
    messages: list[dict[str, Any]] | None = None

    def passed(self) -> bool:
        return self.success and self.gate_passed

    def to_dict(self) -> dict:
        # Private attributes (the attached harness) are not part of the audit record.
        skip = {"lifecycle", "messages"}
        data = {k: v for k, v in self.__dict__.items() if k not in skip and not k.startswith("_")}
        if self.lifecycle is not None:
            data["lifecycle"] = self.lifecycle.to_dict()
        if self.messages is not None:
            data["message_count"] = len(self.messages)
        return data


@dataclass
class RunConfig:
    """Configuration for a single agent run."""

    agent_name: str
    model: str = "claude-sonnet-5-5"
    max_tokens: int = 2048
    min_confidence: float = 0.7  # Gate: reject below this
    max_cost_usd: float = 0.50  # Gate: reject if estimated cost exceeds this
    otel_endpoint: str | None = None  # OTLP gRPC endpoint
    audit_log_path: str | None = None  # Path to append audit JSONL
    price_per_m_input: float | None = None  # Override MODEL_PRICES (USD / 1M tokens)
    price_per_m_output: float | None = None
    dry_run: bool = False  # Canned response, no API call. Never a real pass.
    max_attempts: int = 2  # Model-call attempts. Side effects are not retried here.
    # Soft spend/quality dial. max_cost_usd remains the hard ceiling.
    reasoning_effort: str | None = None
    # Process budget: sum of today's audit cost for this agent + upcoming call.
    max_cost_usd_per_day: float | None = None
    # Persist RunMachine snapshots here (one JSON file per run_id) for crash resume.
    lifecycle_dir: str | None = None
    # Anthropic message-threads beta: cache prior turns for lower TTFT on long contexts.
    use_message_threads: bool = False
    thread_id: str | None = None
    # Who invoked this run (person or system). Written on every audit row.
    caller_identity: str | None = None
    # Session scope for "does this action match the request". None means unset.
    # allowed_tools=[] means the caller set a scope that permits no tools.
    intent: str | None = None
    allowed_tools: list[str] | None = None

    def __post_init__(self) -> None:
        if self.max_cost_usd_per_day is not None and self.max_cost_usd_per_day < 0:
            raise ValueError("max_cost_usd_per_day must be >= 0")
        if self.caller_identity is not None:
            cleaned = self.caller_identity.strip()
            self.caller_identity = cleaned or None
        if self.intent is not None:
            intent = str(self.intent).strip()
            self.intent = intent or None
        if self.allowed_tools is not None:
            if isinstance(self.allowed_tools, str):
                raise TypeError("allowed_tools must be a list of tool names, not a string")
            tools: list[str] = []
            seen: set[str] = set()
            for tool in self.allowed_tools:
                if not isinstance(tool, str):
                    continue
                name = tool.strip()
                if name and name not in seen:
                    seen.add(name)
                    tools.append(name)
            self.allowed_tools = tools
        if self.reasoning_effort is None:
            return
        level = self.reasoning_effort.strip().lower()
        if level not in REASONING_EFFORTS:
            allowed = ", ".join(sorted(REASONING_EFFORTS))
            raise ValueError(f"reasoning_effort must be one of {allowed}, got {self.reasoning_effort!r}")
        self.reasoning_effort = level


# ── OTel setup ────────────────────────────────────────────────────────────────


def _setup_tracer(service_name: str, endpoint: str | None) -> Any:
    if not OTEL_AVAILABLE:
        return None
    resource = Resource({"service.name": service_name, "service.version": "0.1.0"})
    provider = TracerProvider(resource=resource)
    if endpoint:
        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
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

    def __init__(self, config: RunConfig, api_key: str | None = None) -> None:
        self.config = config  # __post_init__ already validated reasoning_effort
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._tracer = _setup_tracer(
            service_name=f"agent-harness.{config.agent_name}",
            endpoint=config.otel_endpoint,
        )
        self._store = LifecycleStore(config.lifecycle_dir) if config.lifecycle_dir else None
        if not ANTHROPIC_AVAILABLE:
            logger.warning("anthropic package not installed: real runs will fail closed")

    def save_lifecycle(self, machine: RunMachine) -> None:
        """Persist the authoritative phase so a crash can resume mid-gated / mid-approved."""
        if self._store is not None:
            self._store.save(machine)

    def load_lifecycle(self, run_id: str) -> RunMachine | None:
        if self._store is None:
            return None
        return self._store.load(run_id)

    def run(
        self,
        system_prompt: str,
        user_message: str,
        context: dict | None = None,
        *,
        run_id: str | None = None,
        prior_messages: list[dict[str, Any]] | None = None,
    ) -> AgentResult:
        """
        Execute one agent turn and return a structured AgentResult.

        The system_prompt MUST instruct the model to return JSON with at minimum:
          { "confidence": float, "reasoning": str, ...task-specific fields... }

        Pass run_id to resume a persisted lifecycle (must be planned or failed-retryable
        returning to running). Pass prior_messages for multi-turn context; the result
        includes the updated transcript on result.messages.

        Args:
            system_prompt: Instructions and output schema for the agent.
            user_message:  The actual task payload (diff, incident data, etc.)
            context:       Optional metadata added to OTel span attributes.
            run_id:        Resume this persisted run, or mint a new id when omitted.
            prior_messages: Prior user/assistant turns (not including this user_message).

        Returns:
            AgentResult — always returned, even on failure (success=False).
        """
        start = time.monotonic()
        machine, run_id = self._begin_machine(run_id)
        transcript = list(prior_messages or [])
        transcript.append({"role": "user", "content": user_message})

        span_ctx = (
            self._tracer.start_as_current_span(
                f"agent.{self.config.agent_name}.run",
                attributes={
                    "agent.name": self.config.agent_name,
                    "agent.run_id": run_id,
                    "agent.model": self.config.model,
                    "agent.turn_count": len(transcript),
                    **(
                        {"agent.reasoning_effort": self.config.reasoning_effort} if self.config.reasoning_effort else {}
                    ),
                    **(context or {}),
                },
            )
            if self._tracer
            else _NullSpan()
        )

        with span_ctx as span:
            total_in = 0
            total_out = 0
            total_cost = 0.0
            result: AgentResult | None = None
            while machine.phase == Phase.RUNNING:
                try:
                    raw, input_tok, output_tok = self._call_api(system_prompt, transcript)
                    total_in += input_tok
                    total_out += output_tok
                    total_cost += self._calculate_cost(input_tok, output_tok)
                    data, confidence, reasoning = self._parse_response(raw)
                    dur = time.monotonic() - start
                    messages = list(transcript) + [{"role": "assistant", "content": raw}]

                    result = AgentResult(
                        agent_name=self.config.agent_name,
                        run_id=run_id,
                        success=True,
                        data=data,
                        confidence=confidence,
                        reasoning=reasoning,
                        cost_usd=total_cost,
                        duration_s=dur,
                        input_tokens=total_in,
                        output_tokens=total_out,
                        model=self.config.model,
                        reasoning_effort=self.config.reasoning_effort,
                        lifecycle=machine,
                        messages=messages,
                    )
                    result = self._evaluate_gates(result)
                    if self.config.dry_run:
                        result.gate_passed = False
                        result.gate_reason = "Dry run: no API call was made"
                    machine.succeed()  # running → gated. Domain policy decides what happens next.
                    self.save_lifecycle(machine)
                except Exception as exc:
                    spent_another = False
                    try:
                        spent_another = total_cost + self._worst_case_cost(system_prompt, transcript) > (
                            self.config.max_cost_usd
                        )
                    except RuntimeError:
                        spent_another = False
                    retryable = (
                        is_model_retryable(exc)
                        and not isinstance(exc, BudgetExceeded)
                        and not self.config.dry_run
                        and not spent_another
                    )
                    machine.fail_model(str(exc), retryable=retryable)
                    self.save_lifecycle(machine)
                    if machine.phase == Phase.FAILED_RETRYABLE:
                        logger.warning(
                            "Agent %s run %s attempt %s failed (%s); retrying model call",
                            self.config.agent_name,
                            run_id,
                            machine.model_attempt,
                            exc,
                        )
                        if hasattr(span, "record_exception"):
                            span.record_exception(exc)
                        machine.retry()
                        self.save_lifecycle(machine)
                        continue
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
                        cost_usd=total_cost,
                        duration_s=dur,
                        input_tokens=total_in,
                        output_tokens=total_out,
                        model=self.config.model,
                        gate_passed=False,
                        gate_reason=f"Agent execution failed: {exc}",
                        reasoning_effort=self.config.reasoning_effort,
                        lifecycle=machine,
                        messages=list(transcript),
                    )

            if result is None:
                raise RuntimeError("agent run ended without a result")  # unreachable; keeps the type honest
            if hasattr(span, "set_attribute"):
                span.set_attribute("agent.confidence", result.confidence)
                span.set_attribute("agent.cost_usd", result.cost_usd)
                span.set_attribute("agent.gate_passed", result.gate_passed)
                span.set_attribute("agent.duration_s", result.duration_s)
                span.set_attribute("agent.phase", machine.phase.value)
                span.set_attribute("agent.model_attempts", machine.model_attempt)
                if self.config.reasoning_effort:
                    span.set_attribute("agent.reasoning_effort", self.config.reasoning_effort)

        self.save_lifecycle(machine)
        self._audit(result)
        return result

    def _begin_machine(self, run_id: str | None) -> tuple[RunMachine, str]:
        if run_id and self._store is not None:
            existing = self._store.load(run_id)
            if existing is not None:
                if existing.agent_name != self.config.agent_name:
                    raise RuntimeError(
                        f"lifecycle {run_id!r} belongs to {existing.agent_name!r}, not {self.config.agent_name!r}"
                    )
                if existing.phase == Phase.FAILED_RETRYABLE:
                    existing.retry()
                elif existing.phase != Phase.PLANNED:
                    raise RuntimeError(
                        f"cannot resume run {run_id!r} from phase {existing.phase.value}; "
                        "only planned or failed-retryable may re-enter running"
                    )
                else:
                    existing.start()
                self.save_lifecycle(existing)
                return existing, run_id
        minted = run_id or str(uuid.uuid4())[:8]
        machine = RunMachine(
            run_id=minted,
            agent_name=self.config.agent_name,
            max_attempts=self.config.max_attempts,
        )
        machine.start()
        self.save_lifecycle(machine)
        return machine, minted

    # ── Private helpers ───────────────────────────────────────────────────────

    def _prices(self) -> tuple[float, float]:
        """Return (input, output) USD per 1M tokens, or raise if the model cannot be priced."""
        inp, out = self.config.price_per_m_input, self.config.price_per_m_output
        if inp is not None and out is not None:
            return inp, out
        if self.config.model in MODEL_PRICES:
            return MODEL_PRICES[self.config.model]
        raise RuntimeError(
            f"No pricing for model {self.config.model!r}. Add it to MODEL_PRICES or set "
            "price_per_m_input and price_per_m_output in RunConfig."
        )

    def _messages_chars(self, messages: list[dict[str, Any]]) -> int:
        total = 0
        for message in messages:
            content = message.get("content", "")
            if isinstance(content, str):
                total += len(content)
            else:
                total += len(json.dumps(content))
        return total

    def _worst_case_cost(self, system: str, messages: list[dict[str, Any]]) -> float:
        """Upper bound on cost before calling: estimated input plus the full max_tokens output."""
        inp, out = self._prices()
        est_in = (len(system) + self._messages_chars(messages)) / CHARS_PER_TOKEN
        return est_in / 1_000_000 * inp + self.config.max_tokens / 1_000_000 * out

    def _call_api(self, system: str, messages: list[dict[str, Any]]) -> tuple[str, int, int]:
        """Call the Anthropic API and return (raw_text, input_tokens, output_tokens).

        Fails closed: a missing SDK or key raises, so the run is reported as failed and
        gated. Only an explicit RunConfig(dry_run=True) returns a canned response, and that
        response is marked DRY_RUN with zero confidence so no gate can pass on it.
        """
        if self.config.dry_run:
            canned = json.dumps(
                {
                    "confidence": 0.0,
                    "reasoning": "DRY_RUN: no API call was made",
                    "findings": [],
                    "recommendation": "DRY_RUN",
                }
            )
            return canned, 0, 0
        if not ANTHROPIC_AVAILABLE:
            raise RuntimeError("anthropic package is not installed (pip install anthropic)")
        if not self._api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")

        ceiling = self._worst_case_cost(system, messages)
        if ceiling > self.config.max_cost_usd:
            raise RuntimeError(
                f"Worst-case cost ${ceiling:.4f} exceeds max ${self.config.max_cost_usd:.2f}; call not made"
            )
        check_daily_budget(
            self.config.audit_log_path,
            agent_name=self.config.agent_name,
            max_cost_usd_per_day=self.config.max_cost_usd_per_day,
            upcoming_cost_usd=ceiling,
        )

        client = anthropic.Anthropic(api_key=self._api_key)
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "system": system,
            "messages": messages,
        }
        # Effort is soft guidance; max_cost_usd / max_tokens remain the hard ceilings.
        if self.config.reasoning_effort is not None:
            kwargs["output_config"] = {"effort": self.config.reasoning_effort}
        # Message threads (public beta): reuse server-side cache of prior turns for lower TTFT.
        if self.config.use_message_threads:
            kwargs["extra_headers"] = {"anthropic-beta": "message-threads-2025-11-13"}
            if self.config.thread_id:
                kwargs["extra_body"] = {"thread_id": self.config.thread_id}
        response = client.messages.create(**kwargs)
        # Content may include non-text blocks (thinking, tool use); keep only the text.
        raw = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
        return raw, response.usage.input_tokens, response.usage.output_tokens

    def _parse_response(self, raw: str) -> tuple[dict, float, str]:
        """Parse the model's JSON response.

        A malformed body raises ModelOutputError, which the lifecycle may retry.
        The model has not touched a business system at this point.
        """
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ModelOutputError("model output was not a JSON object")
            confidence = float(data.get("confidence", 0.0))
        except ModelOutputError:
            raise
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ModelOutputError(str(exc)) from exc
        reasoning = str(data.get("reasoning", ""))
        return data, confidence, reasoning

    def _calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        inp, out = self._prices()
        return input_tokens / 1_000_000 * inp + output_tokens / 1_000_000 * out

    def _evaluate_gates(self, result: AgentResult) -> AgentResult:
        """Apply confidence and cost gates. Updates result in place."""
        if result.confidence < self.config.min_confidence:
            result.gate_passed = False
            result.gate_reason = f"Confidence {result.confidence:.2f} below threshold {self.config.min_confidence:.2f}"
        elif result.cost_usd > self.config.max_cost_usd:
            result.gate_passed = False
            result.gate_reason = f"Cost ${result.cost_usd:.4f} exceeds max ${self.config.max_cost_usd:.2f}"
        return result

    def audit(self, result: AgentResult) -> None:
        """Append a JSONL audit record for this run.

        Every row carries agent_name (from the result), caller_identity (who
        invoked the run), and agent_scope (caller, intent, allowed tools). When
        the lifecycle has an approval, `approver` is also written (call again
        after settle so that field is present).
        """
        record = {**result.to_dict(), "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        record["caller_identity"] = self.config.caller_identity
        scope = AgentScope(
            caller=self.config.caller_identity,
            intent=self.config.intent,
            allowed_tools=None if self.config.allowed_tools is None else list(self.config.allowed_tools),
        )
        record["agent_scope"] = scope.to_dict()
        if result.lifecycle is not None and result.lifecycle.approval is not None:
            record["approver"] = result.lifecycle.approval.actor
        logger.info("agent_run %s", json.dumps(record))
        if self.config.audit_log_path:
            try:
                with open(self.config.audit_log_path, "a") as f:
                    f.write(json.dumps(record) + "\n")
            except OSError:
                logger.warning("Could not write audit log to %s", self.config.audit_log_path)

    def _audit(self, result: AgentResult) -> None:
        self.audit(result)


class _NullSpan:
    """No-op span for when OTel is not available."""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def set_attribute(self, *_):
        pass

    def record_exception(self, *_):
        pass
