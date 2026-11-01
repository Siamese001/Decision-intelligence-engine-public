"""Pipeline C: Policy-Gated Retrieval & Copilot Context Fusion Package."""
from __future__ import annotations

from dual_pipeline.decision.context_fusion import (
    CopilotFusionEngine,
    CopilotFusionPayload,
)
from pipeline_c_retrieval.contracts import (
    CLIENT_SAFE_NAMESPACE,
    INTERNAL_ADVISORY_NAMESPACE,
    HybridWeights,
    RetrievedFact,
)
from pipeline_c_retrieval.engine import PolicyGatedRetrievalEngine

__all__ = [
    "CLIENT_SAFE_NAMESPACE",
    "INTERNAL_ADVISORY_NAMESPACE",
    "HybridWeights",
    "RetrievedFact",
    "PolicyGatedRetrievalEngine",
    "CopilotFusionPayload",
    "CopilotFusionEngine",
]
