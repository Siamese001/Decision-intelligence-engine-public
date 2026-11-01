"""Sensitive-content-safe spans, durable reconstructable learning events, and OpenTelemetry facade."""
from __future__ import annotations

from .otel_engine import (
    JSONLSpanExporter,
    compute_prompt_hash,
    correlate_subagent_span,
    extract_subagent_context,
    extract_trace_context,
    format_traceparent,
    get_current_trace_and_span_id,
    get_tracer,
    init_telemetry,
    inject_trace_context,
    propagate_subagent_context,
    record_bipartite_leak_event,
    record_eval_result,
    record_phonetic_repair_event,
    record_quarantine_event,
    trace_agent_handoff,
    trace_llm_execution,
)
from .telemetry_core.constants import (
    L2_OTEL_ATTRIBUTE_KEYS,
    LEDGER_SCHEMA,
    SPAN_NAMES,
)
from .telemetry_core.constants import (
    ledger_connection as _ledger_connection,
)
from .telemetry_core.recorder import RunRecorder

from contextlib import contextmanager
from typing import Any, Dict, Optional


class NullPipelineSpanRecorder:
    """Null pipeline span recorder fallback when no span recorder is provided."""

    @contextmanager
    def span(self, name: str, attributes: Optional[Dict[str, Any]] = None):
        yield None


__all__ = [
    "SPAN_NAMES",
    "L2_OTEL_ATTRIBUTE_KEYS",
    "LEDGER_SCHEMA",
    "_ledger_connection",
    "RunRecorder",
    "NullPipelineSpanRecorder",
    "JSONLSpanExporter",
    "init_telemetry",
    "get_tracer",
    "inject_trace_context",
    "extract_trace_context",
    "get_current_trace_and_span_id",
    "format_traceparent",
    "compute_prompt_hash",
    "trace_llm_execution",
    "trace_agent_handoff",
    "propagate_subagent_context",
    "extract_subagent_context",
    "correlate_subagent_span",
    "record_eval_result",
    "record_quarantine_event",
    "record_bipartite_leak_event",
    "record_phonetic_repair_event",
]
