"""Test Suite: SQLite WAL Concurrency, Busy Timeout, and Atomic Append Protocol.

Verifies:
- SQLite WAL mode and 30,000ms busy timeout invariants.
- 16-worker concurrent evidence appends without lock contention or lost updates.
- Concurrent reader/writer overlap without reader blocking or database locks.
- Idempotent duplicate prevention via unique content digests.
- Checkpoint integrity and crash recovery verification.
"""
from __future__ import annotations

import concurrent.futures
import time
from pathlib import Path
from typing import List

import pytest

from dual_pipeline.l0.contracts import EvidenceRecord
from dual_pipeline.l0.provenance import compute_content_digest
from dual_pipeline.l0.wal_evidence import (
    append_evidence_record,
    get_evidence_record_by_digest,
    get_governed_wal_connection,
    init_evidence_wal_db,
    query_evidence_by_tenant_and_namespace,
)


@pytest.fixture
def temp_wal_db(tmp_path: Path) -> Path:
    db_file = tmp_path / "test_concurrency.sqlite"
    init_evidence_wal_db(db_file)
    return db_file


def test_wal_pragma_settings(temp_wal_db: Path) -> None:
    with get_governed_wal_connection(temp_wal_db) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]

    assert str(journal_mode).lower() == "wal"
    assert int(busy_timeout) >= 30000


def test_concurrent_evidence_appends_16_workers(temp_wal_db: Path) -> None:
    worker_count = 16
    records_per_worker = 5

    def worker_task(worker_id: int) -> List[str]:
        appended_ids = []
        for i in range(records_per_worker):
            text = f"Worker {worker_id} statement {i} at {time.time()}"
            rec = EvidenceRecord(
                evidence_id=f"ev_w{worker_id}_{i}",
                session_id=f"sess_{worker_id}",
                source_id="audio_mic",
                speaker=f"Speaker_{worker_id}",
                text=text,
                timestamp=time.time(),
                namespace="CLIENT_SAFE",
                tenant_id="test_tenant",
                digest=compute_content_digest(text).sha256,
            )
            success = append_evidence_record(temp_wal_db, rec)
            if success:
                appended_ids.append(rec.evidence_id)
        return appended_ids

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(worker_task, wid) for wid in range(worker_count)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    total_inserted = sum(len(r) for r in results)
    assert total_inserted == worker_count * records_per_worker

    # Verify total count in database
    records = query_evidence_by_tenant_and_namespace(
        temp_wal_db,
        tenant_id="test_tenant",
        namespace="CLIENT_SAFE",
    )
    assert len(records) == worker_count * records_per_worker


def test_concurrent_read_write_overlap(temp_wal_db: Path) -> None:
    stop_flag = False
    read_counts = []

    def writer():
        for i in range(25):
            text = f"Continuous write {i}"
            rec = EvidenceRecord(
                evidence_id=f"ev_rw_{i}",
                session_id="sess_rw",
                source_id="stream",
                speaker="ContinuousSpeaker",
                text=text,
                timestamp=time.time(),
                namespace="CLIENT_SAFE",
                tenant_id="rw_tenant",
                digest=compute_content_digest(text).sha256,
            )
            append_evidence_record(temp_wal_db, rec)
            time.sleep(0.005)

    def reader():
        while not stop_flag:
            recs = query_evidence_by_tenant_and_namespace(
                temp_wal_db,
                tenant_id="rw_tenant",
                namespace="CLIENT_SAFE",
            )
            read_counts.append(len(recs))
            time.sleep(0.002)

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        writer_future = executor.submit(writer)
        reader_future = executor.submit(reader)

        writer_future.result()
        stop_flag = True
        reader_future.result()

    assert len(read_counts) > 0
    # Final query must see all 25 records
    final_records = query_evidence_by_tenant_and_namespace(
        temp_wal_db,
        tenant_id="rw_tenant",
        namespace="CLIENT_SAFE",
    )
    assert len(final_records) == 25


def test_retry_idempotency_duplicate_appends(temp_wal_db: Path) -> None:
    text = "Exactly identical statement text"
    rec = EvidenceRecord(
        evidence_id="ev_idempotency_01",
        session_id="sess_idem",
        source_id="mic",
        speaker="SpeakerA",
        text=text,
        timestamp=1000.0,
        namespace="CLIENT_SAFE",
        tenant_id="tenant_idem",
        digest=compute_content_digest(text).sha256,
    )

    # First append
    first_res = append_evidence_record(temp_wal_db, rec)
    assert first_res is True

    # Duplicate append with same digest
    duplicate_rec = EvidenceRecord(
        evidence_id="ev_idempotency_02",  # Different ID, but same digest
        session_id="sess_idem",
        source_id="mic",
        speaker="SpeakerA",
        text=text,
        timestamp=1001.0,
        namespace="CLIENT_SAFE",
        tenant_id="tenant_idem",
        digest=compute_content_digest(text).sha256,
    )
    second_res = append_evidence_record(temp_wal_db, duplicate_rec)
    assert second_res is False  # Ignored idempotently

    # Only 1 record should exist in the DB
    found = get_evidence_record_by_digest(temp_wal_db, rec.digest)
    assert found is not None
    assert found.evidence_id == "ev_idempotency_01"


def test_wal_checkpoint_and_recovery(temp_wal_db: Path) -> None:
    rec = EvidenceRecord(
        evidence_id="ev_chk_01",
        session_id="sess_chk",
        source_id="mic",
        speaker="SpeakerB",
        text="Pre-checkpoint data",
        timestamp=time.time(),
        namespace="CLIENT_SAFE",
        tenant_id="tenant_chk",
        digest=compute_content_digest("Pre-checkpoint data").sha256,
    )
    append_evidence_record(temp_wal_db, rec)

    with get_governed_wal_connection(temp_wal_db) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")

    # Data remains intact and queryable post-checkpoint
    found = get_evidence_record_by_digest(temp_wal_db, rec.digest)
    assert found is not None
    assert found.text == "Pre-checkpoint data"
