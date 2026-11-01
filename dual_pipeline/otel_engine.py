"""
otel_engine.py — Universal OpenTelemetry Engine for Decision Intelligence.

Provides:
1. Vendor-agnostic OpenTelemetry TracerProvider with W3C TraceContext propagation.
2. Dual Exporters: Local append-only JSONL span ledger + optional OTLP HTTP/gRPC exporter.
3. W3C Context injection/extraction helpers across async queues, threads, IPC, and SQLite.
4. GenAI Semantic Convention tracing for LLM calls (capturing prompt hashes, actual returned models,
   prompt/completion tokens, and reasoning tokens).
5. Specialized helpers for Evals, Quarantine events, and Bipartite namespace security assertions.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple, Union

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
)
from opentelemetry.trace import Status, StatusCode, format_span_id, format_trace_id
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from config.credentials import CredentialManager

logger = logging.getLogger(__name__)

# W3C Propagator instance
_PROPAGATOR = TraceContextTextMapPropagator()
_TRACER_INITIALIZED = False
_ACTIVE_PROVIDER: Optional[TracerProvider] = None
_INIT_LOCK = threading.Lock()

from contextlib import contextmanager

from dual_pipeline.telemetry_core.exporter import JSONLSpanExporter


def init_telemetry(
    service_name: str = "decision-intelligence-engine",
    service_version: str = "5.7.0",
    jsonl_path: Optional[Union[str, Path]] = None,
    otlp_endpoint: Optional[str] = None,
) -> trace.Tracer:
    """
    Initializes the OpenTelemetry TracerProvider with dual export capabilities:
    1. Local JSONL file exporter (default: output/telemetry/traces_YYYYMMDD.jsonl)
    2. Optional standard OTLP HTTP/gRPC exporter (configured via otlp_endpoint or OTEL_EXPORTER_OTLP_ENDPOINT)
    """
    global _TRACER_INITIALIZED, _ACTIVE_PROVIDER

    with _INIT_LOCK:
        if _TRACER_INITIALIZED:
            return trace.get_tracer(service_name, service_version)

        resource = Resource.create(
            {
                "service.name": service_name,
                "service.version": service_version,
                "environment": CredentialManager.get_credential("ENVIRONMENT", "development"),
            }
        )

        provider = TracerProvider(resource=resource)

        # 1. Local JSONL Exporter
        if jsonl_path is None:
            today_str = datetime.date.today().strftime("%Y%m%d")
            jsonl_path = Path(".local/telemetry") / f"traces_{today_str}.jsonl"

        jsonl_exporter = JSONLSpanExporter(jsonl_path)
        # Use SimpleSpanProcessor for immediate disk flush
        provider.add_span_processor(SimpleSpanProcessor(jsonl_exporter))

        # 2. OTLP Exporter (if endpoint provided or set in env)
        endpoint = otlp_endpoint or CredentialManager.get_credential("OTEL_EXPORTER_OTLP_ENDPOINT")
        if endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )
                otlp_exporter = OTLPSpanExporter(endpoint=endpoint)
                provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
                logger.info("OTLP Trace Exporter initialized with endpoint: %s", endpoint)
            except Exception as exc:
                logger.warning("Failed to initialize OTLP exporter: %s", exc)

        trace.set_tracer_provider(provider)
        _ACTIVE_PROVIDER = provider
        _TRACER_INITIALIZED = True
        logger.info(
            "OpenTelemetry TracerProvider initialized (Service: %s, JSONL: %s)",
            service_name,
            jsonl_path,
        )

    return trace.get_tracer(service_name, service_version)


def shutdown_telemetry(timeout_millis: int = 2000) -> bool:
    """Flushes and shuts down the active OpenTelemetry TracerProvider within a bounded timeout."""
    global _TRACER_INITIALIZED, _ACTIVE_PROVIDER
    from dual_pipeline.telemetry_core.shutdown import perform_bounded_shutdown

    with _INIT_LOCK:
        if not _TRACER_INITIALIZED and _ACTIVE_PROVIDER is None:
            return True
        provider = _ACTIVE_PROVIDER or trace.get_tracer_provider()

        def _reset_state() -> None:
            global _TRACER_INITIALIZED, _ACTIVE_PROVIDER
            _ACTIVE_PROVIDER = None
            _TRACER_INITIALIZED = False

        return perform_bounded_shutdown(
            get_provider_fn=lambda: provider,
            reset_state_fn=_reset_state,
            timeout_millis=timeout_millis,
        )


def get_tracer(service_name: str = "decision-intelligence-engine") -> trace.Tracer:
    """Returns an active Tracer instance, initializing if necessary."""
    if not _TRACER_INITIALIZED:
        return init_telemetry(service_name=service_name)
    return trace.get_tracer(service_name)


# ────────────────────────────────────────────────────────────────────────
# W3C Context Propagation Helpers
# ────────────────────────────────────────────────────────────────────────

def inject_trace_context(carrier: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Injects current W3C TraceContext (traceparent, tracestate) into a dictionary carrier."""
    c = carrier if carrier is not None else {}
    _PROPAGATOR.inject(c)
    return c


