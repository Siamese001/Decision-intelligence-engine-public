"""
tests/test_wave2_retrieval_decision_agent_learning_e2e.py

End-to-end lifecycle verification for Wave 2: Hybrid Retrieval Ranking & Dialectical Decision Learning.
Tests:
1. RetrievalCoordinator RETRIEVAL_MISS outcome emission and RetrievalPerspectiveHealer COMPONENT_REPAIR.
2. L1QueryCognition calibrated intent fusion weight consumption and HybridFusionRanker weighted RRF.
3. ReleaseGateAuditor 11-gate qualification and rejection gating on holdout replay.
4. DecisionOrchestrator DECISION_OVERRIDE outcome recording and FastPathSync conviction threshold tightening.
5. L1DecisionCognition bounded perspective weight clamping within [0.10, 0.45].
6. BriefingAndDecisionHealerAgent COMPONENT_REPAIR outcome emission.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

from dual_pipeline.a2a import A2ATask, A2ATaskStatus
from dual_pipeline.learning_adapters.agent_outcome_adapter import (
    AgentOutcomeAdapter,
    OutcomeType,
)
from dual_pipeline.learning_adapters.live_pipeline_integrator import (
    LivePipelineIntegrator,
)
from dual_pipeline.retrieval.agents.hybrid_fusion_ranker import HybridFusionRanker
from dual_pipeline.retrieval.agents.l1_query_cognition import L1QueryCognition
from dual_pipeline.retrieval.agents.release_gate_auditor import ReleaseGateAuditor
from dual_pipeline.retrieval.agents.retrieval_coordinator import RetrievalCoordinator
from dual_pipeline.retrieval.agents.retrieval_perspective_healer import (
    RetrievalPerspectiveHealerAgent,
)
from dual_pipeline.decision.agents.briefing_healer import (
    BriefingAndDecisionHealerAgent,
)
from dual_pipeline.decision.agents.decision_orchestrator import (
    DecisionOrchestrator,
)
from dual_pipeline.decision.agents.dialectical_perspective_agent import (
    DialecticalPerspectiveAgent,
    PerspectiveType,
)
from dual_pipeline.decision.agents.fast_path_sync_agent import FastPathSyncAgent
from dual_pipeline.decision.agents.l1_decision_cognition import (
    L1DecisionCognition,
)


@pytest.fixture
def sandbox_env():
    """Provides isolated temporary sandbox directory for ledger and runtime configs."""
    with tempfile.TemporaryDirectory(prefix="wave2_e2e_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir(parents=True, exist_ok=True)
        config_dir = tmp_path / "config" / "runtime"
        config_dir.mkdir(parents=True, exist_ok=True)

        params_path = config_dir / "parameters.json"
        initial_params = {
            "schema_version": "1.0.0",
            "parameters": {
                "retrieval": {
                    "intent_fusion_weights": {
                        "TECHNICAL_ARCHITECTURE": {"dense": 0.45, "sparse": 0.35, "colbert": 0.20},
                        "DELIVERY_TIMELINE": {"dense": 0.30, "sparse": 0.50, "colbert": 0.20},
                    },
                    "perspective_weights": {
                        "TECHNICAL_ARCHITECTURE": {"TECHNICAL": 0.60, "BLOCKER": 0.20, "ADOPTION": 0.10, "COMMERCIAL": 0.10},
                    },
                },
                "decision": {
                    "fast_path_conviction_threshold": 0.85,
                    "perspective_weights": {
                        "RED_TEAM": 0.35,
                        "STRATEGIC_FEASIBILITY": 0.35,
                        "SYNTHESIZER": 0.30,
                    },
                },
            },
        }
        with open(params_path, "w", encoding="utf-8") as f:
            json.dump(initial_params, f, indent=2)

        integrator = LivePipelineIntegrator(runtime_root=tmp_path)
        yield {
            "root": tmp_path,
            "params_path": params_path,
            "integrator": integrator,
        }


def test_retrieval_coordinator_miss_and_healer_telemetry(sandbox_env):
    """Verifies that RetrievalCoordinator records RETRIEVAL_MISS and perspective healer records COMPONENT_REPAIR."""
    integrator = sandbox_env["integrator"]
    coordinator = RetrievalCoordinator(mock_mode=True, integrator=integrator)

    # 1. Explicit retrieval miss outcome recording
    miss_evt = coordinator.record_retrieval_miss(
        query_text="What is the BGE-M3 target latency for large documents?",
        missing_requirements=["CITATION_COVERAGE_INSUFFICIENT", "GATE_LATENCY_EXCEEDED"],
        retrieved_count=0,
        run_id="run-ret-001",
    )
    assert miss_evt is not None
    assert miss_evt.outcome_type == OutcomeType.RETRIEVAL_MISS
    assert miss_evt.observed_outcome["query_text"] == "What is the BGE-M3 target latency for large documents?"

    # 2. Perspective healer repair telemetry
    healer = RetrievalPerspectiveHealerAgent(mock_mode=True, integrator=integrator)
    repair_evt = healer.record_perspective_repair(
        starved_lens="TECHNICAL",
        was_successful=True,
        run_id="run-heal-001",
    )
    assert repair_evt is not None
    assert repair_evt.outcome_type == OutcomeType.COMPONENT_REPAIR
    assert repair_evt.observed_outcome["starved_lens"] == "TECHNICAL"

    events = integrator.get_recorded_events()
    assert len(events) >= 2
    types = {e.outcome_type for e in events}
    assert OutcomeType.RETRIEVAL_MISS in types
    assert OutcomeType.COMPONENT_REPAIR in types


def test_l1_query_cognition_calibrated_weights_and_hybrid_fusion(sandbox_env):
    """Verifies L1QueryCognition loads calibrated weights and HybridFusionRanker applies stream weights."""
    params_path = sandbox_env["params_path"]
    cognition = L1QueryCognition(parameters_path=params_path)

    plan = cognition.plan(
        query="What is the system architecture of BGE-M3 vector index?",
        metadata={"bge_release_gate_passed": True},
    )
    assert plan.primary_intent == "TECHNICAL_ARCHITECTURE"
    assert plan.hybrid_weights["dense"] == 0.45
    assert plan.hybrid_weights["sparse"] == 0.35
    assert plan.perspective_weights["TECHNICAL"] == 0.60

    # Test HybridFusionRanker weighted RRF
    ranker = HybridFusionRanker(rrf_k=60)
    task = A2ATask(
        task_id="task-rf-001",
        parent_task_id=None,
        sender_agent_id="test",
        recipient_agent_id=ranker.card.agent_id,
        objective="Rank multiple streams with calibrated weights",
        status=A2ATaskStatus.SUBMITTED,
        input_payload={
            "ranked_lists": [
                [{"id": "doc-a", "title": "Doc A"}, {"id": "doc-b", "title": "Doc B"}],
                [{"id": "doc-b", "title": "Doc B"}, {"id": "doc-a", "title": "Doc A"}],
            ],
            "stream_weights": [2.0, 0.5],
            "limit": 5,
        },
    )
    res = asyncio.run(ranker.handle_task(task))
    assert res["status"] == "COMPLETED"
    fused = res["fused_facts"]
    # Doc A has higher weight (2.0 in rank 1 vs 0.5 in rank 2)
    assert fused[0]["id"] == "doc-a"
    assert fused[0]["rrf_retrieval_score"] > fused[1]["rrf_retrieval_score"]


def test_release_gate_auditor_qualification_and_rejection(sandbox_env):
    """Verifies ReleaseGateAuditor qualifies compliant pairs and rejects regressions."""
    auditor = ReleaseGateAuditor(mock_mode=True)

    # 1. Compliant payload passing all gates
    qual_task = A2ATask(
        task_id="task-audit-pass",
        parent_task_id=None,
        sender_agent_id="test",
        recipient_agent_id=auditor.card.agent_id,
        objective="Verify compliant candidate pair",
        status=A2ATaskStatus.SUBMITTED,
        input_payload={
            "backend_id": "bge-m3",
            "graph_sha256": "sha-graph-001",
            "sidecar_sha256": "sha-sidecar-001",
            "max_query_latency_ms": 150.0,
            "real_bge_m3_passed": True,
            "bm25_executed": True,
        },
    )
    res_pass = asyncio.run(auditor.handle_task(qual_task))
    assert res_pass["all_gates_passed"] is True
    assert res_pass["qualification_status"] == "DELIVERY_READY"

    # 2. Latency regression payload exceeding ceiling
    fail_task = A2ATask(
        task_id="task-audit-fail",
        parent_task_id=None,
        sender_agent_id="test",
        recipient_agent_id=auditor.card.agent_id,
        objective="Verify failing candidate pair",
        status=A2ATaskStatus.SUBMITTED,
        input_payload={
            "backend_id": "bge-m3",
            "graph_sha256": "sha-graph-001",
            "sidecar_sha256": "sha-sidecar-001",
            "max_query_latency_ms": 9999.0,  # Exceeds 7500ms ceiling
            "real_bge_m3_passed": True,
        },
    )
    res_fail = asyncio.run(auditor.handle_task(fail_task))
    assert res_fail["all_gates_passed"] is False
    assert res_fail["qualification_status"] == "BLOCKED"


def test_decision_orchestrator_override_and_fast_path_tightening(sandbox_env):
    """Verifies DecisionOrchestrator logs DECISION_OVERRIDE and FastPathSync tightens conviction threshold."""
    integrator = sandbox_env["integrator"]
    fast_path = FastPathSyncAgent(conviction_threshold=0.85)
    orchestrator = DecisionOrchestrator(
        fast_path_agent=fast_path,
        integrator=integrator,
    )

    # 1. Initial fast path check with 0.88 confidence clears 0.85 threshold
    turn1 = fast_path.ingest_live_turn(
        speaker="Lead Architect",
        text="We should implement PostgreSQL for storage.",
        confidence=0.88,
    )
    assert turn1["fast_path_admissible"] is True

    # 2. Operator overrides decision
    override_evt = orchestrator.record_decision_override(
        decision_id="dec-db-001",
        category="strategic_architecture",
        recommended_option="PostgreSQL",
        operator_chosen_option="SQLite WAL",
        run_id="run-dec-001",
    )
    assert override_evt is not None
    assert override_evt.outcome_type == OutcomeType.DECISION_OVERRIDE
    assert override_evt.expected_or_corrected_outcome["operator_chosen_option"] == "SQLite WAL"
    assert override_evt.observed_outcome["recommended_option"] == "PostgreSQL"

    # 3. Tighten conviction threshold based on override
    new_thresh = fast_path.tighten_conviction_threshold(delta=0.08)  # 0.85 -> 0.93
    assert new_thresh == 0.93

    # 4. Turn with 0.88 confidence now fails tighter threshold
    turn2 = fast_path.ingest_live_turn(
        speaker="Lead Architect",
        text="We should reconsider MongoDB.",
        confidence=0.88,
    )
    assert turn2["fast_path_admissible"] is False


def test_l1_decision_cognition_bounded_clamps():
    """Verifies L1DecisionCognition and DialecticalPerspectiveAgent strictly clamp weights to [0.10, 0.45]."""
    cognition = L1DecisionCognition()

    # Plan with heavy override count
    plan = cognition.plan(
        decision_id="dec-test",
        question="Which database architecture should we adopt?",
        options=[
            {"key": "opt-1", "title": "SQLite WAL Engine", "description": "Single-file serverless relational"},
            {"key": "opt-2", "title": "Managed PostgreSQL", "description": "Client server distributed cloud"},
        ],
        metadata={"operator_override_count": 10},
    )

    weights = plan.perspective_weights
    assert "RED_TEAM" in weights
    assert "STRATEGIC_FEASIBILITY" in weights
    for k, w in weights.items():
        assert 0.10 <= w <= 0.45, f"Perspective weight {k}={w} out of bounds [0.10, 0.45]"

    # Test DialecticalPerspectiveAgent set_role_weight clamping
    agent = DialecticalPerspectiveAgent(perspective=PerspectiveType.RED_TEAM, role_weight=0.35)
    # Attempt out of bounds values
    w_high = agent.set_role_weight(0.80)
    assert w_high == 0.45
    w_low = agent.set_role_weight(0.02)
    assert w_low == 0.10


def test_briefing_healer_component_repair_telemetry(sandbox_env):
    """Verifies BriefingAndDecisionHealerAgent emits COMPONENT_REPAIR events."""
    integrator = sandbox_env["integrator"]
    healer = BriefingAndDecisionHealerAgent(mock_mode=True, integrator=integrator)

    repair_evt = healer.record_briefing_repair(
        defect_type="PASSIVE_ACTION_VERBS",
        was_successful=True,
        run_id="run-briefing-001",
    )
    assert repair_evt is not None
    assert repair_evt.outcome_type == OutcomeType.COMPONENT_REPAIR
    assert repair_evt.observed_outcome["defect_type"] == "PASSIVE_ACTION_VERBS"

    events = integrator.get_recorded_events(outcome_type=OutcomeType.COMPONENT_REPAIR)
    assert len(events) >= 1
    assert events[-1].source_agent == "BriefingAndDecisionHealerAgent"
