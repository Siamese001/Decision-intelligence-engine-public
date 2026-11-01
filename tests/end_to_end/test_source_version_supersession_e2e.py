"""Wave 2 E2E tests for source-version supersession, evidence identity stability, and role re-ingestion idempotency."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from dual_pipeline.core import digest, seal_artifact, utc_now
from dual_pipeline.graph_delta_core.counts import _graph_counts
from dual_pipeline.graph_delta_core.delta import build_actual_graph_delta, build_planned_graph_delta
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
            node_id TEXT PRIMARY KEY, node_type TEXT, canonical_name TEXT, display_name TEXT,
            description TEXT, status TEXT, active INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE node_state (
            node_id TEXT PRIMARY KEY, lifecycle_state TEXT, validation_state TEXT,
            currentness_state TEXT, approval_state TEXT, rag_state TEXT, resolution_state TEXT,
            retrieval_eligibility TEXT, publication_state TEXT, review_state TEXT,
            legacy_status_text TEXT, intentional_orphan INTEGER, intentional_orphan_reason TEXT, updated_at TEXT
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
    for nt in ("stakeholder", "internal_person", "org", "technical_anchor", "role_group", "workstream", "program", "strategy", "risk", "deliverable", "pattern"):
        conn.execute(
            "INSERT INTO node_type_registry VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                nt,
                nt.title(),
                f"Type {nt}",
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
    conn.execute(
        "INSERT INTO governance_approval VALUES (?,?,?,?,?,?,?,1)",
        ("app-wave2", "app-wave2", "all", "approved", "operator", now, json.dumps({"allowed_actions": ["canonical_assertion_promotion"]})),
    )
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()


def _build_meeting_envelope(
    tmp_path: Path,
    *,
    meeting_id: str,
    source_hash: str,
    statements: dict[str, dict],
    entity_upserts: list[dict],
    relationship_signals: list[dict],
) -> dict:
    src_path = tmp_path / f"{meeting_id}_01_transcript.md"
    lines = [f"[{v['locator']['value']}] **{v['speaker']}:** {v['exact_text']}" for v in statements.values()]
    src_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    proj = {
        "schema_version": "5.8",
        "meeting": {
            "meeting_id": meeting_id,
            "title": "Infrastructure Automation Orals",
            "date": "2026-10-20",
            "classification": "confidential",
            "audiences": ["internal"],
            "source_location": f"local://{src_path.name}",
            "source_hash": source_hash,
            "attendees": ["Speaker_Alpha", "Speaker_Beta"],
        },
        "evidence_index": statements,
        "entity_upserts": entity_upserts,
        "relationship_signals": relationship_signals,
    }
    pkg = _adapt_v53_projection_to_envelope(proj, source_path=src_path)
    pkg["meeting"]["source_hash"] = source_hash
    pkg["meeting"]["source_location"] = f"local://{src_path.name}"
    pkg["meeting"]["classification"] = "confidential"
    pkg["meeting"]["audiences"] = ["internal"]
    return seal_artifact(pkg)


def test_source_version_and_evidence_stability_across_projection_regenerations_e2e(tmp_path: Path) -> None:
    """Verify frozen Output 01 statements produce identical source_version_id and evidence_ids even when Output 03 varies."""
    statements = {
        "stmt-01": {
            "speaker": "Speaker_Alpha",
            "exact_text": "Speaker_Alpha serves as Lead Architect for the Infrastructure Automation workstream.",
            "kind": "assertion",
            "source_classification": "assertion",
            "locator": {"kind": "timestamp", "value": "00:01"},
        }
    }
    upserts_1 = [
        {"id": "person:brien", "entity_id": "person:brien", "canonical_name": "Speaker_Alpha", "name": "Speaker_Alpha", "entity_type": "person", "role": "Lead Architect", "is_human_confirmed": True, "evidence_ids": ["stmt-01"]},
        {"id": "role:lead_architect", "entity_id": "role:lead_architect", "canonical_name": "Lead Architect", "name": "Lead Architect", "entity_type": "role", "evidence_ids": ["stmt-01"]},
    ]
    rels_1 = [
        {"signal_id": "sig-role-1", "from_id": "person:brien", "to_id": "role:lead_architect", "predicate": "HAS_ROLE_IN", "relationship": "HAS_ROLE_IN", "assertion_type": "speaker_role", "is_human_confirmed": True, "evidence_ids": ["stmt-01"]},
    ]
    pkg_1 = _build_meeting_envelope(
        tmp_path, meeting_id="mtg-orals-01", source_hash="a" * 64,
        statements=statements, entity_upserts=upserts_1, relationship_signals=rels_1,
    )
    # Second envelope has extra metadata in Output 03 (changing package canonical_digest) but identical Output 01 statements
    upserts_2 = [dict(u, description="Updated description from Output 03 regeneration") for u in upserts_1]
    pkg_2 = _build_meeting_envelope(
        tmp_path, meeting_id="mtg-orals-01", source_hash="a" * 64,
        statements=statements, entity_upserts=upserts_2, relationship_signals=rels_1,
    )
    assert pkg_1["canonical_digest"]["value"] != pkg_2["canonical_digest"]["value"]

    b1 = normalize_evidence_envelope(pkg_1, run_context="run-1")
    b2 = normalize_evidence_envelope(pkg_2, run_context="run-2")
    assert b1["source_id"] == b2["source_id"]
    assert b1["source_version_id"] == b2["source_version_id"]
    assert [e["evidence_id"] for e in b1["evidence"]] == [e["evidence_id"] for e in b2["evidence"]]

    graph_path = tmp_path / "graph.sqlite"
    _seed_graph(graph_path)
    res_1 = resolve_bundle(b1, graph_path)
    comp_1 = compare_candidates(b1, graph_path)
    routes_1 = build_routes(b1, res_1, comp_1)
    disp_1 = build_dispositions(b1, routes_1, res_1)
    cs_1 = build_graph_change_set(b1, disp_1, res_1, graph_path)
    cs_1 = seal_artifact({**cs_1, "status": "staged", "approval_id": "app-wave2"})
    GovernedGraphWriter(graph_path).apply(cs_1)

    # Re-ingest same source: HAS_ROLE_IN from same source_id must route to EXACT_DUPLICATE -> REJECTED_NOOP
    res_2 = resolve_bundle(b1, graph_path)
    comp_2 = compare_candidates(b1, graph_path)
    routes_2 = build_routes(b1, res_2, comp_2)
    disp_2 = build_dispositions(b1, routes_2, res_2)
    role_cands_2 = [c for c in b1["candidates"] if "HAS_ROLE_IN" in c.get("normalized_text", "")]
    assert len(role_cands_2) == 1
    role_disp_2 = next(d for d in disp_2 if d["candidate_id"] == role_cands_2[0]["candidate_id"])
    assert role_disp_2["disposition"] == "REJECTED_NOOP"


def test_source_version_supersession_retires_omitted_prior_assertions_e2e(tmp_path: Path) -> None:
    """Verify re-ingesting an existing source_id with a new source_version_id retires omitted prior assertions."""
    graph_path = tmp_path / "graph.sqlite"
    _seed_graph(graph_path)

    stmts_v1 = {
        "stmt-01": {
            "speaker": "Speaker_Alpha",
            "exact_text": "Conduit Platform enforces deterministic SQLite WAL checkpointing on every write.",
            "kind": "decision",
            "source_classification": "decision",
            "locator": {"kind": "timestamp", "value": "00:01"},
        },
        "stmt-02": {
            "speaker": "Speaker_Beta",
            "exact_text": "Legacy batch polling runs every 45 minutes on staging nodes.",
            "kind": "assertion",
            "source_classification": "assertion",
            "locator": {"kind": "timestamp", "value": "00:02"},
        },
    }
    upserts = [
        {"id": "workstream:conduit", "entity_id": "workstream:conduit", "canonical_name": "Conduit Platform", "name": "Conduit Platform", "entity_type": "workstream", "evidence_ids": ["stmt-01"]},
    ]
    pkg_v1 = _build_meeting_envelope(
        tmp_path, meeting_id="mtg-supersede-01", source_hash="1" * 64,
        statements=stmts_v1, entity_upserts=upserts, relationship_signals=[],
    )
    b_v1 = normalize_evidence_envelope(pkg_v1, run_context="run-v1")
    res_v1 = resolve_bundle(b_v1, graph_path)
    comp_v1 = compare_candidates(b_v1, graph_path)
    routes_v1 = build_routes(b_v1, res_v1, comp_v1)
    disp_v1 = build_dispositions(b_v1, routes_v1, res_v1)
    cs_v1 = build_graph_change_set(b_v1, disp_v1, res_v1, graph_path)
    cs_v1 = seal_artifact({**cs_v1, "status": "staged", "approval_id": "app-wave2"})
    GovernedGraphWriter(graph_path).apply(cs_v1)

    conn = sqlite3.connect(str(graph_path))
    cur_v1 = conn.execute(
        "SELECT scope_text FROM assertion WHERE primary_source_id=? AND currentness_state='current'",
        (b_v1["source_id"],),
    ).fetchall()
    conn.close()
    assert any("Legacy batch polling" in r[0] for r in cur_v1)

    # Version 2 of the same meeting drops stmt-02 ("Legacy batch polling") and adds stmt-03
    stmts_v2 = {
        "stmt-01": stmts_v1["stmt-01"],
        "stmt-03": {
            "speaker": "Speaker_Beta",
            "exact_text": "Conduit Platform replaces legacy polling with event-driven webhook triggers.",
            "kind": "decision",
            "source_classification": "decision",
            "locator": {"kind": "timestamp", "value": "00:03"},
        },
    }
    pkg_v2 = _build_meeting_envelope(
        tmp_path, meeting_id="mtg-supersede-01", source_hash="2" * 64,
        statements=stmts_v2, entity_upserts=upserts, relationship_signals=[],
    )
    b_v2 = normalize_evidence_envelope(pkg_v2, run_context="run-v2")
    assert b_v2["source_id"] == b_v1["source_id"]
    assert b_v2["source_version_id"] != b_v1["source_version_id"]

    res_v2 = resolve_bundle(b_v2, graph_path)
    comp_v2 = compare_candidates(b_v2, graph_path)
    routes_v2 = build_routes(b_v2, res_v2, comp_v2)
    disp_v2 = build_dispositions(b_v2, routes_v2, res_v2)
    cs_v2 = build_graph_change_set(b_v2, disp_v2, res_v2, graph_path)
    cs_v2 = seal_artifact({**cs_v2, "status": "staged", "approval_id": "app-wave2"})

    before_path = tmp_path / "before_v2.sqlite"
    before_path.write_bytes(graph_path.read_bytes())
    planned_summary = build_planned_graph_delta(bundle=b_v2, change_set=cs_v2, graph_path=before_path)

    GovernedGraphWriter(graph_path).apply(cs_v2)
    actual_summary = build_actual_graph_delta(
        planned_summaries=[planned_summary],
        before_graph_path=before_path,
        after_graph_path=graph_path,
    )
    assert actual_summary["status"] == "PASS"

    conn = sqlite3.connect(str(graph_path))
    omitted_row = conn.execute(
        "SELECT currentness_state, superseded_at, retrieval_eligible FROM assertion WHERE primary_source_id=? AND scope_text LIKE '%Legacy batch polling%'",
        (b_v2["source_id"],),
    ).fetchone()
    sd_row = conn.execute(
        "SELECT currentness_state, retrieval_eligible FROM search_document WHERE source_id=? AND body LIKE '%Legacy batch polling%'",
        (b_v2["source_id"],),
    ).fetchone()
    conn.close()
    assert omitted_row is not None
    assert omitted_row[0] == "historical"
    assert omitted_row[1] is not None
    assert omitted_row[2] == 0
    assert sd_row == ("historical", 0)
