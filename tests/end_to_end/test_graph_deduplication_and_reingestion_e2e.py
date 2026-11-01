"""Wave 6 E2E tests for graph mutation deduplication, entity identity resolution, and re-ingestion idempotency."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from dual_pipeline.core import connect_sqlite, digest, seal_artifact, sha256_file, stable_id, utc_now
from dual_pipeline.graph_delta_core.counts import _graph_counts, _planned_increments
from dual_pipeline.graph_writer_core.writer import GovernedGraphWriter
from dual_pipeline.identity_core.catalog import GraphEntityCatalog
from dual_pipeline.identity_core.matching import are_node_types_compatible
from dual_pipeline.identity_core.resolver import resolve_bundle
from dual_pipeline.intake_core.change_set import build_graph_change_set
from dual_pipeline.intake_core.comparison import compare_candidates
from dual_pipeline.intake_core.dispositions import build_dispositions
from dual_pipeline.intake_core.routing import build_routes
from dual_pipeline.normalization_core.meeting import normalize_evidence_envelope
from dual_pipeline.normalization_core.meeting_entity_helpers import map_entity_type
from tools.prepare_evidence_envelope_intake import _adapt_v53_projection_to_envelope


def _seed_test_graph(graph_path: Path) -> None:
    conn = sqlite3.connect(str(graph_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE node_type_registry (
            node_type_id TEXT PRIMARY KEY,
            canonical_name TEXT,
            definition TEXT,
            parent_type_id TEXT,
            required_properties_json TEXT,
            optional_properties_json TEXT,
            identity_rule TEXT,
            example_node_id TEXT,
            registry_state TEXT,
            replacement_type_id TEXT,
            retrieval_use TEXT,
            created_at TEXT
        );
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT,
            canonical_name TEXT,
            display_name TEXT,
            description TEXT,
            status TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE node_state (
            node_id TEXT PRIMARY KEY,
            lifecycle_state TEXT,
            validation_state TEXT,
            currentness_state TEXT,
            approval_state TEXT,
            rag_state TEXT,
            resolution_state TEXT,
            retrieval_eligibility TEXT,
            publication_state TEXT,
            review_state TEXT,
            legacy_status_text TEXT,
            intentional_orphan INTEGER,
            intentional_orphan_reason TEXT,
            updated_at TEXT
        );
        CREATE TABLE canonical_alias (
            alias_id TEXT PRIMARY KEY,
            normalized_alias TEXT,
            entity_id TEXT,
            active INTEGER DEFAULT 1
        );
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
        CREATE TABLE source_version (
            source_version_id TEXT PRIMARY KEY,
            source_id TEXT,
            version_number INTEGER,
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
            extraction_run_id TEXT PRIMARY KEY,
            algorithm_version TEXT,
            baseline_graph_sha256 TEXT,
            input_manifest_sha256 TEXT,
            started_at TEXT,
            completed_at TEXT,
            status TEXT,
            deterministic INTEGER
        );
        CREATE TABLE evidence_item (
            evidence_id TEXT PRIMARY KEY,
            evidence_fingerprint TEXT,
            source_id TEXT,
            source_version_id TEXT,
            locator_kind TEXT,
            locator_value TEXT,
            page_number INTEGER,
            section_name TEXT,
            paragraph_number INTEGER,
            row_reference TEXT,
            slide_number INTEGER,
            transcript_timestamp TEXT,
            direct_quote TEXT,
            evidence_directness TEXT,
            source_authority_rank INTEGER,
            extraction_confidence REAL,
            corroboration_group TEXT,
            location_status TEXT,
            created_at TEXT
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            assertion_fingerprint TEXT,
            subject_node_id TEXT,
            predicate TEXT,
            legacy_predicate TEXT,
            object_node_id TEXT,
            object_literal TEXT,
            object_literal_type TEXT,
            assertion_type TEXT,
            atomicity_state TEXT,
            scope_text TEXT,
            speaker_or_author TEXT,
            primary_source_id TEXT,
            extraction_run_id TEXT,
            extraction_method TEXT,
            origin_type TEXT,
            confidence_label TEXT,
            confidence_score REAL,
            validation_state TEXT,
            currentness_state TEXT,
            valid_from TEXT,
            valid_to TEXT,
            date_precision TEXT,
            date_status TEXT,
            recorded_at TEXT,
            superseded_at TEXT,
            superseded_by_assertion_id TEXT,
            resolution_state TEXT,
            canonical_status TEXT,
            canonical_eligible INTEGER,
            retrieval_eligible INTEGER,
            materiality TEXT,
            classification TEXT,
            allowed_audiences_json TEXT,
            hitl_required INTEGER,
            validation_priority TEXT,
            validation_owner TEXT,
            qualifiers_json TEXT,
            legacy_record_type TEXT,
            legacy_record_ids_json TEXT,
            created_at TEXT,
            updated_at TEXT,
            approval_reference TEXT
        );
        CREATE TABLE assertion_evidence (
            assertion_id TEXT,
            evidence_id TEXT,
            evidence_relationship TEXT,
            independent_corroboration INTEGER,
            created_at TEXT,
            PRIMARY KEY (assertion_id, evidence_id)
        );
        CREATE TABLE search_document (
            search_rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            search_document_id TEXT UNIQUE,
            object_type TEXT,
            object_id TEXT,
            title TEXT,
            body TEXT,
            canonical_status TEXT,
            currentness_state TEXT,
            retrieval_eligible INTEGER,
            source_id TEXT,
            assertion_id TEXT,
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
        CREATE TABLE governance_approval (
            approval_id TEXT PRIMARY KEY,
            approval_reference TEXT,
            decision_scope TEXT,
            status TEXT,
            approved_by TEXT,
            approved_at TEXT,
            scope_json TEXT,
            active INTEGER DEFAULT 1
        );
        """
    )
    now = utc_now()
    for nt in (
        "stakeholder",
        "internal_person",
        "org",
        "technical_anchor",
        "role_group",
        "control",
        "control_protocol",
        "program",
        "workstream",
        "risk",
        "deliverable",
        "open_question",
        "strategy",
        "opportunity",
        "capability",
        "pattern",
    ):
        conn.execute(
            "INSERT INTO node_type_registry VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                nt,
                nt.title(),
                f"Active type {nt}",
                None,
                "[]",
                "[]",
                "Deterministic ID; never auto-merge on name similarity.",
                None,
                "active",
                None,
                "eligible",
                now,
            ),
        )
    conn.execute(
        "INSERT INTO node VALUES (?,?,?,?,?,?,1,?,?)",
        ("stakeholder_brien", "stakeholder", "Speaker_Alpha", "Speaker_Alpha", "Architecture Lead", "approved", now, now),
    )
    conn.execute(
        "INSERT INTO node_state VALUES (?,?,?,?,?,?,?,?,?,?,?,0,NULL,?)",
        ("stakeholder_brien", "active", "accepted", "current", "approved", "green", "resolved", "eligible", "internal", "reviewed", "approved", now),
    )
    conn.execute(
        "INSERT INTO node VALUES (?,?,?,?,?,?,1,?,?)",
        ("stakeholder_ninan", "stakeholder", "Speaker_Beta", "Speaker_Beta", "Delivery Lead", "approved", now, now),
    )
    conn.execute(
        "INSERT INTO node_state VALUES (?,?,?,?,?,?,?,?,?,?,?,0,NULL,?)",
        ("stakeholder_ninan", "active", "accepted", "current", "approved", "green", "resolved", "eligible", "internal", "reviewed", "approved", now),
    )
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()


