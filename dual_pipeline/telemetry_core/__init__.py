"""Agent telemetry core package for trajectory tracking, ledger recording, and observability."""
from __future__ import annotations

from .agent_trajectory import (
    AgentTrajectoryTelemetry,
    TelemetryPrivacyViolation,
    TrajectoryStep,
)
from .constants import (
    L2_OTEL_ATTRIBUTE_KEYS,
    LEDGER_SCHEMA,
    SPAN_NAMES,
    ledger_connection,
)
from .events import (
    record_bipartite_leak_event,
    record_eval_result,
    record_phonetic_repair_event,
    record_quarantine_event,
)
from .recorder import RunRecorder, verify_run_l4_seal
from .taxonomy import (
    AmbiguityThresholdExceeded,
    BudgetExhaustionError,
    PipelineError,
    PolicyViolationError,
    SchemaValidationError,
    TransientNetworkError,
)

__all__ = [
    "AgentTrajectoryTelemetry",
    "TrajectoryStep",
    "TelemetryPrivacyViolation",
    "RunRecorder",
    "verify_run_l4_seal",
    "SPAN_NAMES",
    "L2_OTEL_ATTRIBUTE_KEYS",
    "LEDGER_SCHEMA",
    "ledger_connection",
    "record_eval_result",
    "record_quarantine_event",
    "record_bipartite_leak_event",
    "record_phonetic_repair_event",
    "PipelineError",
    "TransientNetworkError",
    "PolicyViolationError",
    "SchemaValidationError",
    "BudgetExhaustionError",
    "AmbiguityThresholdExceeded",
]
