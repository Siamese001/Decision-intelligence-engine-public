"""Adversarial and edge-case tests for APEX_TRANS gap remediation and HRIT project affinity guards."""
from __future__ import annotations

import sqlite3

from tools.tier2_dedup_core.neet_gap_remediation import (
    NEET_CANONICAL_SOURCES,
    _consolidate_neet_duplicate_sources,
    _promote_neet_candidates,
    _unmerge_neet_collided_sources,
)
from tools.validate_output_hierarchy_core.helpers import _validate_project_affinity


def _make_minimal_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", timeout=30.0)
    conn.executescript(
        """
        CREATE TABLE governance_approval (
            approval_id TEXT PRIMARY KEY, approval_reference TEXT, approval_type TEXT,
            scope_json TEXT, proposal_sha256 TEXT, approval_statement_sha256 TEXT,
            approved_at TEXT, expires_at TEXT, status TEXT, approved_by TEXT,
            release_version TEXT, active INTEGER, created_at TEXT
        );
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY, source_type TEXT, source_name TEXT,
            source_date TEXT, authority_rank INTEGER, source_posture TEXT,
            cold_archive_ref TEXT, notes TEXT, created_at TEXT
        );
        CREATE TABLE source_version (
            source_version_id TEXT PRIMARY KEY, source_id TEXT, version_number INTEGER
        );
        CREATE TABLE evidence_item (
            evidence_id TEXT PRIMARY KEY, source_id TEXT, source_version_id TEXT
        );
        CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT);
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY, primary_source_id TEXT,
            canonical_status TEXT, canonical_eligible INTEGER,
            validation_state TEXT, currentness_state TEXT,
            superseded_by_assertion_id TEXT, retrieval_eligible INTEGER,
            approval_reference TEXT, updated_at TEXT
        );
        """
    )
    return conn


def test_boundary_empty_database_and_idempotent_reexecution() -> None:
    """Category 1 (Boundary payload) & Category 6 (Idempotent re-execution): empty tables return 0 without error."""
    conn = _make_minimal_conn()
    assert _unmerge_neet_collided_sources(conn, "2026-10-25T21:00:00Z") == 0
    assert _consolidate_neet_duplicate_sources(conn) == 0
    stats = _promote_neet_candidates(conn, "2026-10-25T21:00:00Z")
    assert stats["total_promoted"] == 0
    # Second run is strictly idempotent
    assert _unmerge_neet_collided_sources(conn, "2026-10-25T21:00:00Z") == 0
    assert _consolidate_neet_duplicate_sources(conn) == 0
    conn.close()


def test_rollback_recovery_on_trigger_integrity_error() -> None:
    """Category 6 (Rollback & recovery): savepoint recovers when canonical promotion trigger rejects an assertion."""
    conn = _make_minimal_conn()
    sid = NEET_CANONICAL_SOURCES[0]
    conn.execute(
        "INSERT INTO source VALUES (?, 'dual_pipeline_intake', 'Vanguard', '2026-07-21', 5, 'derivative_internal', 'ref', '', '2026-07-21')",
        (sid,),
    )
    conn.execute(
        "INSERT INTO assertion VALUES ('a-bad', ?, 'candidate', 0, 'needs_review', 'current', NULL, 0, NULL, '2026-07-21')",
        (sid,),
    )
    conn.execute(
        "INSERT INTO assertion VALUES ('a-good', ?, 'candidate', 0, 'needs_review', 'current', NULL, 0, NULL, '2026-07-21')",
        (sid,),
    )
    conn.execute(
        """
        CREATE TRIGGER reject_bad_canonical BEFORE UPDATE ON assertion
        WHEN new.assertion_id = 'a-bad' AND new.canonical_status = 'canonical'
        BEGIN
            SELECT RAISE(ABORT, 'simulated cardinality or evidence trigger rejection');
        END;
        """
    )
    stats = _promote_neet_candidates(conn, "2026-10-25T21:00:00Z")
    assert stats["promoted_active_candidates"] == 1
    assert stats["normalized_superseded_candidates"] == 1
    bad_row = conn.execute("SELECT canonical_status, validation_state FROM assertion WHERE assertion_id = 'a-bad'").fetchone()
    assert bad_row == ("historical", "superseded")
    good_row = conn.execute("SELECT canonical_status, validation_state FROM assertion WHERE assertion_id = 'a-good'").fetchone()
    assert good_row == ("canonical", "accepted_legacy")
    conn.close()


def test_invariant_bypass_hrit_affinity_under_neet_rejected() -> None:
    """Category 5 (Invariant bypass attempt): HRIT/ENTERPRISE4U deliverables placed under 'apex_trans' fail affinity validation."""
    for stem in (
        "Hr_It_Minute_Notes_09_16_09_25_apex_global_09-25-2026",
        "Nee4u_Employee_Experience_Agent_PartnerAdvisory_10-12-2026",
        "Hr360_Position_Request_Form_Clement_10-24-2026",
    ):
        err = _validate_project_affinity(stem, "apex_trans", "apex_trans/10-12-2026")
        assert err is not None and "[PROJECT_AFFINITY_MISMATCH]" in err
        ok = _validate_project_affinity(stem, "hrit", "hrit/10-12-2026")
        assert ok is None
