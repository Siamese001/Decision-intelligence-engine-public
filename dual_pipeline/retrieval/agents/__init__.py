"""Specialized Retrieval & Gate Verification Agents for Pipeline C.

Exposes Agents C0 through C4 conforming to the A2A protocol and Law 6 constraints.
"""
from __future__ import annotations

from .bounded_query_recovery_agent import (
    BoundedQueryRecoveryAgent,
    QueryRecoveryObservation,
    RecoveryActionReceipt,
    get_bounded_query_recovery_card,
)
from .hybrid_fusion_ranker import HybridFusionRanker, get_hybrid_fusion_ranker_card
from .l1_query_cognition import L1QueryCognition, L1RetrievalPlanContract
from .multi_perspective_dispatcher import (
    MultiPerspectiveDispatcher,
    get_multi_perspective_dispatcher_card,
)
from .policy_gatekeeper_agent import PolicyGatekeeperAgent, get_policy_gatekeeper_card
from .release_gate_auditor import ReleaseGateAuditor, get_release_gate_auditor_card
from .retrieval_coordinator import RetrievalCoordinator, get_retrieval_coordinator_card
from .retrieval_perspective_healer import (
    RetrievalPerspectiveHealerAgent,
    get_retrieval_perspective_healer_card,
)

__all__ = [
    "BoundedQueryRecoveryAgent",
    "HybridFusionRanker",
    "L1QueryCognition",
    "L1RetrievalPlanContract",
    "MultiPerspectiveDispatcher",
    "PolicyGatekeeperAgent",
    "QueryRecoveryObservation",
    "RecoveryActionReceipt",
    "ReleaseGateAuditor",
    "RetrievalCoordinator",
    "RetrievalPerspectiveHealerAgent",
    "get_bounded_query_recovery_card",
    "get_hybrid_fusion_ranker_card",
    "get_multi_perspective_dispatcher_card",
    "get_policy_gatekeeper_card",
    "get_release_gate_auditor_card",
    "get_retrieval_coordinator_card",
    "get_retrieval_perspective_healer_card",
]
