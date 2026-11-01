#!/usr/bin/env python3
"""
End-to-End System Hardening & Integration Test Suite.
Dual-Artery Enterprise Hybrid Architecture (Apple MacBook Pro 16" M5 Pro, 48GB RAM).

Validates the complete lifecycle across all 4 phases:
  Phase 1: WAL Storage & Durable Queue (`storage/database.py`)
  Phase 2: Local STT & Fast Path Intake (`pipeline_a_intake/local_stt.py`)
  Phase 3: Compute Fork & Slow Path Worker (`pipeline_b_extraction/`)
  Phase 4: Policy-Gated Retrieval & Copilot Fusion (`pipeline_c_retrieval.py`)

Validates the 7 Non-Negotiable Engineering Laws (P0):
  1. Compute Fork BEFORE Extraction (Independent cloud vs local execution)
  2. Purged Agentic Leverage -> Governed INTERNAL_ADVISORY
  3. Untrusted Fast Path (Ambient live context marked ephemeral & unverified)
  4. Immutable Provenance First (SQLite WAL log + queue checkpoints)
  5. Multi-Dimensional Soft-Quarantine (Tactical suppression without data loss)
  6. Policy Before Retrieval (Hard SQL boundary before ranking)
  7. Correct Pipeline Sequencing (Evidence -> Graph -> Recap)
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import List

import numpy as np
import pytest

from pipeline_a_intake.local_stt import (
    AudioChunk,
    FastPathTranscriptEvent,
    LocalSTTEngine,
)
from pipeline_b_extraction.extractors import (
    ClientSafeExtractor,
    ComputeForkCoordinator,
    InternalAdvisoryExtractor,
)
from pipeline_b_extraction.judges import QualityJudge, StealthJudge
from pipeline_b_extraction.worker import SlowPathWorker, WorkerConfig
from pipeline_c_retrieval import (
    CLIENT_SAFE_NAMESPACE,
    INTERNAL_ADVISORY_NAMESPACE,
    CopilotFusionEngine,
    PolicyGatedRetrievalEngine,
)
from storage.database import (
    AdvisoryCategory,
    CanonicalGraphNode,
    DatabaseManager,
    EvidenceRecord,
    InternalAdvisoryNode,
    WorkItemState,
)


@pytest.fixture
def test_rig():
    """Provisions an isolated hardware-simulated runtime environment."""
    temp_dir = Path(tempfile.mkdtemp())
    db_mgr = DatabaseManager(base_dir=temp_dir)
    stt_engine = LocalSTTEngine(db_manager=db_mgr, mock_mode=True)
    fork_coord = ComputeForkCoordinator(
        client_safe_extractor=ClientSafeExtractor(mock_mode=True),
        internal_advisory_extractor=InternalAdvisoryExtractor(mock_mode=True),
    )
    worker = SlowPathWorker(
        db_manager=db_mgr,
        fork_coordinator=fork_coord,
        quality_judge=QualityJudge(),
        stealth_judge=StealthJudge(),
        config=WorkerConfig(batch_size=10, poll_interval_seconds=0.05, max_retries=3),
    )
    retrieval_engine = PolicyGatedRetrievalEngine(db_manager=db_mgr)
    fusion_engine = CopilotFusionEngine(retrieval_engine=retrieval_engine)

    yield {
        "dir": temp_dir,
        "db": db_mgr,
        "stt": stt_engine,
        "worker": worker,
        "retrieval": retrieval_engine,
        "fusion": fusion_engine,
    }

    shutil.rmtree(temp_dir, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Complete End-to-End Lifecycle: Audio Ingestion -> Graph -> Copilot Fusion
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_end_to_end_multimodal_lifecycle(test_rig):
    """
    Simulates a live executive meeting:
      1. Speaker speaks into microphone (Phase 2 Fast Path).
      2. Non-blocking audio ingestion writes to WAL evidence log (Phase 1).
      3. Slow-path worker consumes queue and executes compute fork (Phase 3).
      4. Quality & Stealth judges tag nodes (Law 5 soft quarantine).
      5. Policy-gated retrieval searches graph and Copilot synthesizes answer (Phase 4).
    """
    db = test_rig["db"]
    stt = test_rig["stt"]
    worker = test_rig["worker"]
    fusion = test_rig["fusion"]
    retrieval = test_rig["retrieval"]

    fast_path_events: List[FastPathTranscriptEvent] = []
    stt.subscribe(lambda ev: fast_path_events.append(ev))

    await stt.start()
    try:
        # Utterance 1: High-level architectural decision
        chunk1 = AudioChunk(
            chunk_id="chunk_arch_01",
            meeting_id="exec_summit_2026",
            audio_data=np.zeros(16000, dtype=np.float32),
            speaker="Amit Ayer",
            timestamp_offset=0.0,
            metadata={"mock_text": "We have finalized the dual-artery architecture with WAL storage to eliminate biometric leakage."}
        )
        # Utterance 2: Tactical to-do task (Should be soft-quarantined)
        chunk2 = AudioChunk(
            chunk_id="chunk_tact_02",
            meeting_id="exec_summit_2026",
            audio_data=np.zeros(16000, dtype=np.float32),
            speaker="Delivery Lead",
            timestamp_offset=6.0,
            metadata={"mock_text": "Please schedule calendar invite for the daily standup follow-up meeting."}
        )
        # Utterance 3: Strategic advisory risk
        chunk3 = AudioChunk(
            chunk_id="chunk_adv_03",
            meeting_id="exec_summit_2026",
            audio_data=np.zeros(16000, dtype=np.float32),
            speaker="Executive Sponsor",
            timestamp_offset=12.0,
            metadata={"mock_text": "CTO sponsors the zero data retention policy, but VP expresses migration friction risk."}
        )

        # Feed audio chunks into Fast Path
        stt.feed_audio(chunk1)
        stt.feed_audio(chunk2)
        stt.feed_audio(chunk3)

        # Gracefully drain Fast Path STT queue
        await stt.stop()

        # 1. Verify Fast Path (Law 3: Ephemeral & Read-Only)
        assert len(fast_path_events) == 3
        assert all(ev.is_ephemeral is True for ev in fast_path_events)
        assert fast_path_events[0].speaker == "Amit Ayer"

        # 2. Verify WAL Evidence Persistence (Law 4)
        with db.get_evidence_conn() as conn:
            evidence_count = conn.execute("SELECT count(*) FROM immutable_evidence_log").fetchone()[0]
            queue_count = conn.execute("SELECT count(*) FROM durable_work_queue WHERE state = 'PENDING'").fetchone()[0]
            assert evidence_count == 3
            assert queue_count == 3

        # 3. Execute Slow Path Worker (Phase 3)
        processed = await worker.run_once()
        assert processed == 3

        # Verify durable work queue items are all COMPLETED with checkpoints
        with db.get_evidence_conn() as conn:
            completed_count = conn.execute(
                "SELECT count(*) FROM durable_work_queue WHERE state = 'COMPLETED' AND checkpoint IS NOT NULL"
            ).fetchone()[0]
            assert completed_count == 3

        # 4. Verify Canonical & Advisory Nodes in Separate DBs
        canonical_nodes = db.query_canonical_nodes(include_quarantined=True)
        assert len(canonical_nodes) >= 2

        advisory_nodes = db.query_internal_advisory_nodes(include_quarantined=True)
        assert len(advisory_nodes) >= 1

        # 5. Verify Soft-Quarantine (Law 5): Tactical item suppressed from search
        visible_canonical = db.query_canonical_nodes(include_quarantined=False)
        quarantined_canonical = [n for n in canonical_nodes if n.is_quarantined]
        assert len(quarantined_canonical) >= 1
        assert "TACTICAL_SUPPRESSION" in (quarantined_canonical[0].quarantine_reason or "")
        # Tactical node is preserved in the DB for auditability
        assert any("schedule" in n.name.lower() or "standup" in n.name.lower() or "follow-up" in n.name.lower() for n in quarantined_canonical)
        # But excluded from visible search!
        assert not any(n.node_id == quarantined_canonical[0].node_id for n in visible_canonical)

        # 6. Verify Policy Before Retrieval (Law 6):
        # A) In Stealth Mode: ONLY CLIENT_SAFE
        stealth_results = retrieval.search(query="architecture", stealth_mode=True)
        assert len(stealth_results) >= 1
        assert all(r.namespace == CLIENT_SAFE_NAMESPACE for r in stealth_results)

        # B) In Advisory Mode: Unmasked view
        unmasked_results = retrieval.search(query="", stealth_mode=False)
        namespaces = {r.namespace for r in unmasked_results}
        assert CLIENT_SAFE_NAMESPACE in namespaces
        assert INTERNAL_ADVISORY_NAMESPACE in namespaces

        # 7. Verify Copilot Context Fusion (Phase 4):
        payload = fusion.fuse_context(
            query="architecture",
            live_fast_path_events=[ev.to_dict() for ev in fast_path_events],
            stealth_mode=True,
        )
        assert payload.policy_check_passed is True
        assert len(payload.reconciled_canonical_facts) >= 1
        assert len(payload.reconciled_advisory_facts) == 0  # Stealth Mode protects advisory
        assert len(payload.fast_path_context) == 3
        assert "Ambient Fast-Path (Unverified)" in payload.executive_synthesis
        assert "Settled Architecture Baseline" in payload.executive_synthesis

    finally:
        if stt._is_running:
            await stt.stop()


# ─────────────────────────────────────────────────────────────────────────────
# 2. Concurrency & Stress Testing (Non-blocking Fast Path under WAL contention)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_wal_concurrency_and_stress(test_rig):
    """
    Stress tests high-frequency concurrent audio chunk feeding and slow-path
    worker polling under SQLite WAL mode to ensure zero database lock errors.
    """
    db = test_rig["db"]
    stt = test_rig["stt"]
    worker = test_rig["worker"]

    await stt.start()
    try:
        # Rapidly feed 50 audio chunks
        num_chunks = 50
        for i in range(num_chunks):
            stt.feed_audio(AudioChunk(
                chunk_id=f"chunk_stress_{i}",
                meeting_id="meet_stress",
                audio_data=np.zeros(1600, dtype=np.float32),
                speaker=f"Speaker_{i % 3}",
                timestamp_offset=float(i * 2),
                metadata={"mock_text": f"Architecture stress payload {i}"}
            ))

        # Simultaneously run worker cycles concurrently with queue draining
        async def run_workers_concurrently():
            total_processed = 0
            while total_processed < num_chunks:
                count = await worker.run_once()
                total_processed += count
                await asyncio.sleep(0.01)
            return total_processed

        worker_task = asyncio.create_task(run_workers_concurrently())

        # Drain STT
        await stt.stop()

        # Wait for all 50 items to be processed by workers
        total_processed = await asyncio.wait_for(worker_task, timeout=10.0)
        assert total_processed == num_chunks

        # Verify all items in queue are COMPLETED
        with db.get_evidence_conn() as conn:
            pending = conn.execute("SELECT count(*) FROM durable_work_queue WHERE state != 'COMPLETED'").fetchone()[0]
            assert pending == 0

    finally:
        if stt._is_running:
            await stt.stop()


# ─────────────────────────────────────────────────────────────────────────────
# 3. Security Hardening: Anti-Leakage & Adversarial Scope Verification
# ─────────────────────────────────────────────────────────────────────────────

def test_zero_leakage_adversarial_injection(test_rig):
    """
    Law 6 Security Guard: Verifies that simulated prompt injection or malicious
    node namespace alteration cannot bypass the stealth boundary.
    """
    db = test_rig["db"]
    retrieval = test_rig["retrieval"]

    # Insert malicious node that pretends to be CLIENT_SAFE but has stealth data
    leak_node = InternalAdvisoryNode(
        node_id="adv_leak_01",
        evidence_id="ev_leak",
        type="BLOCKER",
        name="Confidential Executive Reorg Blocker",
        category=AdvisoryCategory.BLOCKER,
        properties={"signal": "Confidential budget cut"},
        strategic_value=9.5,
        stealth_score=1.0,
        namespace=INTERNAL_ADVISORY_NAMESPACE,
    )
    db.upsert_internal_advisory_node(leak_node)

    # Search in Stealth Mode
    results = retrieval.search(query="Confidential", stealth_mode=True)
    # The advisory node must NEVER be returned in stealth mode
    assert len(results) == 0

    # Explicitly test the leak guard assertion by manually injecting an item into the scored list
    with pytest.raises(PermissionError, match="CRITICAL SECURITY VIOLATION"):
        # Simulate an internal driver error where an advisory fact leaked into stealth search
        retrieval.db_manager.query_canonical_nodes = lambda **kwargs: [
            CanonicalGraphNode(
                node_id="corrupt_node",
                evidence_id="ev_corrupt",
                type="BLOCKER",
                name="Corrupted Leak",
                namespace=INTERNAL_ADVISORY_NAMESPACE,  # Illegal namespace in canonical store
            )
        ]
        retrieval.search(query="", stealth_mode=True)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Fault Tolerance: Worker Failure & Dead-Letter Queue
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_worker_failure_and_dead_letter_escalation(test_rig):
    """
    Verifies that worker exceptions properly increment retry counts and move
    permanently broken tasks to DEAD_LETTER status without stalling the pipeline.
    """
    db = test_rig["db"]
    worker = test_rig["worker"]

    ev = EvidenceRecord(
        evidence_id="ev_poison_pill",
        meeting_id="meet_error",
        speaker="Faulty Speaker",
        text="Crash on this text",
        start_timestamp=0.0
    )
    event_id = db.append_evidence(ev)

    # Inject failure into coordinator
    async def failing_extract(evidence):
        raise ValueError("Simulated network timeout or GPU OOM during extraction")

    worker.fork_coordinator.execute_fork = failing_extract

    # Run 1: Retry 1 -> PENDING
    await worker.run_once()
    with db.get_evidence_conn() as conn:
        r1 = conn.execute("SELECT state, retry_count FROM durable_work_queue WHERE event_id = ?", (event_id,)).fetchone()
        assert r1["state"] == WorkItemState.PENDING.value
        assert r1["retry_count"] == 1

    # Run 2: Retry 2 -> PENDING
    await worker.run_once()
    with db.get_evidence_conn() as conn:
        r2 = conn.execute("SELECT state, retry_count FROM durable_work_queue WHERE event_id = ?", (event_id,)).fetchone()
        assert r2["state"] == WorkItemState.PENDING.value
        assert r2["retry_count"] == 2

    # Run 3: Retry 3 >= max_retries (3) -> DEAD_LETTER
    await worker.run_once()
    with db.get_evidence_conn() as conn:
        r3 = conn.execute("SELECT state, retry_count, error_message FROM durable_work_queue WHERE event_id = ?", (event_id,)).fetchone()
        assert r3["state"] == WorkItemState.DEAD_LETTER.value
        assert r3["retry_count"] == 3
        assert "Simulated network timeout" in r3["error_message"]

    # Run 4: Worker finds 0 pending items (Pipeline does not stall!)
    count = await worker.run_once()
    assert count == 0


# ─────────────────────────────────────────────────────────────────────────────
# 5. Multi-Dimensional Soft-Quarantine Matrix (Law 5 Hardening)
# ─────────────────────────────────────────────────────────────────────────────

def test_soft_quarantine_multi_dimensional_matrix(test_rig):
    """
    Law 5 Hardening: Exhaustively evaluates all 4 dimensions of soft quarantine:
      a) High tacticality -> TACTICAL_SUPPRESSION
      b) Ungrounded claim -> UNGROUNDED_EVIDENCE
      c) Low strategic value in advisory -> LOW_STRATEGIC_VALUE
      d) High strategic architectural decision -> APPROVED (Visible)
    """
    quality_judge = QualityJudge()
    stealth_judge = StealthJudge()

    evidence = EvidenceRecord(
        evidence_id="ev_matrix_01",
        meeting_id="meet_matrix",
        speaker="Staff Architect",
        text="We selected SQLite WAL mode and Metal acceleration for our zero-latency real-time copilot.",
        start_timestamp=0.0,
    )

    # 1. High Strategic Node (Grounded in evidence)
    good_node = CanonicalGraphNode(
        node_id="c_arch_01",
        evidence_id=evidence.evidence_id,
        type="ARCHITECTURAL_DECISION",
        name="SQLite WAL Mode Selection",
        strategic_value=8.5,
        tacticality=1.0,
        properties={"context": "Selected SQLite WAL mode for zero-latency pipeline"},
    )
    judged_good = quality_judge.judge_node(good_node, evidence)
    assert judged_good.is_quarantined is False
    assert judged_good.strategic_value >= 6.0
    assert judged_good.quarantine_reason is None

    # 2. Overtly Tactical Node (Low strategic value)
    tactical_node = CanonicalGraphNode(
        node_id="c_tact_02",
        evidence_id=evidence.evidence_id,
        type="OPERATIONAL_TODO",
        name="Send calendar invite for retrospective",
        strategic_value=1.5,
        tacticality=9.0,
        properties={"context": "Send calendar invite"},
    )
    judged_tactical = quality_judge.judge_node(tactical_node, evidence)
    assert judged_tactical.is_quarantined is True
    assert "TACTICAL_SUPPRESSION" in (judged_tactical.quarantine_reason or "")
    assert judged_tactical.tacticality >= 7.0

    # 3. Ungrounded Hallucination Node (Zero overlap with evidence)
    hallucinated_node = CanonicalGraphNode(
        node_id="c_hallucinated_03",
        evidence_id=evidence.evidence_id,
        type="ARCHITECTURAL_DECISION",
        name="Kubernetes Multi-Region Failover Cluster",
        strategic_value=7.0,
        tacticality=2.0,
        evidence_quality=0.0,
        properties={"context": "Provision multi-region AWS EKS pods"},
    )
    judged_hallucinated = quality_judge.judge_node(hallucinated_node, evidence)
    assert judged_hallucinated.is_quarantined is True
    assert "UNGROUNDED_EVIDENCE" in (judged_hallucinated.quarantine_reason or "")

    # 4. Advisory Node - Low Strategic Value (< 4.0)
    low_adv = InternalAdvisoryNode(
        node_id="adv_low_04",
        evidence_id=evidence.evidence_id,
        type="STAKEHOLDER_DYNAMICS",
        name="Participant arrived 2 minutes late to meeting",
        category=AdvisoryCategory.STAKEHOLDER_DYNAMICS,
        strategic_value=2.0,
        properties={"signal": "Late arrival"},
    )
    judged_adv = stealth_judge.judge_node(low_adv, evidence)
    assert judged_adv.is_quarantined is True
    assert "LOW_STRATEGIC_VALUE" in (judged_adv.quarantine_reason or "")

    # 5. Advisory Node - High Strategic Value (>= 4.0)
    high_adv = InternalAdvisoryNode(
        node_id="adv_high_05",
        evidence_id=evidence.evidence_id,
        type="BLOCKER",
        name="Cross-departmental budget approval blocker",
        category=AdvisoryCategory.BLOCKER,
        strategic_value=9.0,
        properties={"signal": "Department head withholding budget approval until Q4"},
    )
    judged_high_adv = stealth_judge.judge_node(high_adv, evidence)
    assert judged_high_adv.is_quarantined is False
    assert judged_high_adv.quarantine_reason is None


# ─────────────────────────────────────────────────────────────────────────────
# 6. Idempotent Deduplication & Re-Processing Hardening
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_idempotent_reprocessing_and_checkpoint_deduplication(test_rig):
    """
    Law 4 Hardening: Verifies that re-processing the same work item:
      1. Generates identical deterministic SHA-256 checkpoints.
      2. Does not produce duplicate nodes in CANONICAL_GRAPH or SHADOW_GRAPH.
      3. Preserves graph integrity across repeated worker runs.
    """
    db = test_rig["db"]
    worker = test_rig["worker"]

    ev = EvidenceRecord(
        evidence_id="ev_idempotent_01",
        meeting_id="meet_idemp",
        speaker="Chief Architect",
        text="CTO aligns on the dual-artery architecture with zero biometric leakage, but notes migration risk.",
        start_timestamp=0.0,
    )
    event_id = db.append_evidence(ev)

    # First execution pass
    processed_1 = await worker.run_once()
    assert processed_1 == 1

    # Check node counts
    can_nodes_1 = db.query_canonical_nodes(include_quarantined=True)
    adv_nodes_1 = db.query_internal_advisory_nodes(include_quarantined=True)
    assert len(can_nodes_1) >= 1
    assert len(adv_nodes_1) >= 1

    with db.get_evidence_conn() as conn:
        chk_1 = conn.execute(
            "SELECT checkpoint FROM durable_work_queue WHERE event_id = ?",
            (event_id,)
        ).fetchone()[0]
        assert chk_1 is not None

    # Manually reset state to PENDING to simulate a crash/restart scenario
    with db.get_evidence_conn() as conn:
        conn.execute(
            "UPDATE durable_work_queue SET state = 'PENDING', locked_at = NULL, locked_by = NULL WHERE event_id = ?",
            (event_id,)
        )
        conn.commit()

    # Second execution pass (Re-processing)
    processed_2 = await worker.run_once()
    assert processed_2 == 1

    # Verify node counts did NOT duplicate!
    can_nodes_2 = db.query_canonical_nodes(include_quarantined=True)
    adv_nodes_2 = db.query_internal_advisory_nodes(include_quarantined=True)
    assert len(can_nodes_2) == len(can_nodes_1)
    assert len(adv_nodes_2) == len(adv_nodes_1)

    # Verify checkpoint is bitwise identical
    with db.get_evidence_conn() as conn:
        chk_2 = conn.execute(
            "SELECT checkpoint FROM durable_work_queue WHERE event_id = ?",
            (event_id,)
        ).fetchone()[0]
        assert chk_2 == chk_1

