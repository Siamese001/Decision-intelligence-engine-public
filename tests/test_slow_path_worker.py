#!/usr/bin/env python3
"""
Unit tests for Pipeline B: Artery 2 - The Slow Path (`pipeline_b_extraction/`).
Verifies:
  - Law 1: Compute Fork BEFORE Extraction
  - Law 2: Governed INTERNAL_ADVISORY Categories
  - Law 4: Durable queue consumption and immutable provenance FK
  - Law 5: Multi-Dimensional Soft-Quarantine Metadata Tagging
"""
import shutil
import tempfile
from pathlib import Path

import pytest

from pipeline_b_extraction.extractors import (
    ComputeForkCoordinator,
)
from pipeline_b_extraction.worker import SlowPathWorker, WorkerConfig
from storage.database import (
    AdvisoryCategory,
    DatabaseManager,
    EvidenceRecord,
    WorkItemState,
)


@pytest.fixture
def temp_environment():
    temp_dir = Path(tempfile.mkdtemp())
    mgr = DatabaseManager(base_dir=temp_dir)
    worker = SlowPathWorker(
        db_manager=mgr,
        config=WorkerConfig(batch_size=5, poll_interval_seconds=0.1, discourse_window_seconds=0.0),
    )
    yield mgr, worker
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.anyio
async def test_compute_fork_independent_execution():
    """Verify Law 1: Two extractors execute with independent schemas and models."""
    evidence = EvidenceRecord(
        evidence_id="ev_fork_01",
        meeting_id="meet_fork",
        speaker="Amit Ayer",
        text="We are standardizing on WAL mode for the database, but the VP is hesitant about migration friction.",
        start_timestamp=10.0,
    )

    coordinator = ComputeForkCoordinator()
    result = await coordinator.execute_fork(evidence)

    # 1. Canonical nodes must be CLIENT_SAFE only
    assert len(result.canonical_nodes) >= 1
    for c_node in result.canonical_nodes:
        assert c_node.namespace == "CLIENT_SAFE"
        assert c_node.evidence_id == "ev_fork_01"

    # 2. Advisory nodes must be INTERNAL_ADVISORY only (Law 2)
    assert len(result.advisory_nodes) >= 1
    for a_node in result.advisory_nodes:
        assert a_node.namespace == "INTERNAL_ADVISORY"
        assert a_node.evidence_id == "ev_fork_01"
        assert a_node.category in [
            AdvisoryCategory.STAKEHOLDER_DYNAMICS,
            AdvisoryCategory.DECISION_INFLUENCE,
            AdvisoryCategory.ADOPTION_RISK,
            AdvisoryCategory.BLOCKER,
        ]


@pytest.mark.anyio
async def test_quality_judge_soft_quarantine(temp_environment):
    """Verify Law 5: Tactical items are tagged with soft quarantine metadata, not dropped."""
    mgr, worker = temp_environment

    # 1. High-value strategic evidence
    ev_strategic = EvidenceRecord(
        evidence_id="ev_strat_01",
        meeting_id="meet_test",
        speaker="Architect",
        text="We have decided to decouple the compute fork from extraction to enforce zero biometric leakage.",
        start_timestamp=0.0,
    )
    mgr.append_evidence(ev_strategic)

    # 2. Tactical to-do evidence
    ev_tactical = EvidenceRecord(
        evidence_id="ev_tact_02",
        meeting_id="meet_test",
        speaker="Project Manager",
        text="Please schedule a calendar invite and follow-up to-do for the engineering team tomorrow.",
        start_timestamp=5.0,
    )
    mgr.append_evidence(ev_tactical)

    # Process batch with worker
    processed_count = await worker.run_once()
    assert processed_count == 2

    # Query CANONICAL_GRAPH.db with default policy (Law 6: Drop quarantined before vector search)
    visible_nodes = mgr.query_canonical_nodes(include_quarantined=False)
    assert len(visible_nodes) == 1
    assert visible_nodes[0].evidence_id == "ev_strat_01"
    assert visible_nodes[0].is_quarantined is False

    # Query CANONICAL_GRAPH.db including quarantined: tactical node exists! (Law 5)
    all_nodes = mgr.query_canonical_nodes(include_quarantined=True)
    assert len(all_nodes) == 2
    tactical_node = next(n for n in all_nodes if n.evidence_id == "ev_tact_02")
    assert tactical_node.is_quarantined is True
    assert "TACTICAL_SUPPRESSION" in (tactical_node.quarantine_reason or "")
    assert tactical_node.tacticality >= 7.0


@pytest.mark.anyio
async def test_slow_path_worker_idempotency_and_queue(temp_environment):
    """Verify Law 4: Queue items transition to COMPLETED with checkpoint hashes."""
    mgr, worker = temp_environment

    ev = EvidenceRecord(
        evidence_id="ev_queue_01",
        meeting_id="meet_q",
        speaker="Director",
        text="Budgetary freeze might delay the procurement blocker next quarter.",
        start_timestamp=20.0,
    )
    event_id = mgr.append_evidence(ev)

    # First run processes the item
    count = await worker.run_once()
    assert count == 1

    # Verify queue status is now COMPLETED
    with mgr.get_evidence_conn() as conn:
        row = conn.execute(
            "SELECT state, checkpoint FROM durable_work_queue WHERE event_id = ?",
            (event_id,)
        ).fetchone()
        assert row["state"] == WorkItemState.COMPLETED.value
        assert row["checkpoint"] is not None

    # Second run should find 0 pending items
    count2 = await worker.run_once()
    assert count2 == 0

    # Verify shadow graph received the internal advisory node (Law 2)
    adv_nodes = mgr.query_internal_advisory_nodes()
    assert len(adv_nodes) >= 1
    assert adv_nodes[0].evidence_id == "ev_queue_01"
    assert adv_nodes[0].category in [AdvisoryCategory.BLOCKER, AdvisoryCategory.ADOPTION_RISK]
