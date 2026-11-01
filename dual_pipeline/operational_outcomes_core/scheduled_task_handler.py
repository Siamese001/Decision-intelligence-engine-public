"""dual_pipeline.operational_outcomes_core.scheduled_task_handler

Scheduled task outcome handler connecting scheduled adapters to authentic evidence:
1. Validates scheduled execution against independent state readback.
2. Preserves visibility of adapter errors, pending, and partial states.
3. Automatically triggers read-only incident diagnosis upon failure or partial completion.
4. Prevents silent failure or artificial completion flags.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any, Dict, Optional

from dual_pipeline.core import utc_now

from .adapter import AdapterState, OperationalOutcomeAdapter
from .incident_diagnosis import DiagnosticProposal, IncidentDiagnosisEngine

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScheduledTaskReport:
    """Observable outcome report for a scheduled task execution."""
    task_id: str
    task_name: str
    state: AdapterState
    is_visible: bool
    diagnostic_proposal: Optional[DiagnosticProposal] = None
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=utc_now)


class ScheduledTaskOutcomeHandler:
    """Coordinates scheduled task execution with authentic outcome derivation and diagnosis."""

    def __init__(
        self,
        adapter: Optional[OperationalOutcomeAdapter] = None,
        diagnosis_engine: Optional[IncidentDiagnosisEngine] = None,
    ) -> None:
        self.adapter = adapter or OperationalOutcomeAdapter()
        self.diagnosis_engine = diagnosis_engine or IncidentDiagnosisEngine()

    def process_task_outcome(
        self,
        task_id: str,
        task_name: str,
        execution_context: Dict[str, Any],
        independent_readback: Optional[Dict[str, Any]] = None,
    ) -> ScheduledTaskReport:
        """Processes execution result of a scheduled task and generates observable outcome report."""
        state_result = self.adapter.derive_lifecycle_state(
            run_context=execution_context,
            independent_readback=independent_readback,
        )

        proposal: Optional[DiagnosticProposal] = None

        if state_result.state in (AdapterState.ERROR, AdapterState.PARTIAL, AdapterState.UNVERIFIED):
            failure_ctx = {
                "category": execution_context.get("category", "scheduled_task_failure"),
                "error_code": execution_context.get("error_code", f"TASK_{state_result.state.value}"),
                "source_component": f"scheduled_task:{task_name}",
                "root_cause": state_result.rationale,
                "source_file": execution_context.get("source_file", "dual_pipeline/hitl_governance_core/scheduler_adapter.py"),
                "line_number": execution_context.get("line_number"),
                "suggested_test_scope": [f"tests/test_{task_name}.py"],
                "evidence_refs": [f"task:{task_id}", f"run:{execution_context.get('run_id', 'unknown')}"],
            }
            proposal = self.diagnosis_engine.diagnose_incident(failure_ctx)

        return ScheduledTaskReport(
            task_id=task_id,
            task_name=task_name,
            state=state_result.state,
            is_visible=True,  # Guarantees state is always observable
            diagnostic_proposal=proposal,
            details={
                "rationale": state_result.rationale,
                "verified_stages": list(state_result.verified_stages),
                "missing_stages": list(state_result.missing_stages),
            },
        )
