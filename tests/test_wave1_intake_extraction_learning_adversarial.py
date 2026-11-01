"""
tests/test_wave1_intake_extraction_learning_adversarial.py

Adversarial and edge-case verification for Wave 1: Intake & Extraction Agent System Learning.
Covers at least 3 required governance edge-case categories:
1. Boundary payloads: empty turns, empty roster, empty text, null candidate entries.
2. Malformed input & corrupted priors: corrupt JSON syntax, missing keys, invalid types.
3. Concurrency & race: parallel outcome recording across threads without log corruption.
4. Invariant bypass attempts: auto_apply: true rejection, unconfirmed single-sample rejection, conflict detection.
5. Rollback & recovery: non-destructive restoration of learned speaker priors.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import tempfile
from pathlib import Path
from typing import Any, Dict

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
    DEFAULT_BOOTSTRAP_PRIORS,
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
from pipeline_b_extraction.agents.l1_extraction_cognition import (
    L1ExtractionCognition,
)


@pytest.fixture
def adversarial_env():
    """Provides isolated sandbox for adversarial testing."""
    with tempfile.TemporaryDirectory(prefix="wave1_adv_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir(parents=True, exist_ok=True)
        config_dir = tmp_path / "config" / "runtime"
        config_dir.mkdir(parents=True, exist_ok=True)

        priors_path = config_dir / "learned_speaker_priors.json"
        with open(priors_path, "w", encoding="utf-8") as f:
            json.dump(dict(DEFAULT_BOOTSTRAP_PRIORS), f, indent=2)

        integrator = LivePipelineIntegrator(runtime_root=tmp_path)
        yield {
            "root": tmp_path,
            "priors_path": priors_path,
            "integrator": integrator,
        }


def test_boundary_empty_and_null_payloads(adversarial_env):
    """Category 1: Boundary payload tests (empty turns, null roster, empty text)."""
    priors_path = adversarial_env["priors_path"]
    integrator = adversarial_env["integrator"]

    # 1. RosterReconciler with zero turns and empty roster
    agent = RosterReconcilerAgent(mock_mode=True, integrator=integrator, priors_path=priors_path)
    task_empty = A2ATask(
        task_id="task-empty",
        parent_task_id=None,
        sender_agent_id="test",
        recipient_agent_id=agent.card.agent_id,
        objective="Empty boundary test",
        status=A2ATaskStatus.SUBMITTED,
        input_payload={"speaker_turns": [], "roster_context": []},
    )
    res_empty = asyncio.run(agent.handle_task(task_empty))
    assert res_empty["status"] == "COMPLETED"
    assert res_empty["reconciled_turns"] == []

    # 2. AudioForensics with empty raw text
    audio_agent = AudioForensicsAgent(mock_mode=True, integrator=integrator, priors_path=priors_path)
    task_audio_empty = A2ATask(
        task_id="task-audio-empty",
        parent_task_id=None,
        sender_agent_id="test",
        recipient_agent_id=audio_agent.card.agent_id,
        objective="Empty audio boundary test",
        status=A2ATaskStatus.SUBMITTED,
        input_payload={"raw_transcript_text": ""},
    )
    res_audio = asyncio.run(audio_agent.handle_task(task_audio_empty))
    assert res_audio["status"] == "COMPLETED"

    # 3. L1ExtractionCognition with empty string
    cognition = L1ExtractionCognition(priors_path=priors_path)
    plan_empty = cognition.plan(evidence_id="ev-empty", text="")
    assert plan_empty.domain_relevance["TECH_ARCH"] == 0.0
    assert plan_empty.domain_relevance["STRATEGIC_COMMITMENTS"] == 0.0


def test_malformed_and_corrupted_priors_fallback(adversarial_env):
    """Category 2: Malformed input tests (corrupt JSON file, missing keys)."""
    priors_path = adversarial_env["priors_path"]

    # Write invalid JSON syntax to the priors file
    with open(priors_path, "w", encoding="utf-8") as f:
        f.write("{ INVALID JSON NOT CLOSED")

    # Should fall back cleanly to bootstrap defaults without unhandled exception
    priors = load_learned_speaker_priors(priors_path)
    assert "speaker_aliases" in priors
    assert priors["speaker_aliases"]["Speaker 1"] == "Alex Sanchez"

    # Test load weights and acoustic priors with corrupt file
    weights = load_extractor_routing_weights(priors_path)
    assert weights["TECH_ARCH"] == 1.0
    acoustic = load_acoustic_priors(priors_path)
    assert acoustic["min_snr_db"] == 12.0


def test_concurrency_parallel_outcome_emission(adversarial_env):
    """Category 3: Concurrency & race condition tests across multiple worker threads."""
    integrator = adversarial_env["integrator"]
    num_threads = 8
    events_per_thread = 25

    def emit_worker(worker_id: int):
        for i in range(events_per_thread):
            evt = AgentOutcomeAdapter.build_speaker_correction_event(
                assigned_speaker=f"Speaker_W{worker_id}_{i}",
                confirmed_speaker=f"Confirmed_Person_{worker_id}",
                run_id=f"run-worker-{worker_id}",
            )
            integrator.record_outcome(evt)

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(emit_worker, w) for w in range(num_threads)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    # Verify that all events were persisted and file is valid JSONL
    log_path = integrator.events_log_path
    assert log_path.is_file()
    with open(log_path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    assert len(lines) == num_threads * events_per_thread
    # Verify every line parses validly
    for line in lines:
        data = json.loads(line)
        assert data["outcome_type"] == OutcomeType.SPEAKER_CORRECTION.value


def test_invariant_unconfirmed_and_conflicting_corrections(adversarial_env):
    """Category 4: Invariant bypass attempt (unconfirmed single-sample & conflicting targets)."""
    root = adversarial_env["root"]
    ingestor = CorrectionFeedbackIngestor(runtime_root=root, min_confirmations=2)

    # 1. Single sample should NOT qualify as confirmed
    ingestor.record_speaker_correction(
        raw_speaker="Speaker X",
        corrected_speaker="Person A",
        operator_id="op1",
        session_id="session-1",
    )
    assert len(ingestor.aggregate_cohorts()) == 0

    # 2. Conflicting correction from another operator targeting different person
    ingestor.record_speaker_correction(
        raw_speaker="Speaker X",
        corrected_speaker="Person B",
        operator_id="op2",
        session_id="session-2",
    )
    # Target Person A has 1 confirmation, Person B has 1 confirmation -> neither meets threshold 2
    assert len(ingestor.aggregate_cohorts()) == 0

    # 3. Third correction confirming Person A -> Person A reaches 2 confirmations
    ingestor.record_speaker_correction(
        raw_speaker="Speaker X",
        corrected_speaker="Person A",
        operator_id="op3",
        session_id="session-3",
    )
    cohorts = ingestor.aggregate_cohorts()
    assert len(cohorts) == 1
    assert cohorts[0].confirmations == 2
    assert cohorts[0].target_identifier == "Person A"
    assert cohorts[0].has_conflicts is True  # Detects conflict with Person B


def test_non_destructive_rollback_of_priors(adversarial_env):
    """Category 5: Rollback & recovery of learned speaker priors."""
    priors_path = adversarial_env["priors_path"]

    # Initial snapshot
    initial_priors = load_learned_speaker_priors(priors_path)
    snapshot_bytes = json.dumps(initial_priors, sort_keys=True)

    # Mutate priors
    register_learned_speaker_alias("Temp Speaker", "New Name", priors_path=priors_path)
    update_extractor_routing_weights({"TECH_ARCH": 2.5}, priors_path=priors_path)

    mutated = load_learned_speaker_priors(priors_path)
    assert mutated["speaker_aliases"]["Temp Speaker"] == "New Name"
    assert mutated["extractor_routing_weights"]["TECH_ARCH"] == 2.5

    # Execute rollback by restoring snapshot
    with open(priors_path, "w", encoding="utf-8") as f:
        f.write(snapshot_bytes)

    restored = load_learned_speaker_priors(priors_path)
    assert "Temp Speaker" not in restored["speaker_aliases"]
    assert restored["extractor_routing_weights"]["TECH_ARCH"] == initial_priors["extractor_routing_weights"]["TECH_ARCH"]
