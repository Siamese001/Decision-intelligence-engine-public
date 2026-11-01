#!/usr/bin/env python3
"""
Unit tests for Dual-Artery Storage Subsystem (`storage/database.py`).
Verifies WAL configuration, durable queues, immutable logging,
soft-quarantine tagging, and internal advisory shadow storage isolation.
"""
import shutil
import tempfile
from pathlib import Path

import pytest

from storage.database import (
    AdvisoryCategory,
    CanonicalGraphNode,
    DatabaseManager,
    EvidenceRecord,
    InternalAdvisoryNode,
    WorkItemState,
)


@pytest.fixture
def temp_db_manager():
    temp_dir = Path(tempfile.mkdtemp())
    mgr = DatabaseManager(base_dir=temp_dir, allow_unencrypted_dev_fallback=True)
    yield mgr
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_wal_mode_and_foreign_keys(temp_db_manager):
    with temp_db_manager.get_evidence_conn() as conn:
        journal = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert journal.upper() == "WAL"
        fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        assert fk == 1

    with temp_db_manager.get_canonical_conn() as conn:
        journal = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert journal.upper() == "WAL"


def test_immutable_evidence_logging_and_queue(temp_db_manager):
    rec = EvidenceRecord(
        evidence_id="ev_test_100",
        meeting_id="meet_eng_01",
        speaker="Speaker A",
        text="Testing raw STT immutable log",
        start_timestamp=0.0,
        end_timestamp=3.5,
        is_final=True
    )
    event_id = temp_db_manager.append_evidence(rec)
    assert event_id.startswith("evt_ev_test_100")

    # Lease batch
    leased = temp_db_manager.lease_work_batch(batch_size=10, worker_id="test_worker")
    assert len(leased) == 1
    item, evidence = leased[0]
    assert item.event_id == event_id
    assert item.state == WorkItemState.PROCESSING
    assert evidence.text == "Testing raw STT immutable log"

    # Complete item
    temp_db_manager.complete_work_item(event_id, checkpoint="sha256_chk_1")

    # Verify queue is now empty for next lease
    leased_again = temp_db_manager.lease_work_batch(batch_size=10, worker_id="test_worker")
    assert len(leased_again) == 0


def test_retry_and_dead_letter(temp_db_manager):
    rec = EvidenceRecord(
        evidence_id="ev_fail_1",
        meeting_id="meet_fail",
        speaker="Speaker B",
        text="Will fail parsing",
        start_timestamp=5.0
    )
    event_id = temp_db_manager.append_evidence(rec)

    # First lease and fail
    leased = temp_db_manager.lease_work_batch(batch_size=1, worker_id="worker_fail")
    assert len(leased) == 1
    state1 = temp_db_manager.fail_work_item(event_id, error_message="Error 1", max_retries=2)
    assert state1 == WorkItemState.PENDING

    # Second lease and fail (reaches max_retries=2)
    leased2 = temp_db_manager.lease_work_batch(batch_size=1, worker_id="worker_fail")
    assert len(leased2) == 1
    state2 = temp_db_manager.fail_work_item(event_id, error_message="Error 2", max_retries=2)
    assert state2 == WorkItemState.DEAD_LETTER

    # Should no longer be leased
    leased3 = temp_db_manager.lease_work_batch(batch_size=1, worker_id="worker_fail")
    assert len(leased3) == 0


def test_canonical_node_soft_quarantine_policy(temp_db_manager):
    # Log evidence first (FK constraint)
    rec = EvidenceRecord(
        evidence_id="ev_fact_01",
        meeting_id="meet_gov",
        speaker="Lead Architect",
        text="Decision reached",
        start_timestamp=10.0
    )
    temp_db_manager.append_evidence(rec)

    # Insert non-quarantined strategic node
    node_good = CanonicalGraphNode(
        node_id="node_good",
        evidence_id="ev_fact_01",
        type="DECISION",
        name="Adopt Hybrid Architecture",
        strategic_value=9.0,
        tacticality=0.0,
        is_quarantined=False
    )
    temp_db_manager.upsert_canonical_node(node_good)

    # Insert quarantined tactical node (Law 5 soft quarantine)
    node_tactical = CanonicalGraphNode(
        node_id="node_tactical",
        evidence_id="ev_fact_01",
        type="TODO",
        name="Schedule follow-up invite",
        strategic_value=1.0,
        tacticality=9.0,
        is_quarantined=True,
        quarantine_reason="TACTICAL_SUPPRESSION"
    )
    temp_db_manager.upsert_canonical_node(node_tactical)

    # Query with default policy (Law 6: drop quarantined before vector retrieval)
    visible = temp_db_manager.query_canonical_nodes()
    assert len(visible) == 1
    assert visible[0].node_id == "node_good"

    # Query including quarantined
    all_nodes = temp_db_manager.query_canonical_nodes(include_quarantined=True)
    assert len(all_nodes) == 2


def test_internal_advisory_isolation(temp_db_manager):
    adv_node = InternalAdvisoryNode(
        node_id="adv_node_01",
        evidence_id="ev_fact_01",
        type="INFLUENCE_MAP",
        name="CTO Consensus Path",
        category=AdvisoryCategory.DECISION_INFLUENCE,
        strategic_value=9.2,
        stealth_score=1.0,
        namespace="INTERNAL_ADVISORY"
    )
    temp_db_manager.upsert_internal_advisory_node(adv_node)

    results = temp_db_manager.query_internal_advisory_nodes(category=AdvisoryCategory.DECISION_INFLUENCE)
    assert len(results) == 1
    assert results[0].name == "CTO Consensus Path"
    assert results[0].namespace == "INTERNAL_ADVISORY"