def _build_sealed_envelope(
    tmp_path: Path,
    *,
    meeting_id: str,
    title: str,
    date_str: str,
    claim_text: str,
    entity_sid: str = "workstream:conduit-platform",
    entity_name: str = "Conduit Platform",
    entity_type: str = "workstream",
    duplicate_statement: bool = False,
) -> dict:
    src_path = tmp_path / f"{meeting_id}_01_transcript.md"
    src_path.write_text(
        f"[00:01] **Speaker_Alpha:** {claim_text}\n[00:02] **Speaker_Beta:** {claim_text}\n",
        encoding="utf-8",
    )
    ev_index = {
        "stmt-01": {
            "speaker": "Speaker_Alpha",
            "exact_text": claim_text,
            "kind": "decision",
            "source_classification": "decision",
            "locator": {"kind": "timestamp", "value": "00:01"},
        }
    }
    if duplicate_statement:
        ev_index["stmt-02"] = {
            "speaker": "Speaker_Beta",
            "exact_text": claim_text,
            "kind": "assertion",
            "source_classification": "assertion",
            "locator": {"kind": "timestamp", "value": "00:02"},
        }
    proj = {
        "schema_version": "5.7",
        "meeting": {
            "meeting_id": meeting_id,
            "title": title,
            "date": date_str,
            "classification": "confidential",
            "audiences": ["internal"],
            "source_location": f"local://{src_path.name}",
            "source_hash": digest(meeting_id + title),
            "attendees": ["Speaker_Alpha"],
        },
        "evidence_index": ev_index,
        "entity_upserts": [
            {
                "id": entity_sid,
                "entity_id": entity_sid,
                "canonical_name": entity_name,
                "name": entity_name,
                "entity_type": entity_type,
                "mutation": "CREATE",
                "evidence_ids": ["stmt-01"],
            }
        ],
    }
    pkg = _adapt_v53_projection_to_envelope(proj, source_path=src_path)
    pkg["meeting"]["source_hash"] = digest(meeting_id + title)
    pkg["meeting"]["source_location"] = f"local://{src_path.name}"
    pkg["meeting"]["classification"] = "confidential"
    pkg["meeting"]["audiences"] = ["internal"]
    return seal_artifact(pkg)


