"""Adversarial and boundary test suite for roster set-difference reconciler.

Validates that omission of active workstream members (such as Abhijit Mahishi on FCDB)
from comprehensive status/charter rosters generates structured retraction candidates,
while non-comprehensive or malformed payloads fail closed.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from unified_pipeline.temporal_reconciliation.set_diff_reconciler import SetDiffReconciler


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

    # Insert authentic entities
    conn.execute("INSERT INTO node VALUES ('person_abhijit_mahishi', 'STAKEHOLDER', 'Abhijit Mahishi', 'PartnerAdvisory Tech Lead');")
    conn.execute("INSERT INTO node VALUES ('person_lancey_lexima', 'STAKEHOLDER', 'Lancey Lexima', 'Product Manager');")
    conn.execute("INSERT INTO node VALUES ('person_brett_gavagni', 'STAKEHOLDER', 'Brett Gavagni', 'Engineering Manager');")
    conn.execute("INSERT INTO node VALUES ('workstream_fcdb', 'WORKSTREAM', 'FCDB', 'Fault Code Database');")

    # Insert July 2026 roster source
    conn.execute(
        "INSERT INTO source VALUES ('source_v44_nea_charter_roster_xlsx_2026_07_11', 'ROSTER', 'July 2026 Charter Roster', '2026-07-11', 2, 'INTERNAL', '2026-07-11T00:34:03Z');"
    )

    # Insert active FCDB edges for all 3 members
    conn.execute(
        """
        INSERT INTO edge VALUES (
            'edge_v36_person_abhijit_mahishi_leads_fcdb',
            'person_abhijit_mahishi', 'OWNS_OR_LEADS', 'workstream_fcdb',
            'Tech Lead FCDB', 'DOCUMENTED', 'HIGH', 'HIGH', NULL, NULL, 0,
            'source_v44_nea_charter_roster_xlsx_2026_07_11', '2026-07-11T00:34:03Z', '2026-07-11T00:34:03Z', NULL
        );
        """
    )
    conn.execute(
        """
        INSERT INTO edge VALUES (
            'edge_v44_row_7_tech_lead_abhijit_mahishi',
            'person_abhijit_mahishi', 'DATED_ROLE_IN', 'workstream_fcdb',
            'Dated Tech Lead', 'DOCUMENTED', 'HIGH', 'HIGH', 'P1', NULL, 0,
            'source_v44_nea_charter_roster_xlsx_2026_07_11', '2026-07-11T00:34:03Z', '2026-07-11T00:34:03Z', NULL
        );
        """
    )
    conn.execute(
        """
        INSERT INTO edge VALUES (
            'edge_lancey_fcdb_pm',
            'person_lancey_lexima', 'DATED_ROLE_IN', 'workstream_fcdb',
            'Product Manager FCDB', 'DOCUMENTED', 'HIGH', 'HIGH', NULL, NULL, 0,
            'source_v44_nea_charter_roster_xlsx_2026_07_11', '2026-07-11T00:34:03Z', '2026-07-11T00:34:03Z', NULL
        );
        """
    )
    conn.execute(
        """
        INSERT INTO edge VALUES (
            'edge_brett_fcdb_em',
            'person_brett_gavagni', 'DATED_ROLE_IN', 'workstream_fcdb',
            'Engineering Manager FCDB', 'DOCUMENTED', 'HIGH', 'HIGH', NULL, NULL, 0,
            'source_v44_nea_charter_roster_xlsx_2026_07_11', '2026-07-11T00:34:03Z', '2026-07-11T00:34:03Z', NULL
        );
        """
    )

    conn.commit()
    conn.close()


@pytest.fixture
def isolated_db(tmp_path: Path) -> Path:
    db_file = tmp_path / "test_set_diff.sqlite"
    _init_test_graph_db(db_file)
    return db_file


# Category 1: Exact production regression (Abhijit Mahishi omitted from Q4 FCDB roster)
def test_production_regression_abhijit_mahishi_omission_detected(isolated_db: Path) -> None:
    reconciler = SetDiffReconciler(isolated_db)
    # Q4 Roster contains Lancey and Brett, but omits Abhijit
    q4_members = ["person_lancey_lexima", "person_brett_gavagni"]

    report = reconciler.reconcile_roster(
        workstream_id="workstream_fcdb",
        new_source_id="source_q4_charter_roster_2026_10_01",
        source_type="ROSTER",
        source_name="Q4 NEA Charter Roster",
        source_date="2026-10-01",
        roster_member_node_ids=q4_members,
    )

    assert report.is_comprehensive is True
    assert report.disqualification_reason is None
    assert set(report.active_graph_members) == {
        "person_abhijit_mahishi",
        "person_lancey_lexima",
        "person_brett_gavagni",
    }
    assert report.omitted_members == ["person_abhijit_mahishi"]
    assert len(report.candidates) == 2

    edge_ids = {c.target_edge_id for c in report.candidates}
    assert "edge_v36_person_abhijit_mahishi_leads_fcdb" in edge_ids
    assert "edge_v44_row_7_tech_lead_abhijit_mahishi" in edge_ids

    for c in report.candidates:
        assert c.missing_entity_id == "person_abhijit_mahishi"
        assert c.retracting_source_id == "source_q4_charter_roster_2026_10_01"
        assert c.confidence_score >= 0.90


# Category 2: Boundary payload (empty roster member list fails closed)
def test_boundary_empty_roster_fails_closed(isolated_db: Path) -> None:
    reconciler = SetDiffReconciler(isolated_db)
    report = reconciler.reconcile_roster(
        workstream_id="workstream_fcdb",
        new_source_id="source_empty",
        source_type="ROSTER",
        source_name="Empty Roster Payload",
        source_date="2026-10-01",
        roster_member_node_ids=[],
    )

    assert len(report.candidates) == 0
    assert report.disqualification_reason is not None
    assert "EMPTY_ROSTER_PAYLOAD" in report.disqualification_reason


# Category 3: Invariant bypass attempt (partial non-comprehensive source rejected)
def test_invariant_bypass_partial_mention_source_rejected(isolated_db: Path) -> None:
    reconciler = SetDiffReconciler(isolated_db)
    # A meeting transcript mentions only Lancey Lexima
    report = reconciler.reconcile_roster(
        workstream_id="workstream_fcdb",
        new_source_id="source_standup_transcript_2026_10_05",
        source_type="MEETING_TRANSCRIPT",
        source_name="Daily Standup Transcript",
        source_date="2026-10-05",
        roster_member_node_ids=["person_lancey_lexima"],
    )

    assert report.is_comprehensive is False
    assert len(report.candidates) == 0
    assert report.disqualification_reason is not None
    assert "SOURCE_TYPE_NON_COMPREHENSIVE" in report.disqualification_reason


# Category 4: Malformed input (workstream with zero active edges)
def test_boundary_unregistered_workstream_yields_zero_candidates(isolated_db: Path) -> None:
    reconciler = SetDiffReconciler(isolated_db)
    report = reconciler.reconcile_roster(
        workstream_id="workstream_unknown_phantom",
        new_source_id="source_q4_charter",
        source_type="ROSTER",
        source_name="Q4 Roster",
        source_date="2026-10-01",
        roster_member_node_ids=["person_lancey_lexima"],
    )

    assert report.active_graph_members == []
    assert report.omitted_members == []
    assert len(report.candidates) == 0
