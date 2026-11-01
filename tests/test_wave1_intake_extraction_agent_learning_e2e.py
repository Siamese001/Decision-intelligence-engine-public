"""
tests/test_wave1_intake_extraction_agent_learning_e2e.py

End-to-end lifecycle verification for Wave 1: Intake & Extraction Agent System Learning.
Tests:
1. RosterReconcilerAgent learned alias resolution and outcome event emission.
2. Operator correction ingestion via CorrectionFeedbackIngestor with multi-confirmation cohort gating (>= 2).
3. AudioForensicsAgent acoustic prior consumption and repair event logging.
4. ForensicTranscriptHealerAgent learned speaker prior integration and repair telemetry.
5. AdjudicationJudgeAgent contradiction detection and ADJUDICATION_ERROR outcome logging.
6. L1ExtractionCognition adaptive chunk routing weight calibration.
7. CanonicalProjectionHealerAgent repair outcome logging.
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
from dual_pipeline.learning_adapters.correction_feedback_ingestor import (
    CorrectionFeedbackIngestor,
    OperatorCorrection,
)
from dual_pipeline.learning_adapters.live_pipeline_integrator import (
    LivePipelineIntegrator,
)
from dual_pipeline.transcript_metadata_core.speaker_priors_registry import (
    load_acoustic_priors,
    load_extractor_routing_weights,
    load_learned_speaker_priors,
    register_learned_speaker_alias,
    update_extractor_routing_weights,
)
from pipeline_a_intake.agents.audio_forensics_agent import AudioForensicsAgent
from pipeline_a_intake.agents.forensic_transcript_healer import (
    ForensicTranscriptHealerAgent,
)
from pipeline_a_intake.agents.roster_reconciler_agent import RosterReconcilerAgent
from pipeline_b_extraction.agents.adjudication_judge_agent import (
    AdjudicationJudgeAgent,
)
from pipeline_b_extraction.agents.canonical_projection_healer import (
    CanonicalProjectionHealerAgent,
)
from pipeline_b_extraction.agents.extraction_coordinator import (
    ExtractionCoordinator,
)
from pipeline_b_extraction.agents.l1_extraction_cognition import (
    L1ExtractionCognition,
)


@pytest.fixture
def sandbox_env():
    """Provides isolated temporary sandbox directory for ledger and runtime configs."""
    with tempfile.TemporaryDirectory(prefix="wave1_e2e_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir(parents=True, exist_ok=True)
        config_dir = tmp_path / "config" / "runtime"
        config_dir.mkdir(parents=True, exist_ok=True)

        priors_path = config_dir / "learned_speaker_priors.json"
        initial_data = {
            "schema_version": "1.0.0",
            "updated_at": "2026-10-22T00:00:00Z",
            "description": "Sandbox learned speaker priors.",
            "speaker_aliases": {
                "Speaker 1": "Alex Sanchez",
                "Speaker 2": "harper Hoffman",
            },
            "acoustic_priors": {
                "min_snr_db": 15.0,
                "max_speech_rate_wpm": 200,
                "clipping_threshold_db": -1.0,
            },
            "extractor_routing_weights": {
                "TECH_ARCH": 1.2,
                "STRATEGIC_COMMITMENTS": 0.8,
                "STAKEHOLDER_DYNAMICS": 1.0,
                "FRICTION_BLOCKER": 1.1,
            },
        }
        with open(priors_path, "w", encoding="utf-8") as f:
            json.dump(initial_data, f, indent=2)

        integrator = LivePipelineIntegrator(runtime_root=tmp_path)
        yield {
            "root": tmp_path,
            "priors_path": priors_path,
            "integrator": integrator,
        }


def test_roster_reconciler_learned_alias_and_correction_flow(sandbox_env):
    """Verifies that RosterReconciler resolves from learned priors and emits correction events."""
    priors_path = sandbox_env["priors_path"]
    integrator = sandbox_env["integrator"]

    agent = RosterReconcilerAgent(
        mock_mode=True,
        integrator=integrator,
        priors_path=priors_path,
    )

    # 1. Direct resolution from learned speaker prior
    task = A2ATask(
        task_id="task-roster-001",
        parent_task_id=None,
        sender_agent_id="urn:nee:agent:test",
        recipient_agent_id=agent.card.agent_id,
        objective="Resolve known prior speaker",
        status=A2ATaskStatus.SUBMITTED,
        input_payload={
            "speaker_turns": [
                {"speaker": "Speaker 1", "text": "Hello world"},
                {"speaker": "Unknown Voice", "text": "Testing testing"},
            ],
            "roster_context": ["Alex Sanchez", "harper Hoffman", "Tyler Davis"],
        },
    )

    res = asyncio.run(agent.handle_task(task))
    assert res["status"] == "COMPLETED"
    reconciled = res["reconciled_turns"]
    assert reconciled[0]["speaker"] == "Alex Sanchez"
    assert reconciled[0]["identity_confidence"] == 0.98

    # 2. Operator correction recording
    evt = agent.record_operator_correction(
        assigned_speaker="Unknown Voice",
        confirmed_speaker="Tyler Davis",
        run_id="task-roster-001",
        register_alias=True,
    )
    assert evt is not None
    assert evt.outcome_type == OutcomeType.SPEAKER_CORRECTION
    assert evt.observed_outcome["assigned_speaker"] == "Unknown Voice"
    assert evt.expected_or_corrected_outcome["confirmed_speaker"] == "Tyler Davis"

    # Verify registered alias persists into priors_path
    priors = load_learned_speaker_priors(priors_path)
    assert priors["speaker_aliases"]["Unknown Voice"] == "Tyler Davis"


def test_correction_feedback_ingestor_multi_confirmation(sandbox_env):
    """Verifies multi-confirmation (>= 2) cohort threshold for operator corrections."""
    root = sandbox_env["root"]
    ingestor = CorrectionFeedbackIngestor(runtime_root=root, min_confirmations=2)

    # First correction: single sample, not yet confirmed cohort
    ingestor.record_speaker_correction(
        raw_speaker="Speaker 3",
        corrected_speaker="Cansu Cimen",
        operator_id="operator-alice",
        session_id="session-001",
    )
    cohorts1 = ingestor.aggregate_cohorts()
    assert len(cohorts1) == 0  # Below threshold 2

    # Second correction from another session: triggers confirmed cohort
    ingestor.record_speaker_correction(
        raw_speaker="Speaker 3",
        corrected_speaker="Cansu Cimen",
        operator_id="operator-bob",
        session_id="session-002",
    )
    cohorts2 = ingestor.aggregate_cohorts()
    assert len(cohorts2) == 1
    assert cohorts2[0].confirmations == 2
    assert cohorts2[0].target_identifier == "Cansu Cimen"


def test_audio_forensics_prior_and_repair_telemetry(sandbox_env):
    """Verifies AudioForensicsAgent applies acoustic priors and logs repair events."""
    priors_path = sandbox_env["priors_path"]
    integrator = sandbox_env["integrator"]

    agent = AudioForensicsAgent(mock_mode=True, integrator=integrator, priors_path=priors_path)
    task = A2ATask(
        task_id="task-audio-001",
        parent_task_id=None,
        sender_agent_id="urn:nee:agent:test",
        recipient_agent_id=agent.card.agent_id,
        objective="Run acoustic segmentation",
        status=A2ATaskStatus.SUBMITTED,
        input_payload={"raw_transcript_text": "Alex Sanchez 01:23\nLet us begin the review."},
    )

    res = asyncio.run(agent.handle_task(task))
    assert res["status"] == "COMPLETED"
    assert res["min_snr_db"] == 15.0

    repair_evt = agent.record_acoustic_repair(
        defect_message="Clipped waveform normalized",
        was_successful=True,
        run_id="task-audio-001",
    )
    assert repair_evt is not None
    assert repair_evt.outcome_type == OutcomeType.COMPONENT_REPAIR
    assert repair_evt.observed_outcome["was_successful"] is True


def test_forensic_transcript_healer_prior_and_repair(sandbox_env):
    """Verifies ForensicTranscriptHealerAgent loads learned priors and logs repairs."""
    priors_path = sandbox_env["priors_path"]
    integrator = sandbox_env["integrator"]

    agent = ForensicTranscriptHealerAgent(
        mock_mode=True,
        integrator=integrator,
        priors_path=priors_path,
    )

    res = asyncio.run(agent.heal_transcript(
        transcript_md="### Turn 1: Speaker 1\nWe have completed the ingestion pipeline.",
        failure_reasons=["GENERIC_SPEAKER_TAG"],
        roster_attendees=["Tyler Davis"],
        run_id="run-heal-001",
    ))
    assert res.is_success is True

    events = integrator.get_recorded_events()
    repair_events = [e for e in events if e.outcome_type == OutcomeType.COMPONENT_REPAIR]
    assert len(repair_events) >= 1
    assert repair_events[-1].source_agent == "ForensicTranscriptHealerAgent"


def test_adjudication_judge_and_extraction_cognition(sandbox_env):
    """Verifies AdjudicationJudge contradiction detection and L1ExtractionCognition adaptive weights."""
    priors_path = sandbox_env["priors_path"]
    integrator = sandbox_env["integrator"]

    judge = AdjudicationJudgeAgent(mock_mode=True, integrator=integrator)
    task = A2ATask(
        task_id="task-adj-001",
        parent_task_id=None,
        sender_agent_id="urn:nee:agent:test",
        recipient_agent_id=judge.card.agent_id,
        objective="Adjudicate duplicate and contradictory facts",
        status=A2ATaskStatus.SUBMITTED,
        input_payload={
            "canonical_nodes": [
                {"id": "node-1", "title": "BGE-M3 Target", "claim": "Target latency is 50ms"},
                {"id": "node-2", "title": "BGE-M3 Target", "claim": "Target latency is 200ms"},
            ],
            "advisory_nodes": [],
        },
    )

    res = asyncio.run(judge.handle_task(task))
    assert res["status"] == "COMPLETED"
    assert res["quarantined_count"] == 1
    assert res["adjudicated_canonical"][1]["is_quarantined"] is True
    assert res["adjudicated_canonical"][1]["quarantine_reason"] == "CONTRADICTORY_CLAIM_FLAGGED"

    events = integrator.get_recorded_events()
    adj_events = [e for e in events if e.outcome_type == OutcomeType.ADJUDICATION_ERROR]
    assert len(adj_events) >= 1
    assert "BGE-M3 Target" in adj_events[-1].observed_outcome.get("failure_reason", "")

    # Test L1ExtractionCognition adaptive routing
    cognition = L1ExtractionCognition(priors_path=priors_path)
    plan = cognition.plan(
        evidence_id="ev-test",
        text="The database architecture requires api service cloud model scaling.",
    )
    # TECH_ARCH weight is 1.2 in our test priors
    assert plan.domain_relevance["TECH_ARCH"] > 0.0

    # Update routing weights and verify plan reflects update
    update_extractor_routing_weights({"TECH_ARCH": 0.5}, priors_path=priors_path)
    plan_updated = cognition.plan(
        evidence_id="ev-test-2",
        text="The database architecture requires api service cloud model scaling.",
    )
    assert plan_updated.domain_relevance["TECH_ARCH"] < plan.domain_relevance["TECH_ARCH"]


def test_canonical_projection_healer_outcome(sandbox_env):
    """Verifies CanonicalProjectionHealerAgent emits COMPONENT_REPAIR events."""
    integrator = sandbox_env["integrator"]
    healer = CanonicalProjectionHealerAgent(mock_mode=True, integrator=integrator)

    res = asyncio.run(healer.heal_projection(
        candidate_projection={"nodes": [], "edges": []},
        failure_reasons=["EMPTY_GRAPH_PROJECTION"],
        run_id="run-proj-heal-001",
    ))
    assert res.is_success is True

    events = integrator.get_recorded_events()
    repair_events = [e for e in events if e.source_agent == "CanonicalProjectionHealerAgent"]
    assert len(repair_events) >= 1
    assert repair_events[-1].outcome_type == OutcomeType.COMPONENT_REPAIR