def test_prefix_or_type_mapping_avoids_pattern_collapse() -> None:
    """Verify domain prefixes map to specific creatable node types instead of collapsing to 'pattern'."""
    assert map_entity_type("initiative:ai-modernization") == "program"
    assert map_entity_type("metric:latency-sla") == "deliverable"
    assert map_entity_type("decision:adopt-bge-m3") == "strategy"
    assert map_entity_type("proposal:hybrid-routing") == "strategy"
    assert map_entity_type("forum:architecture-review-board") == "workstream"
    assert map_entity_type("model:bge-m3") == "technical_anchor"
    assert map_entity_type("document:sow-v2") == "deliverable"
    assert map_entity_type("milestone:wave6-complete") == "deliverable"
    assert are_node_types_compatible("program", "workstream") is True
    assert are_node_types_compatible("stakeholder", "internal_person") is True
    assert are_node_types_compatible("stakeholder", "technical_anchor") is False


def test_cross_meeting_entity_deduplication_and_corroboration_e2e(tmp_path: Path) -> None:
    """Verify Meeting A creates a hashed entity and assertion, and Meeting B auto-links the entity and corroborates the assertion."""
    graph_path = tmp_path / "graph.sqlite"
    _seed_test_graph(graph_path)

    pkg_a = _build_sealed_envelope(
        tmp_path,
        meeting_id="meeting-a-001",
        title="Meeting A Architecture Sync",
        date_str="2026-10-20",
        claim_text="Conduit Platform enforces zero-lock WAL concurrency across all ingestion writers.",
        duplicate_statement=True,
    )
    bundle_a = normalize_evidence_envelope(pkg_a, run_context="run-meeting-a")
    # Intra-meeting duplicate statement should be merged into 1 assertion candidate with 2 evidence_ids
    assertion_cands_a = [
        c for c in bundle_a["candidates"]
        if c["candidate_type"] in {"assertion", "decision"} and "zero-lock WAL" in c["normalized_text"]
    ]
    assert len(assertion_cands_a) == 1
    assert len(assertion_cands_a[0]["evidence_ids"]) == 2

    resolutions_a = resolve_bundle(bundle_a, graph_path)
    comparison_a = compare_candidates(bundle_a, graph_path)
    routes_a = build_routes(bundle_a, resolutions_a, comparison_a)
    dispositions_a = build_dispositions(bundle_a, routes_a, resolutions_a)
    change_set_a = build_graph_change_set(
        bundle_a,
        dispositions_a,
        resolutions_a,
        graph_path,
    )
    change_set_a = seal_artifact({**change_set_a, "status": "staged", "approval_id": "approval-meeting-a"})

    writer = GovernedGraphWriter(graph_path)
    receipt_a = writer.apply(change_set_a)
    assert receipt_a["status"] == "COMMITTED"
    assert receipt_a["physical_proof"]["overall"] == "PASS"

    # Verify catalog now indexes the hashed entity by its source_stable_identifier from audit_event
    catalog_after_a = GraphEntityCatalog(graph_path)
    exact_ids, basis = catalog_after_a.exact("Conduit Platform", "workstream:conduit-platform")
    assert len(exact_ids) == 1
    assert basis == "stable_identifier"
    created_entity_id = exact_ids[0]

    # Meeting B: compatible node type ('program' vs 'workstream') and same claim from distinct meeting
    pkg_b = _build_sealed_envelope(
        tmp_path,
        meeting_id="meeting-b-002",
        title="Meeting B Delivery Review",
        date_str="2026-10-21",
        claim_text="Conduit Platform enforces zero-lock WAL concurrency across all ingestion writers.",
        entity_sid="project:conduit-platform",
        entity_name="Conduit Platform",
        entity_type="project",
    )
    bundle_b = normalize_evidence_envelope(pkg_b, run_context="run-meeting-b")
    resolutions_b = resolve_bundle(bundle_b, graph_path)
    conduit_res_b = [r for r in resolutions_b if r["reported_name"] == "Conduit Platform"]
    assert conduit_res_b
    for r in conduit_res_b:
        assert r["outcome"] == "AUTO_LINK_EXACT"
        assert r["selected_entity_id"] == created_entity_id

    comparison_b = compare_candidates(bundle_b, graph_path)
    routes_b = build_routes(bundle_b, resolutions_b, comparison_b)
    route_by_cand_b = {r["subject_id"]: r for r in routes_b}
    assertion_cand_b = [
        c for c in bundle_b["candidates"]
        if c["candidate_type"] in {"assertion", "decision"} and "zero-lock WAL" in c["normalized_text"]
    ][0]
    assert route_by_cand_b[assertion_cand_b["candidate_id"]]["intake_route"] == "CORROBORATING_EVIDENCE"
    assert "com.partner_advisory.nee.corroborate-assertion-id" in route_by_cand_b[assertion_cand_b["candidate_id"]]["extensions"]

    dispositions_b = build_dispositions(bundle_b, routes_b, resolutions_b)
    change_set_b = build_graph_change_set(
        bundle_b,
        dispositions_b,
        resolutions_b,
        graph_path,
    )
    change_set_b = seal_artifact({**change_set_b, "status": "staged", "approval_id": "approval-meeting-b"})
    ops_b = [op["operation"] for op in change_set_b["operations"]]
    assert "create_entity" not in ops_b
    assert "add_evidence" in ops_b

    before_counts_b = _graph_counts(graph_path)
    planned_inc_b = _planned_increments(change_set_b, graph_path)
    receipt_b = writer.apply(change_set_b)
    assert receipt_b["status"] == "COMMITTED"
    after_counts_b = _graph_counts(graph_path)
    for k, inc in planned_inc_b.items():
        assert after_counts_b[k] - before_counts_b[k] == inc


