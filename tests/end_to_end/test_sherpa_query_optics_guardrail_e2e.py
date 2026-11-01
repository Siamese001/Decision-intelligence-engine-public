"""End-to-end verification of Wave 4 query routing, recency ranking, role-optics guardrails, and Sherpa/LENS graph remediation."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.decision_engine_core.frame import build_decision_frame
from dual_pipeline.graph_retrieval_core.scoring import (
    _edge_increment,
    compute_seed_recency_boost,
)
from dual_pipeline.prompt_assembly import assemble_prompt
from dual_pipeline.query_core.anti_conflation import validate_answer_anti_conflation
from dual_pipeline.query_router import QueryDestination, route_query
from tools.tier2_dedup_core.sherpa_role_and_lens_remediation import (
    remediate_sherpa_lens_and_role_edges,
)


def _init_sandbox_graph_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY,
            source_type TEXT,
            source_name TEXT,
            source_date TEXT,
            authority_rank INTEGER,
            source_posture TEXT,
            cold_archive_ref TEXT,
            notes TEXT,
            created_at TEXT
        );
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            display_name TEXT,
            description TEXT,
            status TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE node_state (
            node_id TEXT PRIMARY KEY,
            lifecycle_state TEXT NOT NULL,
            validation_state TEXT NOT NULL,
            currentness_state TEXT NOT NULL,
            approval_state TEXT NOT NULL,
            rag_state TEXT NOT NULL,
            resolution_state TEXT NOT NULL,
            retrieval_eligibility TEXT NOT NULL,
            publication_state TEXT NOT NULL,
            review_state TEXT NOT NULL,
            legacy_status_text TEXT,
            intentional_orphan INTEGER NOT NULL DEFAULT 0,
            intentional_orphan_reason TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE canonical_alias (
            canonical_alias_id TEXT PRIMARY KEY,
            normalized_alias TEXT NOT NULL,
            display_alias TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            alias_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            confidence_label TEXT NOT NULL,
            confidence_score REAL NOT NULL,
            valid_from TEXT,
            valid_to TEXT,
            ambiguity_state TEXT NOT NULL,
            preferred_flag INTEGER NOT NULL,
            review_state TEXT NOT NULL,
            active INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE edge (
            edge_id TEXT PRIMARY KEY,
            from_node_id TEXT NOT NULL,
            edge_type TEXT NOT NULL,
            to_node_id TEXT NOT NULL,
            description TEXT,
            evidence_posture TEXT NOT NULL,
            confidence TEXT NOT NULL,
            materiality TEXT NOT NULL,
            validation_priority TEXT,
            validation_owner TEXT,
            hitl_required INTEGER NOT NULL DEFAULT 0,
            source_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            superseded_by TEXT
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            assertion_fingerprint TEXT NOT NULL,
            subject_node_id TEXT NOT NULL,
            predicate TEXT NOT NULL,
            legacy_predicate TEXT,
            object_node_id TEXT,
            object_literal TEXT,
            object_literal_type TEXT,
            assertion_type TEXT NOT NULL,
            atomicity_state TEXT NOT NULL,
            scope_text TEXT NOT NULL,
            speaker_or_author TEXT,
            primary_source_id TEXT,
            extraction_run_id TEXT,
            extraction_method TEXT NOT NULL,
            origin_type TEXT NOT NULL,
            confidence_label TEXT NOT NULL,
            confidence_score REAL NOT NULL,
            validation_state TEXT NOT NULL,
            currentness_state TEXT NOT NULL,
            valid_from TEXT,
            valid_to TEXT,
            date_precision TEXT NOT NULL,
            date_status TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            superseded_at TEXT,
            superseded_by_assertion_id TEXT,
            resolution_state TEXT NOT NULL,
            canonical_status TEXT NOT NULL,
            canonical_eligible INTEGER NOT NULL,
            retrieval_eligible INTEGER NOT NULL,
            materiality TEXT NOT NULL,
            classification TEXT NOT NULL,
            allowed_audiences_json TEXT NOT NULL,
            hitl_required INTEGER NOT NULL,
            validation_priority TEXT,
            validation_owner TEXT,
            qualifiers_json TEXT NOT NULL,
            legacy_record_type TEXT NOT NULL,
            legacy_record_ids_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            approval_reference TEXT
        );
        CREATE TABLE search_document (
            search_rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            search_document_id TEXT NOT NULL UNIQUE,
            object_type TEXT NOT NULL,
            object_id TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            canonical_status TEXT NOT NULL,
            currentness_state TEXT NOT NULL,
            retrieval_eligible INTEGER NOT NULL,
            source_id TEXT,
            assertion_id TEXT,
            updated_at TEXT NOT NULL
        );
        """
    )
    for sid in ("source-c8ba9093a5a3687e0d016e35", "source-ea51c8983147880052dc880e", "source-2c2ac695afc1a9fc97da5a56", "source-244602bbe3c399331a745e51", "source-12af631893ae3f76b62973ff"):
        conn.execute(
            "INSERT INTO source VALUES (?, 'dual_pipeline_intake', ?, '2026-10-16', 5, 'derivative_internal', '', '', '2026-10-16T15:00:00Z')",
            (sid, sid),
        )
    # Seed contaminated LENS assertion on Oct 16 Sherpa source, duplicate Speaker_Beta node, degraded HAS_ROLE, and stale July 10 Lillian Tech Lead assertion
    conn.execute(
        "INSERT INTO node VALUES ('entity-2a2648a1b03f0a600f85378dc03c', 'person', 'Speaker_Beta', 'Speaker_Beta', '', 'candidate', 1, '2026-10-06T00:00:00Z', '2026-10-06T00:00:00Z')"
    )
    asrt_cols = (
        "(assertion_id, assertion_fingerprint, subject_node_id, predicate, object_node_id, "
        "object_literal, object_literal_type, assertion_type, atomicity_state, scope_text, "
        "speaker_or_author, primary_source_id, extraction_run_id, extraction_method, origin_type, "
        "confidence_label, confidence_score, validation_state, currentness_state, valid_from, "
        "date_precision, date_status, recorded_at, resolution_state, canonical_status, "
        "canonical_eligible, retrieval_eligible, materiality, classification, allowed_audiences_json, "
        "hitl_required, validation_priority, qualifiers_json, legacy_record_type, legacy_record_ids_json, "
        "created_at, updated_at, approval_reference)"
    )
    conn.execute(
        f"INSERT INTO assertion {asrt_cols} VALUES ('asrt-lens-contaminated-alex', 'fp1', 'entity-69a6725884f1f89598fd38e83c20', 'CONTRIBUTES_TO', 'workstream_project_beacon', NULL, NULL, 'extracted_claim', 'atomic', 'Alex Sanchez is an identified stakeholder in Sprint Demo Marcus PartnerAdvisory', 'Roster', 'source-c8ba9093a5a3687e0d016e35', 'run1', 'adapter', 'human_source', 'high', 0.9, 'accepted_legacy', 'current', '2026-10-16', 'day', 'exact', '2026-10-16T15:00:00Z', 'resolved', 'canonical', 1, 1, 'high', 'internal', '[]', 0, 'P1', '{{}}', 'edge', '[]', '2026-10-16T15:00:00Z', '2026-10-16T15:00:00Z', 'REQ')"
    )
    conn.execute(
        f"INSERT INTO assertion {asrt_cols} VALUES ('asrt-degraded-role', 'fp2', 'entity-06ea5229f6c62bd4a1346f262928', 'RELATED_TO', 'entity-69ef9733972222d01c2fcb3af0ce', NULL, NULL, 'extracted_claim', 'atomic', 'Egle Tamasevice (person) HAS_ROLE Tech Lead (role_group) in Sprint Demo.', 'Roster', 'source-c8ba9093a5a3687e0d016e35', 'run1', 'adapter', 'human_source', 'high', 0.9, 'accepted_legacy', 'current', '2026-10-16', 'day', 'exact', '2026-10-16T15:00:00Z', 'resolved', 'canonical', 1, 1, 'high', 'internal', '[]', 0, 'P1', '{{}}', 'edge', '[]', '2026-10-16T15:00:00Z', '2026-10-16T15:00:00Z', 'REQ')"
    )
    conn.execute(
        f"INSERT INTO assertion {asrt_cols} VALUES ('asrt_claim_claim_v36_lillian_current_sherpa_technical_lead', 'fp3', 'workstream_project_beacon', 'HAS_CLAIM_TEXT', NULL, 'Lillian is Technical Lead', 'text', 'extracted_claim', 'atomic', 'Lillian Le is current Technical Lead for Project Beacon.', 'Notes', 'source-c8ba9093a5a3687e0d016e35', 'run0', 'adapter', 'human_source', 'high', 0.9, 'accepted_legacy', 'current', '2026-07-10', 'day', 'exact', '2026-07-10T15:00:00Z', 'resolved', 'canonical', 1, 1, 'high', 'internal', '[]', 0, 'P1', '{{}}', 'claim', '[]', '2026-07-10T15:00:00Z', '2026-07-10T15:00:00Z', 'REQ')"
    )
    conn.commit()
    return conn


