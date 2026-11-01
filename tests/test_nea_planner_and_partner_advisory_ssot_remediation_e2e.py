"""End-to-end and adversarial edge-case tests for NEA Planner and PartnerAdvisory SSOT graph remediation."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tools.tier2_dedup_core.nea_planner_and_partner_advisory_ssot_remediation import (
    APPROVAL_REF,
    FALSE_NOEMI_REPORTS_TO_ID,
    NEA_OCT20_PDF_SOURCE_ID,
    NEA_OCT28_CSV_SOURCE_ID,
    NOEMI_NODE_ID,
    ORG_CHART_SOURCE_ID,
    PRODUCT_TEAM_SUPPORT_NODE_ID,
    SHIFTED_ASSERTION_IDS,
    partner_advisory_ALIGNMENT_SOURCE_ID,
    _delete_assertions_by_ids,
    apply_nea_planner_and_partner_advisory_remediation_on_db,
    promote_nea_planner_and_partner_advisory_ssot_generation,
)


def _seed_hermetic_ssot_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE governance_approval (
            approval_id TEXT PRIMARY KEY,
            approval_reference TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'approved',
            active INTEGER NOT NULL DEFAULT 1,
            scope_json TEXT
        );
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY,
            source_type TEXT,
            source_name TEXT,
            source_date TEXT,
            authority_rank INTEGER DEFAULT 9,
            source_posture TEXT DEFAULT 'authoritative_governed',
            cold_archive_ref TEXT,
            notes TEXT,
            created_at TEXT
        );
        CREATE TABLE source_version (
            source_version_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            version_number INTEGER NOT NULL,
            source_fingerprint TEXT,
            content_hash TEXT,
            source_date_normalized TEXT,
            source_date_precision TEXT,
            source_date_status TEXT,
            authority_rank INTEGER,
            scope_text TEXT,
            posture_text TEXT,
            created_at TEXT
        );
        CREATE TABLE extraction_run (
            extraction_run_id TEXT PRIMARY KEY
        );
        CREATE TABLE evidence_item (
            evidence_id TEXT PRIMARY KEY,
            evidence_fingerprint TEXT,
            source_id TEXT,
            source_version_id TEXT,
            locator_kind TEXT,
            locator_value TEXT,
            section_name TEXT,
            direct_quote TEXT,
            evidence_directness TEXT,
            source_authority_rank INTEGER,
            extraction_confidence REAL,
            corroboration_group TEXT,
            location_status TEXT,
            created_at TEXT
        );
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            display_name TEXT,
            normalized_name TEXT,
            description TEXT,
            status TEXT NOT NULL DEFAULT 'canonical',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE node_state (
            node_id TEXT PRIMARY KEY,
            lifecycle_state TEXT DEFAULT 'active',
            currentness_state TEXT DEFAULT 'current',
            retrieval_eligibility TEXT DEFAULT 'eligible',
            resolution_state TEXT DEFAULT 'resolved',
            legacy_status_text TEXT,
            updated_at TEXT
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            assertion_fingerprint TEXT,
            subject_node_id TEXT NOT NULL,
            predicate TEXT NOT NULL,
            legacy_predicate TEXT,
            object_node_id TEXT,
            object_literal TEXT,
            object_literal_type TEXT,
            assertion_type TEXT,
            atomicity_state TEXT,
            scope_text TEXT,
            primary_source_id TEXT,
            extraction_run_id TEXT,
            extraction_method TEXT,
            origin_type TEXT,
            confidence_label TEXT,
            confidence_score REAL,
            validation_state TEXT,
            currentness_state TEXT DEFAULT 'current',
            resolution_state TEXT,
            canonical_status TEXT DEFAULT 'canonical',
            canonical_eligible INTEGER DEFAULT 1,
            retrieval_eligible INTEGER DEFAULT 1,
            materiality TEXT,
            classification TEXT,
            allowed_audiences_json TEXT,
            hitl_required INTEGER DEFAULT 0,
            legacy_record_type TEXT,
            legacy_record_ids_json TEXT,
            approval_reference TEXT,
            qualifiers_json TEXT,
            superseded_by_assertion_id TEXT,
            superseded_at TEXT,
            valid_from TEXT,
            valid_to TEXT,
            date_precision TEXT,
            date_status TEXT,
            recorded_at TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE assertion_evidence (
            assertion_id TEXT NOT NULL,
            evidence_id TEXT NOT NULL,
            evidence_relationship TEXT DEFAULT 'supports',
            independent_corroboration INTEGER DEFAULT 1,
            created_at TEXT,
            PRIMARY KEY (assertion_id, evidence_id)
        );
        CREATE TABLE search_document (
            search_document_id TEXT PRIMARY KEY,
            object_type TEXT,
            object_id TEXT,
            title TEXT,
            body TEXT,
            canonical_status TEXT,
            currentness_state TEXT,
            retrieval_eligible INTEGER DEFAULT 1,
            source_id TEXT,
            assertion_id TEXT,
            updated_at TEXT
        );
        CREATE TABLE entity_merge_event (
            merge_event_id TEXT PRIMARY KEY,
            canonical_node_id TEXT,
            event_type TEXT,
            status TEXT,
            approval_reference TEXT,
            reason TEXT,
            created_at TEXT,
            completed_at TEXT
        );
        CREATE TABLE entity_redirect (
            from_node_id TEXT PRIMARY KEY,
            to_node_id TEXT NOT NULL,
            merge_event_id TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT
        );
        CREATE TABLE edge (
            edge_id TEXT PRIMARY KEY,
            from_node_id TEXT,
            to_node_id TEXT,
            edge_type TEXT,
            source_id TEXT,
            superseded_by TEXT,
            updated_at TEXT
        );
        CREATE TABLE audit_event (
            audit_event_id TEXT PRIMARY KEY,
            actor_or_process TEXT,
            action TEXT,
            record_type TEXT,
            record_id TEXT,
            previous_value_json TEXT,
            new_value_json TEXT,
            source_id TEXT,
            event_timestamp TEXT,
            reason TEXT,
            validation_reference TEXT,
            approval_reference TEXT,
            migration_or_ingestion_run_id TEXT,
            release_version TEXT
        );
        """
    )
    conn.execute("INSERT INTO extraction_run VALUES ('er-1')")
    for sid in (ORG_CHART_SOURCE_ID, NEA_OCT20_PDF_SOURCE_ID, NEA_OCT28_CSV_SOURCE_ID):
        conn.execute(
            "INSERT INTO source (source_id, source_type, source_name, source_date) VALUES (?, 'doc', ?, '2026-10-20')",
            (sid, sid),
        )
        conn.execute(
            "INSERT INTO evidence_item (evidence_id, source_id, section_name, direct_quote) VALUES (?, ?, 'Sec 1', ?)",
            (f"ev-{sid}", sid, None if sid == ORG_CHART_SOURCE_ID else "Quote"),
        )

    # Seed core nodes including split nodes and workstreams
    seed_nodes = [
        (NOEMI_NODE_ID, "stakeholder", "Noemi Monnerville", "canonical"),
        ("org_partner_advisory", "org", "PartnerAdvisory", "canonical"),
        ("workstream_nea_jarvis", "workstream", "NEA JARVIS", "canonical"),
        ("entity-aa4c9aa8c62b20e41dfe21ec2c32", "workstream", "Conduit Q4: Planning and Engineering (PF3)", "canonical"),
        ("entity-1e81e263e90f97622fa7a0f9621a", "workstream", "Construction - Staffing Expansion and Data Enablement", "canonical"),
        ("entity-e6d6c047f48f6b631821b4fc60f1", "workstream", "Contractor Control Tower", "canonical"),
        ("entity-e97eba51589b91b8f2c0b435f33e", "workstream", "Process Atlas", "canonical"),
        ("entity-29131b0d8b26dc7e19c952916f2c", "workstream", "Regulated Capital Asset Management", "canonical"),
        ("workstream_regai", "workstream", "RegAI / Regulatory & Compliance Management", "canonical"),
        ("entity-af7affd4095fd63c0e5234f4b4b2", "workstream", "Purchase Intake Agent Q4", "canonical"),
        ("q3_charter_contractor_management", "workstream", "Contractor Management", "canonical"),
        ("entity-c71f00c4798a227888a4aa5a5285", "workstream", "LENS Visual Asset Intelligence System, 2 of 2 for Maximo Updates", "canonical"),
        ("person_aruna_chinthakindi", "stakeholder", "Aruna Chinthakindi", "legacy prose status"),
        ("person:aruna-tech-lead", "stakeholder", "Aruna", "canonical"),
        ("stakeholder_nicholas_krivov", "stakeholder", "Nicholas Krivov", "legacy prose status"),
        ("person:nick-krivov", "stakeholder", "Nick Krivov", "canonical"),
        ("unresolved_nick_krivov_roster_2026_07_11", "stakeholder", "Nick Krivov", "unresolved"),
        ("person_miles_erickson", "stakeholder", "Miles Erickson", "legacy prose status"),
        ("unresolved_miles_nuclear_nexus_2026_07_10", "stakeholder", "Miles", "unresolved"),
        ("entity-44a7017d5dcb369d624addea379c", "stakeholder", "Justin Kaczmar", "canonical"),
        ("unresolved_justin_vertical_lead_2026_07_10", "stakeholder", "Justin", "unresolved"),
        ("person_stephane", "stakeholder", "Cameron Mercer", "canonical"),
        ("entity-c964cbfa2e5da7f30f4fc3b04d18", "stakeholder", "Cameron Mercer", "canonical"),
        ("person:stefan-practice-lead", "stakeholder", "Stefan", "canonical"),
        ("person:stephan", "stakeholder", "person:stephan", "canonical"),
        ("entity-a4f1bf7fe8c44ff7f217569c16b6", "workstream", "AI FinOps Spend Visibility and Control - Q4 '26", "canonical"),
        ("entity-2196ee9436ce7f6a7e81f76b2914", "workstream", "AI FinOps Spend Visibility", "canonical"),
        ("entity-42fe7b6a80704ffb31a5d2b1ecaa", "workstream", "HR360 - Connected Hiring and Compensation Workflow", "canonical"),
        ("entity-5054c09c61395103df809aafccc2", "workstream", "HR360 - Connected Hiring", "canonical"),
    ]
    for nid, ntype, cname, st in seed_nodes:
        conn.execute(
            "INSERT INTO node (node_id, node_type, canonical_name, display_name, status, active) VALUES (?, ?, ?, ?, ?, 1)",
            (nid, ntype, cname, cname, st),
        )
        conn.execute("INSERT INTO node_state (node_id, legacy_status_text) VALUES (?, NULL)", (nid,))

    # Seed false Noemi REPORTS_TO TJ Kudalis assertion and 15 shifted assertions
    conn.execute(
        "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, scope_text, primary_source_id) "
        "VALUES (?, ?, 'REPORTS_TO', 'org_partner_advisory', 'Noemi Monnerville REPORTS_TO TJ Kudalis.', ?)",
        (FALSE_NOEMI_REPORTS_TO_ID, NOEMI_NODE_ID, ORG_CHART_SOURCE_ID),
    )
    for aid in SHIFTED_ASSERTION_IDS:
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, scope_text, primary_source_id) "
            "VALUES (?, 'person_miles_erickson', 'AFFILIATED_WITH', 'entity-aa4c9aa8c62b20e41dfe21ec2c32', 'Shifted row', ?)",
            (aid, NEA_OCT20_PDF_SOURCE_ID),
        )

    # Seed 90 workstream assertions in source_20261028_nea_planner_workstreams_headcount
    for idx in range(90):
        ws_id = "entity-aa4c9aa8c62b20e41dfe21ec2c32" if idx == 0 else f"ws-oct28-{idx:02d}"
        if idx > 0:
            conn.execute(
                "INSERT INTO node (node_id, node_type, canonical_name, display_name, status, active) VALUES (?, 'workstream', ?, ?, 'canonical', 1)",
                (ws_id, f"Workstream {idx}", f"Workstream {idx}"),
            )
        aid = f"asrt-oct28-{idx:02d}"
        quals = json.dumps({"product_manager": "Hayden Radovich" if idx == 0 else f"PM {idx}"})
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, scope_text, object_literal, primary_source_id, qualifiers_json) "
            "VALUES (?, ?, 'HAS_CLAIM_TEXT', ?, ?, ?, ?)",
            (aid, ws_id, f"Workstream {idx} headcount summary.", f"Workstream {idx} headcount summary.", NEA_OCT28_CSV_SOURCE_ID, quals),
        )
        conn.execute(
            "INSERT INTO search_document (search_document_id, object_type, object_id, title, body, canonical_status, currentness_state, retrieval_eligible, source_id, assertion_id) "
            "VALUES (?, 'assertion', ?, 'Title', 'Body', 'canonical', 'current', 1, ?, ?)",
            (f"sd-{aid}", aid, NEA_OCT28_CSV_SOURCE_ID, aid),
        )
    conn.commit()
    conn.close()