def test_same_source_reingestion_routes_to_exact_duplicate(tmp_path: Path) -> None:
    """Verify re-ingesting the same source_id with identical assertions routes to EXACT_DUPLICATE -> REJECTED_NOOP."""
    graph_path = tmp_path / "graph.sqlite"
    _seed_test_graph(graph_path)

    pkg = _build_sealed_envelope(
        tmp_path,
        meeting_id="meeting-idem-001",
        title="Idempotent Architecture Review",
        date_str="2026-10-22",
        claim_text="Conduit Platform requires cryptographic receipts on all graph mutations.",
    )
    bundle_1 = normalize_evidence_envelope(pkg, run_context="run-idem-1")
    res_1 = resolve_bundle(bundle_1, graph_path)
    comp_1 = compare_candidates(bundle_1, graph_path)
    routes_1 = build_routes(bundle_1, res_1, comp_1)
    disp_1 = build_dispositions(bundle_1, routes_1, res_1)
    cs_1 = build_graph_change_set(bundle_1, disp_1, res_1, graph_path)
    cs_1 = seal_artifact({**cs_1, "status": "staged", "approval_id": "app-idem-1"})
    GovernedGraphWriter(graph_path).apply(cs_1)

    bundle_2 = normalize_evidence_envelope(pkg, run_context="run-idem-2")
    res_2 = resolve_bundle(bundle_2, graph_path)
    comp_2 = compare_candidates(bundle_2, graph_path)
    routes_2 = build_routes(bundle_2, res_2, comp_2)
    disp_2 = build_dispositions(bundle_2, routes_2, res_2)

    assertion_cand_2 = [
        c for c in bundle_2["candidates"]
        if c["candidate_type"] in {"assertion", "decision"} and "cryptographic receipts" in c["normalized_text"]
    ][0]
    route_2 = [r for r in routes_2 if r["subject_id"] == assertion_cand_2["candidate_id"]][0]
    disp_item_2 = [d for d in disp_2 if d["candidate_id"] == assertion_cand_2["candidate_id"]][0]
    assert route_2["intake_route"] == "EXACT_DUPLICATE"
    assert disp_item_2["disposition"] == "REJECTED_NOOP"