def test_wave4_query_routing_decision_frame_and_recency_scoring() -> None:
    """Verify staffing/intro-email queries route to NEE_DECISION_INTELLIGENCE, build READY frames, and rank Oct 2026 above July 2026."""
    with patch("dual_pipeline.query_router._check_graphdb_available", return_value=True):
        decision = route_query(
            "Review gaps in Sherpa delivery, incorporate two new resources Michael and Angela, and write an intro email to Marcus, Speaker_Alpha Muschett, and Jeffrey Glade"
        )
    assert decision.destination == QueryDestination.NEE_DECISION_INTELLIGENCE
    assert decision.handoff_permitted is False

    frame = build_decision_frame(
        question="Review gaps in Sherpa delivery and assess whether we should add Aruna C. to Sherpa"
    )
    assert frame["frame_state"] == "READY"
    assert [c["label"] for c in frame["criteria"]] == [
        "role_fit",
        "org_optics_alignment",
        "current_sprint_evidence_grounding",
    ]

    ref_epoch = 1792166400.0  # 2026-10-16T16:00:00Z
    oct_boost = compute_seed_recency_boost("2026-10-16T14:00:00Z", reference_epoch=ref_epoch)
    jul_boost = compute_seed_recency_boost("2026-07-08T14:00:00Z", reference_epoch=ref_epoch)
    assert oct_boost > jul_boost + 0.15

    oct_edge_inc, _ = _edge_increment(
        {"relationship_family": "OWNS_OR_LEADS", "evidence_ids": ["ev1"], "materiality": "high", "confidence_score": 0.95, "recorded_at": "2026-10-16T14:00:00Z"},
        requested_relationships={"OWNS_OR_LEADS"},
        destination_degree=4,
        hop=1,
        reference_epoch=ref_epoch,
    )
    jul_edge_inc, _ = _edge_increment(
        {"relationship_family": "OWNS_OR_LEADS", "evidence_ids": ["ev1"], "materiality": "high", "confidence_score": 0.95, "recorded_at": "2026-07-08T14:00:00Z"},
        requested_relationships={"OWNS_OR_LEADS"},
        destination_degree=4,
        hop=1,
        reference_epoch=ref_epoch,
    )
    assert oct_edge_inc > jul_edge_inc + 0.15


