"""Wave 3 E2E tests for signal extraction completeness and publication verifier contract alignment."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from dual_pipeline.canonical_pipeline.manifest_synthesis import (
    build_manifest_from_output_03,
    canonicalize_relationship_predicate,
)
from dual_pipeline.canonical_pipeline.publication_verifier import verify_fact_coverage_in_graph
from dual_pipeline.core import digest, seal_artifact, utc_now
from dual_pipeline.graph_writer_core.writer import GovernedGraphWriter
from dual_pipeline.identity_core.resolver import resolve_bundle
from dual_pipeline.intake_core.change_set import build_graph_change_set
from dual_pipeline.intake_core.comparison import compare_candidates
from dual_pipeline.intake_core.dispositions import build_dispositions
from dual_pipeline.intake_core.routing import build_routes
from dual_pipeline.normalization_core.meeting import normalize_evidence_envelope
from tools.prepare_evidence_envelope_intake import _adapt_v53_projection_to_envelope


def _seed_graph(graph_path: Path) -> None:
    conn = sqlite3.connect(str(graph_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE node_type_registry (
            node_type_id TEXT PRIMARY KEY, canonical_name TEXT, definition TEXT,
            parent_type_id TEXT, required_properties_json TEXT, optional_properties_json TEXT,
            identity_rule TEXT, example_node_id TEXT, registry_state TEXT,
            replacement_type_id TEXT, retrieval_use TEXT, created_at TEXT
        );
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY, node_type TEXT, canonical_name TEXT,
            display_name TEXT, description TEXT, status TEXT,
            active INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE node_state (
            node_id TEXT PRIMARY KEY, lifecycle_state TEXT, validation_state TEXT,
            currentness_state TEXT, approval_state TEXT, rag_state TEXT,
            resolution_state TEXT, retrieval_eligibility TEXT, publication_state TEXT,
            review_state TEXT, legacy_status_text TEXT, intentional_orphan INTEGER,
            intentional_orphan_reason TEXT, updated_at TEXT
        );
        CREATE TABLE canonical_alias (
            alias_id TEXT PRIMARY KEY, normalized_alias TEXT, entity_id TEXT, active INTEGER DEFAULT 1
        );
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY, source_type TEXT, source_name TEXT, source_date TEXT,
            authority_rank INTEGER, source_posture TEXT, cold_archive_ref TEXT, notes TEXT, created_at TEXT
        );
        CREATE TABLE source_version (
            source_version_id TEXT PRIMARY KEY, source_id TEXT, version_number INTEGER,
            source_fingerprint TEXT, content_hash TEXT, source_date_normalized TEXT,
            source_date_precision TEXT, source_date_status TEXT, authority_rank INTEGER,
            scope_text TEXT, posture_text TEXT, created_at TEXT
        );
        CREATE TABLE extraction_run (
            extraction_run_id TEXT PRIMARY KEY, algorithm_version TEXT,
            baseline_graph_sha256 TEXT, input_manifest_sha256 TEXT,
            started_at TEXT, completed_at TEXT, status TEXT, deterministic INTEGER
        );
        CREATE TABLE evidence_item (
            evidence_id TEXT PRIMARY KEY, evidence_fingerprint TEXT, source_id TEXT,
            source_version_id TEXT, locator_kind TEXT, locator_value TEXT, page_number INTEGER,
            section_name TEXT, paragraph_number INTEGER, row_reference TEXT, slide_number INTEGER,
            transcript_timestamp TEXT, direct_quote TEXT, evidence_directness TEXT,
            source_authority_rank INTEGER, extraction_confidence REAL, corroboration_group TEXT,
            location_status TEXT, created_at TEXT
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY, assertion_fingerprint TEXT, subject_node_id TEXT,
            predicate TEXT, legacy_predicate TEXT, object_node_id TEXT, object_literal TEXT,
            object_literal_type TEXT, assertion_type TEXT, atomicity_state TEXT, scope_text TEXT,
            speaker_or_author TEXT, primary_source_id TEXT, extraction_run_id TEXT,
            extraction_method TEXT, origin_type TEXT, confidence_label TEXT, confidence_score REAL,
            validation_state TEXT, currentness_state TEXT, valid_from TEXT, valid_to TEXT,
            date_precision TEXT, date_status TEXT, recorded_at TEXT, superseded_at TEXT,
            superseded_by_assertion_id TEXT, resolution_state TEXT, canonical_status TEXT,
            canonical_eligible INTEGER, retrieval_eligible INTEGER, materiality TEXT,
            classification TEXT, allowed_audiences_json TEXT, hitl_required INTEGER,
            validation_priority TEXT, validation_owner TEXT, qualifiers_json TEXT,
            legacy_record_type TEXT, legacy_record_ids_json TEXT, created_at TEXT,
            updated_at TEXT, approval_reference TEXT
        );
        CREATE TABLE assertion_evidence (
            assertion_id TEXT, evidence_id TEXT, evidence_relationship TEXT,
            independent_corroboration INTEGER, created_at TEXT,
            PRIMARY KEY (assertion_id, evidence_id)
        );
        CREATE TABLE search_document (
            search_rowid INTEGER PRIMARY KEY AUTOINCREMENT, search_document_id TEXT UNIQUE,
            object_type TEXT, object_id TEXT, title TEXT, body TEXT, canonical_status TEXT,
            currentness_state TEXT, retrieval_eligible INTEGER, source_id TEXT,
            assertion_id TEXT, updated_at TEXT
        );
        CREATE TABLE audit_event (
            audit_event_id TEXT PRIMARY KEY, actor_or_process TEXT, action TEXT,
            record_type TEXT, record_id TEXT, previous_value_json TEXT, new_value_json TEXT,
            source_id TEXT, event_timestamp TEXT, reason TEXT, validation_reference TEXT,
            approval_reference TEXT, migration_or_ingestion_run_id TEXT, release_version TEXT
        );
        CREATE TABLE governance_approval (
            approval_id TEXT PRIMARY KEY, approval_reference TEXT, decision_scope TEXT,
            status TEXT, approved_by TEXT, approved_at TEXT, scope_json TEXT, active INTEGER DEFAULT 1
        );
        """
    )
    now = utc_now()
    for nt in (
        "stakeholder", "internal_person", "org", "technical_anchor", "role_group",
        "control", "control_protocol", "program", "workstream", "risk",
        "deliverable", "open_question", "strategy", "opportunity", "capability", "pattern",
    ):
        conn.execute(
            "INSERT INTO node_type_registry VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                nt, nt.title(), f"Type {nt}", None, "[]", "[]",
                "Deterministic ID; never auto-merge on name similarity.",
                None, "active", None, "eligible", now,
            ),
        )
    conn.execute(
        "INSERT INTO governance_approval VALUES (?,?,?,?,?,?,?,1)",
        ("app-wave3", "app-wave3", "all", "approved", "operator", now, json.dumps({"allowed_actions": ["canonical_assertion_promotion"]})),
    )
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()


