"""
tests/test_wave2_retrieval_decision_learning_adversarial.py

Adversarial and edge-case verification for Wave 2: Hybrid Retrieval Ranking & Dialectical Decision Learning.
Covers 5 required governance edge-case categories:
1. Boundary payloads: empty candidate sets, empty/whitespace queries, single/zero options, null evidence.
2. Malformed input & corrupted configuration: corrupt parameters JSON, non-numeric weights, invalid types.
3. Concurrency & race: multi-threaded outcome recording across retrieval and decision agents.
4. Invariant bypass attempts: out-of-bounds perspective weights strictly clamped to [0.10, 0.45], unverified fusion weights rejected without 11 gates.
5. Rollback & recovery: snapshotting and non-destructive parameter rollback.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

from dual_pipeline.a2a import A2ATask, A2ATaskStatus
from dual_pipeline.decision.agents.briefing_healer import BriefingAndDecisionHealerAgent
from dual_pipeline.decision.agents.decision_orchestrator import DecisionOrchestrator
from dual_pipeline.decision.agents.dialectical_perspective_agent import (
    DialecticalPerspectiveAgent,
    PerspectiveType,
)
from dual_pipeline.decision.agents.fast_path_sync_agent import FastPathSyncAgent
from dual_pipeline.decision.agents.l1_decision_cognition import (
    L1DecisionCognition,
    L1DecisionPlanContract,
)
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


@pytest.fixture
def sandbox_env():
    """Provides isolated sandbox environment with default parameters for adversarial testing."""
    with tempfile.TemporaryDirectory(prefix="wave2_adv_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        config_dir = tmp_path / "config" / "runtime"
        config_dir.mkdir(parents=True, exist_ok=True)
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir(parents=True, exist_ok=True)

        params_path = config_dir / "parameters.json"
        default_params = {
            "schema_version": "1.0.0",
            "parameters": {
                "retrieval": {
                    "intent_fusion_weights": {
                        "TECHNICAL_ARCHITECTURE": {"dense": 0.45, "sparse": 0.35, "colbert": 0.20},
                        "GENERAL_INQUIRY": {"dense": 0.50, "sparse": 0.30, "colbert": 0.20},
                    },
                    "perspective_weights": {
                        "TECHNICAL_ARCHITECTURE": {"TECHNICAL": 0.60, "BLOCKER": 0.20, "ADOPTION": 0.10, "COMMERCIAL": 0.10}
                    },
                },
                "decision": {
                    "fast_path_conviction_threshold": 0.85,
                    "perspective_weights": {
                        "Chief Architect": 0.35,
                        "Chief Risk Officer": 0.30,
                        "Chief Financial Officer": 0.20,
                        "Chief Information Security Officer": 0.15,
                    },
                },
            },
        }
        with open(params_path, "w", encoding="utf-8") as f:
            json.dump(default_params, f, indent=2)

        integrator = LivePipelineIntegrator(runtime_root=tmp_path)
        yield {
            "root": tmp_path,
            "params_path": params_path,
            "integrator": integrator,
        }


# ==============================================================================
# Category 1: Boundary Payloads
# ==============================================================================


def test_boundary_empty_and_whitespace_retrieval(sandbox_env):
    """Category 1: Verifies hybrid fusion ranker and retrieval coordinator with empty and whitespace inputs."""
    ranker = HybridFusionRanker(rrf_k=60)

    # 1. Empty candidate lists
    task_empty = A2ATask(
        task_id="task-empty-fusion",
        parent_task_id=None,
        sender_agent_id="test",
        recipient_agent_id=ranker.card.agent_id,
        objective="Empty candidates test",
        status=A2ATaskStatus.SUBMITTED,
        input_payload={
            "ranked_lists": [],
            "query": "",
            "stream_weights": [0.4, 0.6],
        },
    )
    res_empty = asyncio.run(ranker.handle_task(task_empty))
    assert res_empty["status"] == "COMPLETED"
    assert res_empty["fused_facts"] == []

    # 2. Whitespace-only query coordinator call
    coordinator = RetrievalCoordinator(
        mock_mode=True,
        parameters_path=sandbox_env["params_path"],
        integrator=sandbox_env["integrator"],
    )
    coord_res = coordinator.retrieve(
        query="    \n\t   ",
        candidate_facts=[],
    )
    assert coord_res is not None
    assert coord_res.get("status") == "COMPLETED"


def test_boundary_empty_decision_cognition(sandbox_env):
    """Category 1: Verifies L1DecisionCognition with empty/null evidence and boundary options."""
    cognition = L1DecisionCognition()

    # Plan with empty canonical evidence and empty live turns
    plan = cognition.plan(
        decision_id="dec-empty-001",
        question="Should we deploy to staging?",
        options=[
            {"id": "opt-1", "label": "Deploy now"},
            {"id": "opt-2", "label": "Wait for wave 3"},
        ],
        canonical_evidence=[],
        live_turns=[],
    )
    assert isinstance(plan, L1DecisionPlanContract)
    assert plan.decision_id == "dec-empty-001"
    assert plan.sanitization_passed is True
    # Default perspective weights should be populated and bounded
    for role, weight in plan.perspective_weights.items():
        assert 0.10 <= weight <= 0.45


# ==============================================================================
# Category 2: Malformed Input & Corrupted Configuration
# ==============================================================================


def test_malformed_parameters_fallback(sandbox_env):
    """Category 2: Verifies L1QueryCognition falls back gracefully when parameters.json is malformed."""
    params_path = sandbox_env["params_path"]

    # Write corrupt JSON syntax
    with open(params_path, "w", encoding="utf-8") as f:
        f.write("{ invalid_json: this is corrupted content [[[")

    cognition = L1QueryCognition(parameters_path=params_path)
    plan = cognition.plan(
        query="What is the system architecture of BGE-M3 vector index?",
        metadata={"bge_release_gate_passed": True},
    )

    # Must fall back gracefully to baseline defaults without crashing
    assert isinstance(plan.hybrid_weights, dict)
    assert "dense" in plan.hybrid_weights and "sparse" in plan.hybrid_weights
    assert plan.hybrid_weights["dense"] == 0.40  # has_entities branch baseline default


def test_malformed_confidence_and_options(sandbox_env):
    """Category 2: Verifies FastPathSyncAgent and L1DecisionCognition with malformed types."""
    fast_path = FastPathSyncAgent(conviction_threshold=0.85)

    # Negative and out-of-range confidences
    turn_neg = fast_path.ingest_live_turn(
        speaker="Architect",
        text="Deploy immediately.",
        confidence=-1.5,
    )
    assert turn_neg["fast_path_admissible"] is False

    turn_huge = fast_path.ingest_live_turn(
        speaker="Architect",
        text="Deploy immediately.",
        confidence=99.0,
    )
    # High confidence is admissible if >= 0.85
    assert turn_huge["fast_path_admissible"] is True

    # None / non-numeric confidence gracefully treated as 0.0
    turn_none = fast_path.ingest_live_turn(
        speaker="Architect",
        text="Deploy immediately.",
        confidence=None,  # type: ignore
    )
    assert turn_none["fast_path_admissible"] is False


# ==============================================================================
# Category 3: Concurrency & Race Conditions
# ==============================================================================


def test_concurrent_outcome_recording(sandbox_env):
    """Category 3: Verifies multi-threaded outcome recording across retrieval and decision agents."""
    integrator = sandbox_env["integrator"]
    coordinator = RetrievalCoordinator(
        mock_mode=True,
        parameters_path=sandbox_env["params_path"],
        integrator=integrator,
    )
    retrieval_healer = RetrievalPerspectiveHealerAgent(
        mock_mode=True,
        integrator=integrator,
    )
    briefing_healer = BriefingAndDecisionHealerAgent(
        mock_mode=True,
        integrator=integrator,
    )
    fast_path = FastPathSyncAgent(conviction_threshold=0.85)
    orchestrator = DecisionOrchestrator(
        fast_path_agent=fast_path,
        integrator=integrator,
    )

    def record_retrieval_worker(worker_id: int):
        coordinator.record_retrieval_miss(
            query_text=f"concurrent query {worker_id}",
            missing_requirements=[f"req_{worker_id}"],
            retrieved_count=worker_id,
            run_id=f"run-ret-{worker_id}",
        )
        retrieval_healer.record_perspective_repair(
            starved_lens="TECHNICAL",
            was_successful=True,
            run_id=f"run-rep-{worker_id}",
        )

    def record_decision_worker(worker_id: int):
        briefing_healer.record_briefing_repair(
            defect_type="UNGROUNDED_ASSUMPTION",
            was_successful=True,
            run_id=f"run-dec-rep-{worker_id}",
        )
        orchestrator.record_decision_override(
            decision_id=f"dec-{worker_id}",
            category="strategic_architecture",
            recommended_option=f"OptionA_{worker_id}",
            operator_chosen_option=f"OptionB_{worker_id}",
            run_id=f"run-dec-{worker_id}",
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = []
        for i in range(10):
            futures.append(executor.submit(record_retrieval_worker, i))
            futures.append(executor.submit(record_decision_worker, i))
        for f in concurrent.futures.as_completed(futures):
            f.result()

    # Verify ledger integrity: 40 total events recorded without database locks or corruptions
    log_path = integrator.events_log_path
    assert log_path.is_file()
    with open(log_path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]
    assert len(lines) == 40


# ==============================================================================
# Category 4: Invariant Bypass Attempts
# ==============================================================================


def test_invariant_perspective_weight_clamping():
    """Category 4: Verifies DialecticalPerspectiveAgent strictly clamps role weights to [0.10, 0.45]."""
    agent = DialecticalPerspectiveAgent(perspective=PerspectiveType.RED_TEAM, role_weight=0.25)
    assert agent.role_weight == 0.25

    # 1. Underflow attempt (< 0.10)
    agent.set_role_weight(0.01)
    assert agent.role_weight == 0.10

    agent.set_role_weight(-2.5)
    assert agent.role_weight == 0.10

    # 2. Overflow attempt (> 0.45)
    agent.set_role_weight(0.85)
    assert agent.role_weight == 0.45

    agent.set_role_weight(999.0)
    assert agent.role_weight == 0.45

    # 3. Output payload reflects clamped weight
    task = A2ATask(
        task_id="task-clamp-check",
        parent_task_id=None,
        sender_agent_id="test",
        recipient_agent_id=agent.card.agent_id,
        objective="Verify role weight in assessment",
        status=A2ATaskStatus.SUBMITTED,
        input_payload={
            "decision_question": "Should we switch to WAL mode?",
            "options": [{"id": "1", "label": "WAL"}, {"id": "2", "label": "Rollback"}],
        },
    )
    res = asyncio.run(agent.handle_task(task))
    assert res["status"] == "COMPLETED"
    assert res["risk_assessments"][0]["role_weight"] == 0.45


def test_unverified_fusion_weights_rejection_without_11gates(sandbox_env):
    """Category 4: Verifies candidate fusion weights cannot be qualified without passing 11 release gates."""
    auditor = ReleaseGateAuditor(mock_mode=True)

    # Candidate with broken repeat stability / core thresholds
    candidate_payload = {
        "backend_id": "bge-m3",
        "graph_sha256": "sha-graph-001",
        "sidecar_sha256": "sha-sidecar-001",
        "max_query_latency_ms": 150.0,
        "real_bge_m3_passed": True,
        "bm25_executed": False,  # Missing required component execution
    }
    task = A2ATask(
        task_id="task-eval-unverified",
        parent_task_id=None,
        sender_agent_id="test",
        recipient_agent_id=auditor.card.agent_id,
        objective="Audit candidate pair before activation",
        status=A2ATaskStatus.SUBMITTED,
        input_payload=candidate_payload,
    )
    res = asyncio.run(auditor.handle_task(task))
    assert res["all_gates_passed"] is False
    assert res["qualification_status"] == "BLOCKED"


# ==============================================================================
# Category 5: Rollback & Recovery
# ==============================================================================


def test_parameter_snapshot_and_rollback(sandbox_env):
    """Category 5: Verifies parameter snapshotting, mutation, and bit-exact rollback."""
    params_path = sandbox_env["params_path"]

    # 1. Take snapshot and compute baseline SHA-256
    with open(params_path, "r", encoding="utf-8") as f:
        baseline_content = f.read()
    baseline_sha = hashlib.sha256(baseline_content.encode("utf-8")).hexdigest()
    baseline_data = json.loads(baseline_content)

    # 2. Mutate parameters (tighten conviction threshold and shift fusion weights)
    mutated_data = dict(baseline_data)
    mutated_data["parameters"]["decision"]["fast_path_conviction_threshold"] = 0.95
    mutated_data["parameters"]["retrieval"]["intent_fusion_weights"]["TECHNICAL_ARCHITECTURE"] = {
        "dense": 0.75,
        "sparse": 0.15,
        "colbert": 0.10,
    }

    with open(params_path, "w", encoding="utf-8") as f:
        json.dump(mutated_data, f, indent=2)

    # Verify mutation took effect
    cognition = L1QueryCognition(parameters_path=params_path)
    mutated_plan = cognition.plan(
        query="What is the system architecture of BGE-M3 vector index?",
        metadata={"bge_release_gate_passed": True},
    )
    assert mutated_plan.hybrid_weights["dense"] == 0.75

    # 3. Perform rollback to snapshot
    with open(params_path, "w", encoding="utf-8") as f:
        f.write(baseline_content)

    # 4. Verify rollback restored bit-exact content
    with open(params_path, "r", encoding="utf-8") as f:
        restored_content = f.read()
    restored_sha = hashlib.sha256(restored_content.encode("utf-8")).hexdigest()
    assert restored_sha == baseline_sha

    restored_cognition = L1QueryCognition(parameters_path=params_path)
    restored_plan = restored_cognition.plan(
        query="What is the system architecture of BGE-M3 vector index?",
        metadata={"bge_release_gate_passed": True},
    )
    assert restored_plan.hybrid_weights["dense"] == 0.45
    assert restored_plan.hybrid_weights["sparse"] == 0.35
