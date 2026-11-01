"""dual_pipeline.operational_outcomes_core

Public API for Wave 11 operational outcome adapters, incident diagnosis,
and scheduled task outcome handling.
"""
from __future__ import annotations

from .adapter import (
    AdapterState,
    AdapterStateResult,
    OperationalOutcomeAdapter,
)
from .incident_diagnosis import (
    DiagnosticAction,
    DiagnosticProposal,
    IncidentDiagnosisEngine,
    IncidentSignature,
)
from .scheduled_task_handler import (
    ScheduledTaskOutcomeHandler,
    ScheduledTaskReport,
)

__all__ = [
    "AdapterState",
    "AdapterStateResult",
    "DiagnosticAction",
    "DiagnosticProposal",
    "IncidentDiagnosisEngine",
    "IncidentSignature",
    "OperationalOutcomeAdapter",
    "ScheduledTaskOutcomeHandler",
    "ScheduledTaskReport",
]