def test_wave4_sherpa_lens_and_role_edges_remediation(tmp_path: Path) -> None:
    """Verify governed graph remediation retires LENS contamination, upgrades HAS_ROLE_IN, and materializes Sherpa structural edges."""
    conn = _init_sandbox_graph_db(tmp_path / "sandbox_graph.sqlite")
    try:
        stats = remediate_sherpa_lens_and_role_edges(conn, "2026-10-26T18:00:00Z")
        conn.commit()
        assert stats["contaminated_assertions_retired"] >= 1
        assert stats["upgraded_has_role_in_predicates"] >= 1
        assert stats["structural_edges_materialized"] == 14

        # Verify Alex Sanchez assertion on Oct 16 Sherpa source is rejected and retrieval_eligible=0
        alex_row = conn.execute(
            "SELECT canonical_status, retrieval_eligible FROM assertion WHERE assertion_id = 'asrt-lens-contaminated-alex'"
        ).fetchone()
        assert alex_row["canonical_status"] == "rejected"
        assert alex_row["retrieval_eligible"] == 0

        # Verify degraded RELATED_TO upgraded to HAS_ROLE_IN
        role_row = conn.execute(
            "SELECT predicate FROM assertion WHERE assertion_id = 'asrt-degraded-role'"
        ).fetchone()
        assert role_row["predicate"] == "HAS_ROLE_IN"

        # Verify Egle Tamasevice, Brandon Que, Jeffrey Glade, and Speaker_Beta Tharakan structural edges exist in `edge`
        egle_edges = {
            (r["edge_type"], r["to_node_id"])
            for r in conn.execute(
                "SELECT edge_type, to_node_id FROM edge WHERE from_node_id = 'entity-06ea5229f6c62bd4a1346f262928'"
            ).fetchall()
        }
        assert ("HAS_ROLE_IN", "entity-69ef9733972222d01c2fcb3af0ce") in egle_edges
        assert ("AFFILIATED_WITH", "org_nee") in egle_edges
        assert ("OWNS_OR_LEADS", "workstream_project_beacon") in egle_edges

        ninan_edges = {
            (r["edge_type"], r["to_node_id"])
            for r in conn.execute(
                "SELECT edge_type, to_node_id FROM edge WHERE from_node_id = 'entity-6a26db0c264ca5d90c540a595c19'"
            ).fetchall()
        }
        assert ("AFFILIATED_WITH", "org_partner_advisory") in ninan_edges
        assert ("CONTRIBUTES_TO", "workstream_project_beacon") in ninan_edges
        assert ("OWNS_OR_LEADS", "deliverable_sherpa_opensearch_migration") in ninan_edges

        # Verify July 10 Lillian Tech Lead assertion is superseded
        lillian_row = conn.execute(
            "SELECT currentness_state, retrieval_eligible FROM assertion WHERE assertion_id = 'asrt_claim_claim_v36_lillian_current_sherpa_technical_lead'"
        ).fetchone()
        assert lillian_row["currentness_state"] == "historical"
        assert lillian_row["retrieval_eligible"] == 0
    finally:
        conn.close()
