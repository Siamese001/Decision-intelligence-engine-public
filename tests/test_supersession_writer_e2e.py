"""End-to-end test suite for governed temporal supersession writer with physical proof.

Validates atomic supersession of stale edges, resolution of validation_queue items,
and enforcement of all 5 mandatory physical checks (File Changed, Raw Bytes,
Integrity Check, Readback, and Row Delta).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from unified_pipeline.temporal_reconciliation.supersession_writer import (
    SupersessionRequest,
    TemporalSupersessionWriter,
)


def _init_test_graph_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            display_name TEXT
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            source_name TEXT NOT NULL,
            source_date TEXT,
            authority_rank INTEGER NOT NULL DEFAULT 5,
            source_posture TEXT NOT NULL DEFAULT 'INTERNAL',
            created_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE edge (
            edge_id TEXT PRIMARY KEY,
            from_node_id TEXT NOT NULL,
            edge_type TEXT NOT NULL,
            to_node_id TEXT NOT NULL,
            description TEXT,
            evidence_posture TEXT NOT NULL DEFAULT 'DOCUMENTED',
            confidence TEXT NOT NULL DEFAULT 'HIGH',
            materiality TEXT NOT NULL DEFAULT 'HIGH',
            validation_priority TEXT,
            validation_owner TEXT,
            hitl_required INTEGER NOT NULL DEFAULT 0,
            source_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            superseded_by TEXT
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE validation_queue (
            validation_id TEXT PRIMARY KEY CHECK (trim(validation_id)<>''),
            node_id TEXT,
            edge_id TEXT,
            claim_id TEXT,
            validation_question TEXT NOT NULL,
            owner TEXT NOT NULL,
            priority TEXT NOT NULL,
            due_date TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            resolution TEXT,
            resolved_at TEXT,
            created_at TEXT NOT NULL
        );
        """
    )

    # Insert authentic entities and target edge
    conn.execute("INSERT INTO node VALUES ('person_abhijit_mahishi', 'STAKEHOLDER', 'Abhijit Mahishi', 'PartnerAdvisory Tech Lead');")
    conn.execute("INSERT INTO node VALUES ('workstream_fcdb', 'WORKSTREAM', 'FCDB', 'Fault Code Database');")
    conn.execute("INSERT INTO source VALUES ('source_june_packet', 'STATUS', 'June Packet', '2026-06-29', 3, 'INTERNAL', '2026-06-29T12:00:00Z');")
    conn.execute(
        """
        INSERT INTO edge VALUES (
            'edge_v36_person_abhijit_mahishi_leads_fcdb',
            'person_abhijit_mahishi', 'OWNS_OR_LEADS', 'workstream_fcdb',
            'Leads FCDB', 'DOCUMENTED', 'HIGH', 'HIGH', NULL, NULL, 0,
            'source_june_packet', '2026-06-29T12:00:00Z', '2026-06-29T12:00:00Z', NULL
        );
        """
    )
    conn.execute(
        """
        INSERT INTO validation_queue VALUES (
            'val_stale_fcdb_01', 'person_abhijit_mahishi', 'edge_v36_person_abhijit_mahishi_leads_fcdb',
            NULL, 'Confirm tenure for Abhijit Mahishi on FCDB', 'OPERATOR', 'P1',
            '2026-10-25', 'open', NULL, NULL, '2026-10-18T00:00:00Z'
        );
        """
    )
    conn.commit()
    conn.close()


@pytest.fixture
def isolated_db(tmp_path: Path) -> Path:
    db_file = tmp_path / "test_supersession.sqlite"
    _init_test_graph_db(db_file)
    return db_file


def test_apply_supersession_with_5_check_physical_proof(isolated_db: Path) -> None:
    writer = TemporalSupersessionWriter(isolated_db)
    request = SupersessionRequest(
        target_edge_id="edge_v36_person_abhijit_mahishi_leads_fcdb",
        effective_date="2026-10-01",
        reason="Omitted from Q4 Charter Roster and replaced as FCDB Tech Lead",
        approval_ref="PD-FCDB-ROLLOFF-2026-10",
        replacement_edge_id="edge_bailey_morton_leads_fcdb",
    )

    result = writer.apply_supersession(request)

    assert result.status == "COMMITTED"
    assert result.superseded_by == "edge_bailey_morton_leads_fcdb"
    assert result.physical_proof["overall"] == "PASS"

    checks_by_name = {c["check"]: c["status"] for c in result.physical_proof["checks"]}
    assert checks_by_name["file_changed"] == "PASS"
    assert checks_by_name["raw_bytes"] == "PASS"
    assert checks_by_name["integrity"] == "PASS"
    assert checks_by_name["readback"] == "PASS"
    assert checks_by_name["row_delta"] == "PASS"

    # Independent readback via fresh read-only SQLite connection
    conn = sqlite3.connect(f"file:{isolated_db.resolve().as_posix()}?mode=ro", uri=True)
    cur = conn.cursor()

    cur.execute("SELECT superseded_by, updated_at FROM edge WHERE edge_id = 'edge_v36_person_abhijit_mahishi_leads_fcdb';")
    edge_row = cur.fetchone()
    assert edge_row[0] == "edge_bailey_morton_leads_fcdb"

    cur.execute("SELECT status, resolution, resolved_at FROM validation_queue WHERE validation_id = 'val_stale_fcdb_01';")
    val_row = cur.fetchone()
    assert val_row[0] == "resolved"
    assert "edge_bailey_morton_leads_fcdb" in val_row[1]
    assert val_row[2] is not None

    # Verify non-destructive preservation: total row count is still 1 (row was updated, not deleted)
    cur.execute("SELECT COUNT(*) FROM edge;")
    assert cur.fetchone()[0] == 1

    conn.close()


def test_supersession_non_existent_or_already_superseded_fails(isolated_db: Path) -> None:
    writer = TemporalSupersessionWriter(isolated_db)
    request = SupersessionRequest(
        target_edge_id="edge_v36_person_abhijit_mahishi_leads_fcdb",
        effective_date="2026-10-01",
        reason="First valid retirement",
        approval_ref="PD-FCDB-ROLLOFF-01",
    )
    # First supersession succeeds
    writer.apply_supersession(request)

    # Second supersession attempt on already superseded edge must fail closed
    with pytest.raises(ValueError, match="not found or already superseded"):
        writer.apply_supersession(request)

    # Superseding non-existent edge must fail closed
    bad_req = SupersessionRequest(
        target_edge_id="edge_non_existent_phantom",
        effective_date="2026-10-01",
        reason="Phantom edge",
        approval_ref="PD-BAD",
    )
    with pytest.raises(ValueError, match="not found or already superseded"):
        writer.apply_supersession(bad_req)
