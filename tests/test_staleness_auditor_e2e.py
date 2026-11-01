"""End-to-end and boundary test suite for temporal staleness auditor.

Validates that active mutable role edges exceeding TTL thresholds (such as
person_abhijit_mahishi on workstream_fcdb) are correctly ranked into P1/P2
staleness tiers without mutating the underlying SQLite database.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from unified_pipeline.temporal_reconciliation.staleness_auditor import (
    StalenessAuditor,
    export_staleness_report,
)


def _init_test_graph_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            name TEXT,
            label TEXT
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

    # Insert authentic nodes
    conn.execute("INSERT INTO node VALUES ('person_abhijit_mahishi', 'STAKEHOLDER', 'Abhijit Mahishi', 'PartnerAdvisory Lead');")
    conn.execute("INSERT INTO node VALUES ('workstream_fcdb', 'WORKSTREAM', 'FCDB', 'Fault Code Database');")
    conn.execute("INSERT INTO node VALUES ('workstream_dociq', 'WORKSTREAM', 'DocIQ', 'DocIQ Engine');")
    conn.execute("INSERT INTO node VALUES ('person_bailey_morton', 'STAKEHOLDER', 'Bailey Morton', 'Tech Lead');")
    conn.execute("INSERT INTO node VALUES ('org_partner_advisory', 'ORGANIZATION', 'PartnerAdvisory', 'Consulting Partner');")

    # Insert sources with authentic historical dates
    conn.execute(
        "INSERT INTO source VALUES ('source_nea_status_2026_06_29', 'STATUS_REPORT', 'NEA Tech Lead Q2 Weekly Status Packet', '2026-06-29', 3, 'INTERNAL', '2026-06-29T12:00:00Z');"
    )
    conn.execute(
        "INSERT INTO source VALUES ('source_v44_nea_charter_roster_xlsx_2026_07_11', 'ROSTER', 'NEA Charter Roster', '2026-07-11', 2, 'INTERNAL', '2026-07-11T00:34:03Z');"
    )
    conn.execute(
        "INSERT INTO source VALUES ('source_q4_status_2026_10_01', 'STATUS_REPORT', 'Q4 Strategy Review', '2026-10-01', 1, 'INTERNAL', '2026-10-01T09:00:00Z');"
    )
    conn.execute(
        "INSERT INTO source VALUES ('source_no_date', 'NOTE', 'Ad-hoc note', NULL, 5, 'INTERNAL', '2026-08-01T00:00:00Z');"
    )

    # Insert edges
    # 1. Abhijit leads FCDB (from June 29, 2026 -> 111 days old relative to 2026-10-18)
    conn.execute(
        """
        INSERT INTO edge VALUES (
            'edge_v36_person_abhijit_mahishi_leads_fcdb',
            'person_abhijit_mahishi',
            'OWNS_OR_LEADS',
            'workstream_fcdb',
            'Leads FCDB workstream',
            'DOCUMENTED', 'HIGH', 'HIGH', NULL, NULL, 0,
            'source_nea_status_2026_06_29',
            '2026-07-10T20:00:00Z', '2026-07-10T20:00:00Z', NULL
        );
        """
    )
    # 2. Abhijit dated role in FCDB (from July 11, 2026 -> 99 days old)
    conn.execute(
        """
        INSERT INTO edge VALUES (
            'edge_v44_row_7_tech_lead_abhijit_mahishi',
            'person_abhijit_mahishi',
            'DATED_ROLE_IN',
            'workstream_fcdb',
            'Tech lead role in FCDB',
            'DOCUMENTED', 'HIGH', 'HIGH', 'P1', NULL, 0,
            'source_v44_nea_charter_roster_xlsx_2026_07_11',
            '2026-07-11T00:34:03Z', '2026-07-11T00:34:03Z', NULL
        );
        """
    )
    # 3. Fresh edge: Bailey leads DocIQ (from Oct 1, 2026 -> 17 days old)
    conn.execute(
        """
        INSERT INTO edge VALUES (
            'edge_fresh_bailey_leads_dociq',
            'person_bailey_morton',
            'OWNS_OR_LEADS',
            'workstream_dociq',
            'Bailey leads DocIQ',
            'DOCUMENTED', 'HIGH', 'HIGH', NULL, NULL, 0,
            'source_q4_status_2026_10_01',
            '2026-10-01T09:00:00Z', '2026-10-01T09:00:00Z', NULL
        );
        """
    )
    # 4. Superseded edge: should be excluded regardless of age
    conn.execute(
        """
        INSERT INTO edge VALUES (
            'edge_superseded_old_lead',
            'person_abhijit_mahishi',
            'OWNS_OR_LEADS',
            'workstream_fcdb',
            'Historical superseded lead',
            'DOCUMENTED', 'HIGH', 'HIGH', NULL, NULL, 0,
            'source_nea_status_2026_06_29',
            '2026-06-29T12:00:00Z', '2026-07-10T20:00:00Z', 'edge_fresh_bailey_leads_dociq'
        );
        """
    )
    # 5. Non-mutable predicate: PART_OF (structural, not role)
    conn.execute(
        """
        INSERT INTO edge VALUES (
            'edge_structural_part_of',
            'workstream_fcdb',
            'PART_OF',
            'org_partner_advisory',
            'Part of PartnerAdvisory scope',
            'DOCUMENTED', 'HIGH', 'HIGH', NULL, NULL, 0,
            'source_nea_status_2026_06_29',
            '2026-06-29T12:00:00Z', '2026-06-29T12:00:00Z', NULL
        );
        """
    )
    # 6. Fallback date edge: source has NULL date, uses edge created_at (2026-08-01 -> 78 days -> P2)
    conn.execute(
        """
        INSERT INTO edge VALUES (
            'edge_fallback_date',
            'person_abhijit_mahishi',
            'CONTRIBUTES_TO',
            'workstream_fcdb',
            'Fallback edge without source date',
            'DOCUMENTED', 'HIGH', 'HIGH', NULL, NULL, 0,
            'source_no_date',
            '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z', NULL
        );
        """
    )

    conn.commit()
    conn.close()


@pytest.fixture
def isolated_db(tmp_path: Path) -> Path:
    db_file = tmp_path / "test_graph.sqlite"
    _init_test_graph_db(db_file)
    return db_file


def test_e2e_staleness_auditor_identifies_abhijit_mahishi_p1(isolated_db: Path) -> None:
    auditor = StalenessAuditor(isolated_db, warning_ttl_days=45, critical_ttl_days=90)
    report = auditor.audit(reference_date="2026-10-18")

    assert report.total_active_edges_scanned == 5
    assert report.mutable_edges_checked == 4
    assert report.stale_count_p1 == 2
    assert report.stale_count_p2 == 1

    p1_edge_ids = {r.edge_id for r in report.records if r.staleness_tier == "P1"}
    assert "edge_v36_person_abhijit_mahishi_leads_fcdb" in p1_edge_ids
    assert "edge_v44_row_7_tech_lead_abhijit_mahishi" in p1_edge_ids

    abhijit_lead = next(r for r in report.records if r.edge_id == "edge_v36_person_abhijit_mahishi_leads_fcdb")
    assert abhijit_lead.from_node_id == "person_abhijit_mahishi"
    assert abhijit_lead.to_node_id == "workstream_fcdb"
    assert abhijit_lead.age_days >= 110
    assert abhijit_lead.recommended_action == "RETIRE_OR_REPLACE"


def test_e2e_staleness_auditor_filters_superseded_and_fresh(isolated_db: Path) -> None:
    auditor = StalenessAuditor(isolated_db)
    report = auditor.audit(reference_date="2026-10-18")

    flagged_ids = {r.edge_id for r in report.records}
    assert "edge_superseded_old_lead" not in flagged_ids
    assert "edge_fresh_bailey_leads_dociq" not in flagged_ids
    assert "edge_structural_part_of" not in flagged_ids


def test_e2e_staleness_auditor_non_destructive(isolated_db: Path) -> None:
    before_hash = hashlib.sha256(isolated_db.read_bytes()).hexdigest()
    auditor = StalenessAuditor(isolated_db)
    report = auditor.audit(reference_date="2026-10-18")
    after_hash = hashlib.sha256(isolated_db.read_bytes()).hexdigest()

    assert before_hash == after_hash
    assert len(report.records) > 0


def test_e2e_staleness_auditor_export_json_manifest(isolated_db: Path, tmp_path: Path) -> None:
    auditor = StalenessAuditor(isolated_db)
    report = auditor.audit(reference_date=date(2026, 10, 18))
    target_out = tmp_path / "reports" / "staleness_audit.json"

    written_path = export_staleness_report(report, target_out)
    assert written_path.is_file()

    payload = json.loads(written_path.read_text(encoding="utf-8"))
    assert payload["reference_date"] == "2026-10-18"
    assert payload["stale_count_p1"] == 2
    assert len(payload["records"]) == 3


def test_edge_case_malformed_and_missing_dates(isolated_db: Path) -> None:
    auditor = StalenessAuditor(isolated_db)
    report = auditor.audit(reference_date="2026-10-18")

    fallback_record = next((r for r in report.records if r.edge_id == "edge_fallback_date"), None)
    assert fallback_record is not None
    assert fallback_record.staleness_tier == "P2"
    assert fallback_record.age_days >= 75
