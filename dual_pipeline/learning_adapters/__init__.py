"""
dual_pipeline/learning_adapters/__init__.py

Adapters bridging agent runtime execution outcomes (adjudication errors, retrieval misses,
speaker corrections, decision overrides, quality failures) into durable learning events.
"""
from __future__ import annotations

from .agent_outcome_adapter import (
    AgentOutcomeAdapter,
    AgentOutcomeEvent,
    OutcomeType,
)
from .correction_feedback_ingestor import (
    CorrectionCohort,
    CorrectionFeedbackIngestor,
    OperatorCorrection,
)
from .live_pipeline_integrator import (
    LivePipelineIntegrator,
    get_live_pipeline_integrator,
)

__all__ = [
    "AgentOutcomeAdapter",
    "AgentOutcomeEvent",
    "CorrectionCohort",
    "CorrectionFeedbackIngestor",
    "LivePipelineIntegrator",
    "OperatorCorrection",
    "OutcomeType",
    "get_live_pipeline_integrator",
]