def test_nea_planner_and_partner_advisory_remediation_e2e_and_idempotency(tmp_path: Path) -> None:
    """E2E + exact production regression + idempotent re-execution test."""
    db_path = tmp_path / "graph.sqlite"
    receipt_path = tmp_path / "receipt.json"
    _seed_hermetic_ssot_db(db_path)

    receipt = apply_nea_planner_and_partner_advisory_remediation_on_db(db_path, receipt_path)
    assert receipt["terminal_outcome"] == "PASS"
    assert receipt_path.exists()
    checks_by_name = {c["check"]: c["status"] for c in receipt["physical_proof"]["checks"]}
    for check_name in ("file_changed", "raw_bytes", "integrity", "readback", "row_delta"):
        assert checks_by_name[check_name] == "PASS"

    conn = sqlite3.connect(str(db_path))
    # Verify Issue 1: Noemi Monnerville on Product Team Support with disjoint_workstream_exclusion for NEA JARVIS
    noemi_rows = conn.execute(
        "SELECT predicate, object_node_id, scope_text, qualifiers_json FROM assertion WHERE subject_node_id = ? AND primary_source_id = ?",
        (NOEMI_NODE_ID, partner_advisory_ALIGNMENT_SOURCE_ID),
    ).fetchall()
    assert len(noemi_rows) == 4
    claim_quals = [json.loads(r[3]) for r in noemi_rows if r[0] == "HAS_CLAIM_TEXT"][0]
    assert claim_quals["boundary_type"] == "disjoint_workstream_exclusion"
    assert claim_quals["excluded_workstream"] == "NEA JARVIS"
    assert claim_quals["canonical_workstream"] == "Product Team Support"

    # Verify Issue 2: False Noemi REPORTS_TO TJ Kudalis deleted and 0 NULL quotes in org chart
    assert conn.execute("SELECT COUNT(*) FROM assertion WHERE assertion_id = ?", (FALSE_NOEMI_REPORTS_TO_ID,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM evidence_item WHERE source_id = ? AND direct_quote IS NULL", (ORG_CHART_SOURCE_ID,)).fetchone()[0] == 0

    # Verify Issue 3 & 4: Shifted assertions deleted, PF3 has 12 team members, and all 90 Oct 28 rows have Assigned Team
    for aid in SHIFTED_ASSERTION_IDS:
        assert conn.execute("SELECT COUNT(*) FROM assertion WHERE assertion_id = ?", (aid,)).fetchone()[0] == 0
    pf3_cnt = conn.execute(
        "SELECT COUNT(DISTINCT subject_node_id) FROM assertion WHERE object_node_id = 'entity-aa4c9aa8c62b20e41dfe21ec2c32' AND predicate = 'AFFILIATED_WITH' AND canonical_status = 'canonical'"
    ).fetchone()[0]
    assert pf3_cnt == 12
    oct28_cnt = conn.execute(
        "SELECT COUNT(*) FROM assertion WHERE primary_source_id = ? AND scope_text LIKE '%Assigned Team:%'",
        (NEA_OCT28_CSV_SOURCE_ID,),
    ).fetchone()[0]
    assert oct28_cnt == 90

    # Verify Issue 5: Split nodes merged with active redirects and active node statuses normalized
    assert conn.execute("SELECT COUNT(*) FROM entity_redirect WHERE active = 1").fetchone()[0] == 10
    assert conn.execute("SELECT COUNT(*) FROM node WHERE active = 1 AND status != 'canonical'").fetchone()[0] == 0
    conn.close()

    # Rollback/recovery & idempotency category: re-running produces 0 duplicate assertions and valid integrity
    receipt_2 = apply_nea_planner_and_partner_advisory_remediation_on_db(db_path)
    assert receipt_2["stats"]["purged_shifted_assertions"] == 0
    assert receipt_2["stats"]["merged_split_nodes"] == 0


def test_adversarial_boundary_and_malformed_inputs(tmp_path: Path) -> None:
    """Adversarial edge-case tests covering boundary payloads and malformed inputs."""
    # Category 1: Boundary payload - missing database path raises FileNotFoundError
    with pytest.raises(FileNotFoundError):
        apply_nea_planner_and_partner_advisory_remediation_on_db(tmp_path / "nonexistent.sqlite")

    db_path = tmp_path / "boundary.sqlite"
    _seed_hermetic_ssot_db(db_path)
    conn = sqlite3.connect(str(db_path))
    # Boundary: empty sequence to _delete_assertions_by_ids
    assert _delete_assertions_by_ids(conn, [], "2026-10-29T00:00:00Z") == 0

    # Category 2: Malformed/null qualifiers_json and unassigned PM in Oct 28 workstreams
    conn.execute(
        "UPDATE assertion SET qualifiers_json = NULL, scope_text = 'Workstream 0 summary. Assigned Team: Old Stale Team.' WHERE assertion_id = 'asrt-oct28-01'"
    )
    conn.commit()
    conn.close()

    receipt = apply_nea_planner_and_partner_advisory_remediation_on_db(db_path)
    assert receipt["terminal_outcome"] == "PASS"
    conn = sqlite3.connect(str(db_path))
    scope, quals_raw = conn.execute(
        "SELECT scope_text, qualifiers_json FROM assertion WHERE assertion_id = 'asrt-oct28-01'"
    ).fetchone()
    assert "Old Stale Team" not in scope
    assert "Assigned Team: Unassigned / Open Roles." in scope
    assert json.loads(quals_raw)["assigned_team"] == []
    conn.close()