def test_assertion_updates_action_dedup_and_publication_coverage_e2e(tmp_path: Path) -> None:
    """Verify assertion_updates extraction, action_updates deduplication, and 100% publication coverage verification."""
    graph_path = tmp_path / "graph.sqlite"
    _seed_graph(graph_path)

    src_path = tmp_path / "mtg_wave3_01_transcript.md"
    src_path.write_text(
        "[00:01] **Speaker_Alpha:** Speaker_Alpha leads the Conduit Platform workstream as Principal Architect.\n"
        "[00:02] **Speaker_Beta:** Conduit Platform mitigates schema drift risks via automated contract validation.\n",
        encoding="utf-8",
    )

    output_03 = {
        "schema_version": "5.8",
        "meeting_id": "mtg-wave3-01",
        "meeting": {
            "meeting_id": "mtg-wave3-01",
            "title": "Wave 3 Contract Alignment Review",
            "date": "2026-10-20",
            "classification": "confidential",
            "audiences": ["internal"],
            "source_location": f"local://{src_path.name}",
            "source_hash": "3" * 64,
            "attendees": ["Speaker_Alpha", "Speaker_Beta"],
        },
        "speakers": [
            {"name": "Speaker_Alpha", "reported_name": "Speaker_Alpha", "role": "Principal Architect"},
        ],
        "evidence_index": {
            "stmt-01": {
                "speaker": "Speaker_Alpha",
                "exact_text": "Speaker_Alpha leads the Conduit Platform workstream as Principal Architect.",
                "kind": "assertion",
                "source_classification": "assertion",
                "locator": {"kind": "timestamp", "value": "00:01"},
            },
            "stmt-02": {
                "speaker": "Speaker_Beta",
                "exact_text": "Conduit Platform mitigates schema drift risks via automated contract validation.",
                "kind": "decision",
                "source_classification": "decision",
                "locator": {"kind": "timestamp", "value": "00:02"},
            },
        },
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "person:brien",
                    "entity_id": "person:brien",
                    "canonical_name": "Speaker_Alpha",
                    "name": "Speaker_Alpha",
                    "entity_type": "person",
                    "role": "Principal Architect",
                    "description": "Speaker_Alpha — Principal Architect",
                    "is_human_confirmed": True,
                    "evidence_ids": ["stmt-01"],
                },
                {
                    "id": "role:principal_architect",
                    "entity_id": "role:principal_architect",
                    "canonical_name": "Principal Architect",
                    "name": "Principal Architect",
                    "entity_type": "role",
                    "evidence_ids": ["stmt-01"],
                },
                {
                    "id": "workstream:conduit_platform",
                    "entity_id": "workstream:conduit_platform",
                    "canonical_name": "Conduit Platform",
                    "name": "Conduit Platform",
                    "entity_type": "workstream",
                    "evidence_ids": ["stmt-02"],
                },
            ],
            "relationship_signals": [
                {
                    "signal_id": "sig-role-01",
                    "from_id": "person:brien",
                    "to_id": "role:principal_architect",
                    "predicate": "HAS_ROLE_IN",
                    "relationship": "HAS_ROLE_IN",
                    "assertion_type": "speaker_role",
                    "is_human_confirmed": True,
                    "evidence_ids": ["stmt-01"],
                },
                {
                    "signal_id": "sig-leads-01",
                    "from_id": "person:brien",
                    "to_id": "workstream:conduit_platform",
                    "predicate": "MITIGATES",
                    "relationship": "MITIGATES",
                    "evidence_ids": ["stmt-02"],
                },
            ],
            "assertion_updates": [
                {
                    "id": "asrt-upd-01",
                    "entity_id": "workstream:conduit_platform",
                    "claim": "Conduit Platform enforces zero-downtime schema migrations across all tenants.",
                    "evidence_ids": ["stmt-02"],
                }
            ],
            "decision_updates": [
                {
                    "id": "decision:dec_upd_01",
                    "decision_id": "decision:dec_upd_01",
                    "entity_id": "workstream:conduit_platform",
                    "decision_text": "Standardize all intake packages on evidence-envelope-intake:1.0.0.",
                    "evidence_ids": ["stmt-02"],
                }
            ],
            "action_updates": [
                {
                    "id": "act-upd-01",
                    "owner": "Speaker_Alpha",
                    "entity_id": "person:brien",
                    "action_text": "Publish the updated Wave 3 schema governance runbook.",
                    "evidence_ids": ["stmt-01"],
                }
            ],
        },
    }

    assert canonicalize_relationship_predicate("MITIGATES") == "RELATED_TO"
    assert canonicalize_relationship_predicate("MEMBER_OF") == "AFFILIATED_WITH"
    assert canonicalize_relationship_predicate("LEADS") == "OWNS_OR_LEADS"

    pkg = _adapt_v53_projection_to_envelope(output_03, source_path=src_path)
    pkg["meeting"]["source_hash"] = "3" * 64
    pkg["meeting"]["source_location"] = f"local://{src_path.name}"
    pkg["meeting"]["classification"] = "confidential"
    pkg["meeting"]["audiences"] = ["internal"]
    pkg = seal_artifact(pkg)

    bundle = normalize_evidence_envelope(pkg, run_context="run-wave3")

    # 1. Verify assertion_updates produced a normalized candidate
    asrt_cands = [c for c in bundle["candidates"] if "zero-downtime schema migrations" in c.get("normalized_text", "")]
    assert len(asrt_cands) == 1

    # 2. Verify action_updates was deduplicated to exactly 1 action candidate (no dual-path duplication)
    action_cands = [c for c in bundle["candidates"] if c.get("candidate_type") == "action" and "schema governance runbook" in c.get("normalized_text", "").lower()]
    assert len(action_cands) == 1

    # 3. Apply graph change set
    resolutions = resolve_bundle(bundle, graph_path)
    comparison = compare_candidates(bundle, graph_path)
    routes = build_routes(bundle, resolutions, comparison)
    dispositions = build_dispositions(bundle, routes, resolutions)
    change_set = build_graph_change_set(bundle, dispositions, resolutions, graph_path)
    change_set = seal_artifact({**change_set, "status": "staged", "approval_id": "app-wave3"})
    receipt = GovernedGraphWriter(graph_path).apply(change_set)
    assert receipt["status"] == "COMMITTED"

    # 4. Synthesize EvidenceCoverageManifest from Output 03 and verify 100% coverage in graph
    manifest = build_manifest_from_output_03(output_03, run_id="run-wave3", source_digest="3" * 64)
    locators = [it.source_origin.get("locator", "") for it in manifest.items]
    assert any(loc.startswith("assertion_updates[") for loc in locators)
    assert any(loc.startswith("decision_updates[") for loc in locators)
    assert any(loc.startswith("action_updates[") for loc in locators)
    assert any(loc.startswith("speakers[") for loc in locators)

    ok, violations, cov_receipt = verify_fact_coverage_in_graph(
        graph_db=graph_path,
        manifest=manifest,
        proposal_digest=digest(output_03),
        activated_generation_id="gen-wave3-01",
        output_dir=tmp_path,
    )
    assert ok is True, f"Expected 0 coverage violations, got: {violations}"
    assert cov_receipt["status"] == "VERIFIED"
    assert cov_receipt["coverage_summary"]["missing_count"] == 0
