"""E2E tests for Wave 2 client vs. vendor role authority, HAS_ROLE_IN preservation, and UWG node promotion."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from dual_pipeline.graph_signal_core.mutex_invalidation import (
    supersede_conflicting_assertions,
)
from dual_pipeline.intake_core.change_set_helpers import resolve_relationship_predicate
from dual_pipeline.intake_core.planning import _is_role_change
from dual_pipeline.intake_core.routing import _is_role_change_candidate
from dual_pipeline.uwg_core.gateway import _promote_single_candidate


def test_affiliation_role_authority_and_uwg_node_promotion_e2e(tmp_path: Path) -> None:
    """Verify HAS_ROLE maps to HAS_ROLE_IN, UWG promotes candidate nodes, and client/vendor tracks co-exist."""
    # 1. Verify HAS_ROLE predicate preservation (zero degradation to RELATED_TO)
    egle_role_cand = {
        "candidate_id": "cand-egle-role",
        "candidate_type": "architecture_relationship",
        "normalized_text": "Egle Tamasevice HAS_ROLE Tech Lead.",
    }
    assert resolve_relationship_predicate(egle_role_cand, "cand-egle-role", {}) == "HAS_ROLE_IN"
    assert _is_role_change(egle_role_cand, "architecture_relationship") is True
    assert _is_role_change_candidate(egle_role_cand) is True

    # 2. Verify UWG candidate assertion promotion also promotes subject/object nodes + search_document
    db_path = tmp_path / "wave2_e2e.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT,
            canonical_name TEXT,
            description TEXT,
            status TEXT,
            updated_at TEXT
        );
        CREATE TABLE node_state (
            node_id TEXT PRIMARY KEY,
            validation_state TEXT,
            approval_state TEXT,
            retrieval_eligibility TEXT,
            legacy_status_text TEXT,
            updated_at TEXT
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_node_id TEXT,
            scope_text TEXT,
            qualifiers_json TEXT,
            validation_state TEXT,
            canonical_status TEXT,
            canonical_eligible INTEGER,
            retrieval_eligible INTEGER,
            currentness_state TEXT,
            confidence_score REAL,
            confidence_label TEXT,
            superseded_at TEXT,
            superseded_by_assertion_id TEXT,
            approval_reference TEXT,
            hitl_required INTEGER,
            validation_priority TEXT,
            validation_owner TEXT,
            primary_source_id TEXT,
            recorded_at TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE search_document (
            search_document_id TEXT PRIMARY KEY,
            object_type TEXT,
            object_id TEXT,
            node_id TEXT,
            assertion_id TEXT,
            title TEXT,
            body TEXT,
            canonical_status TEXT,
            currentness_state TEXT,
            retrieval_eligible INTEGER,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE edge (
            edge_id TEXT PRIMARY KEY,
            from_node_id TEXT,
            to_node_id TEXT,
            predicate TEXT,
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

    conn.execute(
        "INSERT INTO node VALUES ('entity-06ea5229f6c62bd4a1346f262928', 'stakeholder', 'Egle Tamasevice', 'Tech Lead affiliated with Apex Global Enterprises.', 'candidate', '2026-09-09')"
    )
    conn.execute(
        "INSERT INTO node_state VALUES ('entity-06ea5229f6c62bd4a1346f262928', 'needs_review', 'candidate', 'eligible', 'candidate', '2026-09-09')"
    )
    conn.execute(
        "INSERT INTO node VALUES ('entity-69ef9733972222d01c2fcb3af0ce', 'role_group', 'Tech Lead', 'Professional role: Tech Lead.', 'candidate', '2026-09-09')"
    )
    conn.execute(
        "INSERT INTO node_state VALUES ('entity-69ef9733972222d01c2fcb3af0ce', 'needs_review', 'candidate', 'eligible', 'candidate', '2026-09-09')"
    )
    conn.execute(
        """INSERT INTO assertion
           (assertion_id, subject_node_id, predicate, object_node_id, scope_text, qualifiers_json,
            validation_state, canonical_status, canonical_eligible, retrieval_eligible,
            currentness_state, confidence_score, confidence_label, primary_source_id, created_at, updated_at)
           VALUES ('asrt-egle-role', 'entity-06ea5229f6c62bd4a1346f262928', 'HAS_ROLE_IN',
                   'entity-69ef9733972222d01c2fcb3af0ce', 'Egle Tamasevice HAS_ROLE Tech Lead.', '{}',
                   'needs_review', 'candidate', 0, 0, 'current', 0.9, 'high', 'source-sherpa', '2026-09-09', '2026-09-09')"""
    )
    conn.execute(
        """INSERT INTO search_document
           (search_document_id, object_type, object_id, assertion_id, title, body,
            canonical_status, currentness_state, retrieval_eligible, created_at, updated_at)
           VALUES ('sd-egle-role', 'assertion', 'asrt-egle-role', 'asrt-egle-role', 'Egle Tech Lead', 'Egle Tamasevice HAS_ROLE Tech Lead.',
                   'candidate', 'current', 0, '2026-09-09', '2026-09-09')"""
    )

    _promote_single_candidate(
        conn,
        assertion_id="asrt-egle-role",
        validation_state="accepted_legacy",
        evidence={"approval_reference": "REQ_TEST_1", "bindings": {"asrt-egle-role": ["ev-1"]}},
        decision={"promotion_id": "prom-1", "origin_run_id": "run-1", "canonical_digest": {"value": "dig-1"}},
        evaluation={"evaluation_id": "eval-1", "canonical_digest": {"value": "edig-1"}},
        now="2026-10-26T18:00:00Z",
    )

    egle_node = conn.execute("SELECT status FROM node WHERE node_id='entity-06ea5229f6c62bd4a1346f262928'").fetchone()
    egle_state = conn.execute("SELECT approval_state FROM node_state WHERE node_id='entity-06ea5229f6c62bd4a1346f262928'").fetchone()
    egle_sd = conn.execute("SELECT COUNT(*) FROM search_document WHERE object_type='node' AND node_id='entity-06ea5229f6c62bd4a1346f262928'").fetchone()[0]
    assert egle_node["status"] == "canonical"
    assert egle_state["approval_state"] == "approved"
    assert egle_sd == 1

    # 3. Verify affiliation-partitioned mutex: NEE Client Tech Lead (Egle) does NOT overwrite NEE Product Owner (Jeffrey) or PartnerAdvisory Lead (Andy), but supersedes same-track prior PartnerAdvisory lead (Lillian)
    conn.execute("INSERT INTO node VALUES ('person_lillian', 'stakeholder', 'Lillian Le', 'PartnerAdvisory Lead', 'canonical', '2026-07-10')")
    conn.execute("INSERT INTO node VALUES ('person_andy_k', 'stakeholder', 'Andy Kilinskis', 'PartnerAdvisory Lead', 'canonical', '2026-10-16')")
    conn.execute("INSERT INTO node VALUES ('entity-79a958f8aa3bd05f10e7fb6bf57f', 'stakeholder', 'Jeffrey Glade', 'Product Manager at Apex Global Enterprises.', 'canonical', '2026-10-16')")

    conn.execute(
        """INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, scope_text, qualifiers_json, canonical_status, currentness_state, confidence_score)
           VALUES ('asrt-lillian-leads', 'person_lillian', 'OWNS_OR_LEADS', 'workstream_project_beacon', 'Lillian Le leads Sherpa tech track.', '{"affiliation": "PartnerAdvisory", "leadership_track": "tech_lead"}', 'canonical', 'current', 0.95)"""
    )
    conn.execute(
        """INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, scope_text, qualifiers_json, canonical_status, currentness_state, confidence_score)
           VALUES ('asrt-egle-leads', 'entity-06ea5229f6c62bd4a1346f262928', 'OWNS_OR_LEADS', 'workstream_project_beacon', 'Egle Tamasevice Tech Lead for Project Beacon.', '{"affiliation": "Apex Global Enterprises", "leadership_track": "client_tech_lead"}', 'canonical', 'current', 0.95)"""
    )
    conn.execute(
        """INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, scope_text, qualifiers_json, canonical_status, currentness_state, confidence_score)
           VALUES ('asrt-jeff-owns', 'entity-79a958f8aa3bd05f10e7fb6bf57f', 'OWNS_OR_LEADS', 'workstream_project_beacon', 'Jeffrey Glade Business Owner for Project Beacon.', '{"affiliation": "Apex Global Enterprises", "leadership_track": "product_owner"}', 'canonical', 'current', 0.95)"""
    )
    conn.execute(
        """INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, scope_text, qualifiers_json, canonical_status, currentness_state, confidence_score)
           VALUES ('asrt-andy-leads', 'person_andy_k', 'OWNS_OR_LEADS', 'workstream_project_beacon', 'Andy Kilinskis PartnerAdvisory tech lead for Project Beacon.', '{"affiliation": "PartnerAdvisory", "leadership_track": "tech_lead"}', 'canonical', 'current', 0.95)"""
    )

    # Egle (org_nee, tech_lead) should NOT supersede Jeffrey (org_nee, product_owner) or Lillian/Andy (org_partner_advisory)
    sup_by_egle = supersede_conflicting_assertions(
        conn,
        incoming_assertion_id="asrt-egle-leads",
        subject_node_id="entity-06ea5229f6c62bd4a1346f262928",
        predicate="OWNS_OR_LEADS",
        object_node_id="workstream_project_beacon",
    )
    assert sup_by_egle == []

    # Andy (org_partner_advisory, tech_lead) SHOULD supersede Lillian (org_partner_advisory, tech_lead), while leaving Egle and Jeffrey untouched
    sup_by_andy = supersede_conflicting_assertions(
        conn,
        incoming_assertion_id="asrt-andy-leads",
        subject_node_id="person_andy_k",
        predicate="OWNS_OR_LEADS",
        object_node_id="workstream_project_beacon",
    )
    assert sup_by_andy == ["asrt-lillian-leads"]
    conn.close()