def extract_trace_context(carrier: Dict[str, str]) -> Context:
    """Extracts W3C TraceContext from a dictionary carrier."""
    return _PROPAGATOR.extract(carrier)


def get_current_trace_and_span_id() -> Tuple[str, str]:
    """Returns the current active (trace_id, span_id) as hex strings."""
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx and ctx.is_valid:
        return format_trace_id(ctx.trace_id), format_span_id(ctx.span_id)
    return "", ""


def format_traceparent(span_or_context: Optional[Union[trace.Span, Context]] = None) -> str:
    """Formats a W3C traceparent string: 00-{trace_id}-{span_id}-{trace_flags:02x}."""
    if span_or_context is not None and isinstance(span_or_context, Context):
        current_span = trace.get_current_span(span_or_context)
    else:
        current_span = span_or_context or trace.get_current_span()
    if hasattr(current_span, "get_span_context"):
        ctx = current_span.get_span_context()
        if ctx and ctx.is_valid:
            trace_id_str = format_trace_id(ctx.trace_id)
            span_id_str = format_span_id(ctx.span_id)
            flags = f"{ctx.trace_flags:02x}"
            return f"00-{trace_id_str}-{span_id_str}-{flags}"
    return ""


@contextmanager
def trace_agent_handoff(
    operation_name: str,
    parent_carrier: Optional[Dict[str, str]] = None,
    attributes: Optional[Dict[str, Any]] = None,
    tracer: Optional[trace.Tracer] = None,
):
    """
    Context manager that starts a correlated child span across an agent boundary.
    Extracts W3C trace context from parent_carrier if provided, ensuring multi-agent
    handoffs never produce detached or orphaned spans.
    """
    active_tracer = tracer or get_tracer()
    ctx = extract_trace_context(parent_carrier) if parent_carrier else None

    with active_tracer.start_as_current_span(operation_name, context=ctx) as span:
        if attributes:
            for k, v in attributes.items():
                if isinstance(v, (str, int, float, bool)):
                    span.set_attribute(k, v)
                else:
                    span.set_attribute(k, str(v))
        try:
            yield span
            if span.status.status_code == StatusCode.UNSET:
                span.set_status(Status(StatusCode.OK))
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise


def propagate_subagent_context(
    task_payload: Dict[str, Any],
    span: Optional[trace.Span] = None,
) -> Dict[str, Any]:
    """
    Injects W3C TraceContext into a sub-agent task dictionary or carrier.
    Ensures outbound agent delegations carry traceparent and correlation identifiers.
    """
    carrier: Dict[str, str] = {}
    inject_trace_context(carrier)
    tp = format_traceparent(span)
    if tp:
        carrier["traceparent"] = tp
    task_payload["trace_context"] = carrier
    if "traceparent" not in task_payload and tp:
        task_payload["traceparent"] = tp
    t_id, s_id = get_current_trace_and_span_id()
    if t_id:
        task_payload["trace_id"] = t_id
    if s_id:
        task_payload["parent_span_id"] = s_id
    return task_payload


