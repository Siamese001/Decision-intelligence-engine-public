"""Specialized multi-agent mesh for Pipeline D: Dialectical Decision Intelligence & HITL Arbitration.

Agents:
- Agent-D0: DecisionOrchestrator (dialectic coordinator & terminal states)
- Agent-D1: RedTeamRiskAgent (adversarial risk analysis & fatal flaws via GPT-5.6-sol)
- Agent-D2: StrategicFeasibilityAgent (alignment, velocity & ROI via GPT-5.6-sol)
- Agent-D3: FastPathSyncAgent (Law 3 untrusted fast path & [UNTRUSTED_FAST_PATH] badge)
- Agent-D4: HitlArbiterAgent (15% margin gate Δ < 0.15 & Bayesian prior calibration)
- Agent-D5: BriefingSynthesisAgent (Law 7 sequencing, Artifact 02 recap & Figma 4K alignment)
"""
from __future__ import annotations

from .briefing_healer import (
    BriefingAndDecisionHealerAgent,
    get_briefing_healer_card,
)
from .briefing_synthesis_agent import (
    BriefingSynthesisAgent,
    PipelineSequencingViolationError,
    get_briefing_synthesis_card,
)
from .decision_orchestrator import (
    DecisionOrchestrator,
    get_decision_orchestrator_card,
)
from .dialectical_perspective_agent import (
    DialecticalPerspectiveAgent,
    PerspectiveType,
    get_dialectical_perspective_card,
)
from .fast_path_sync_agent import (
    FAST_PATH_BADGE,
    FastPathSyncAgent,
    Law3ViolationError,
    get_fast_path_sync_card,
)
from .hitl_arbiter_agent import (
    HitlArbiterAgent,
    get_hitl_arbiter_card,
)
from .l1_decision_cognition import (
    L1DecisionCognition,
    L1DecisionPlanContract,
)
from .post_debate_reviewer import DecisionPostDebateReviewer
from .red_team_risk_agent import (
    RedTeamRiskAgent,
    get_red_team_risk_card,
)
from .strategic_feasibility_agent import (
    StrategicFeasibilityAgent,
    get_strategic_feasibility_card,
)

__all__ = [
    "BriefingAndDecisionHealerAgent",
    "BriefingSynthesisAgent",
    "DecisionOrchestrator",
    "DecisionPostDebateReviewer",
    "DialecticalPerspectiveAgent",
    "FAST_PATH_BADGE",
    "FastPathSyncAgent",
    "HitlArbiterAgent",
    "L1DecisionCognition",
    "L1DecisionPlanContract",
    "Law3ViolationError",
    "PerspectiveType",
    "PipelineSequencingViolationError",
    "RedTeamRiskAgent",
    "StrategicFeasibilityAgent",
    "get_briefing_healer_card",
    "get_briefing_synthesis_card",
    "get_decision_orchestrator_card",
    "get_dialectical_perspective_card",
    "get_fast_path_sync_card",
    "get_hitl_arbiter_card",
    "get_red_team_risk_card",
    "get_strategic_feasibility_card",
]