def extract_subagent_context(task_payload: Mapping[str, Any]) -> Context:
    """
    Extracts W3C TraceContext from a sub-agent task dictionary.
    Supports 'trace_context', 'carrier', or top-level 'traceparent' keys.
    """
    carrier: Dict[str, str] = {}
    if "trace_context" in task_payload and isinstance(task_payload["trace_context"], dict):
        carrier.update(task_payload["trace_context"])
    elif "carrier" in task_payload and isinstance(task_payload["carrier"], dict):
        carrier.update(task_payload["carrier"])
    if "traceparent" in task_payload and isinstance(task_payload["traceparent"], str):
        carrier["traceparent"] = task_payload["traceparent"]
    return extract_trace_context(carrier)


@contextmanager
def correlate_subagent_span(
    operation_name: str,
    task_payload: Mapping[str, Any],
    attributes: Optional[Dict[str, Any]] = None,
    tracer: Optional[trace.Tracer] = None,
):
    """
    Context manager for sub-agent execution. Extracts parent context from task_payload
    and executes the block inside a child span, guaranteeing span graph continuity.
    """
    carrier: Dict[str, str] = {}
    if "trace_context" in task_payload and isinstance(task_payload["trace_context"], dict):
        carrier.update(task_payload["trace_context"])
    elif "carrier" in task_payload and isinstance(task_payload["carrier"], dict):
        carrier.update(task_payload["carrier"])
    if "traceparent" in task_payload and isinstance(task_payload["traceparent"], str):
        carrier["traceparent"] = task_payload["traceparent"]

    with trace_agent_handoff(
        operation_name=operation_name,
        parent_carrier=carrier,
        attributes=attributes,
        tracer=tracer,
    ) as span:
        yield span


# ────────────────────────────────────────────────────────────────────────
# LLM Execution Tracing & Semantic Conventions
# ────────────────────────────────────────────────────────────────────────

from dual_pipeline.telemetry_core.llm_recorder import (
    LLMTraceContext,
    LLMTraceRecorder,
    compute_prompt_hash,
)


def trace_llm_execution(
    operation_name: str,
    requested_model: str = "",
    reasoning_effort: Optional[str] = None,
    prompt_content: Any = None,
    system: str = "openai",
    model: Optional[str] = None,
) -> LLMTraceContext:
    """
    Creates a standardized OpenTelemetry GenAI span and returns (span, LLMTraceRecorder).
    Can be used as a context manager (with trace_llm_execution(...) as recorder:)
    or unpacked as a tuple (span, recorder = trace_llm_execution(...)).
    """
    tracer = get_tracer()
    prompt_sha = compute_prompt_hash(prompt_content) if prompt_content else ""
    effective_model = model or requested_model
    from dual_pipeline.l0.ingestion_timing import correlation_attributes
    span = tracer.start_span(operation_name, attributes=correlation_attributes())
    span.set_attribute("gen_ai.system", system)
    span.set_attribute("gen_ai.request.model", effective_model)
    if reasoning_effort:
        span.set_attribute("gen_ai.request.reasoning_effort", reasoning_effort)
    if prompt_sha:
        span.set_attribute("gen_ai.prompt.sha256", prompt_sha)

    recorder = LLMTraceRecorder(span)
    return LLMTraceContext(span, recorder)

from dual_pipeline.telemetry_core.events import (
    record_bipartite_leak_event,
    record_eval_result,
    record_l1_post_l2_review_event,
    record_l1_replan_cycle_event,
    record_phonetic_repair_event,
    record_quarantine_event,
)

__all__ = [
    "JSONLSpanExporter",
    "init_telemetry",
    "shutdown_telemetry",
    "get_tracer",
    "inject_trace_context",
    "extract_trace_context",
    "get_current_trace_and_span_id",
    "format_traceparent",
    "compute_prompt_hash",
    "LLMTraceRecorder",
    "trace_llm_execution",
    "trace_agent_handoff",
    "propagate_subagent_context",
    "extract_subagent_context",
    "correlate_subagent_span",
    "record_eval_result",
    "record_quarantine_event",
    "record_bipartite_leak_event",
    "record_phonetic_repair_event",
    "record_l1_post_l2_review_event",
    "record_l1_replan_cycle_event",
]
