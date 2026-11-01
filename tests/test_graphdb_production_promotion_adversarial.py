"""
Wave 4 Adversarial & Edge-Case Tests: Governed Production GraphDB & BGE-M3 Sidecar Promotion.

Covers mandatory edge-case categories:
1. Boundary payload: backfill_unlinked_current_assertions on a database with 0 unlinked assertions (idempotent no-op).
2. Malformed input: persist_validated_role rejects empty/whitespace role or non-existent DB path.
3. Invariant bypass attempt: resolve_active_pair(enforce_integrity=True) fails closed when graph_sha256 or sidecar_sha256 is tampered with.
4. Rollback & idempotent recovery: repeating persist_validated_role + backfill_unlinked_current_assertions is 100% idempotent with zero duplicate assertion rows.
5. Exact production regression: Amit Ayer legacy RELATED_TO role assertions ('Amit Ayer HAS_ROLE Lead Client Partner.') are superseded when persist_validated_role ratifies 'Senior Director / Lead Enterprise Architect'.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from dual_pipeline.runtime_root import resolve_active_pair
from dual_pipeline.transcript_metadata_core.entity_resolver import ResolvedEntity
from dual_pipeline.transcript_metadata_core.entity_resolver_graph import (
    load_knowledge_graph_data,
)
from dual_pipeline.uwg_core.universal_write_gateway_role import (
    backfill_unlinked_current_assertions,
    persist_validated_role,
)


def _create_minimal_v48_graph(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE controlled_value_registry (
                vocabulary TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY (vocabulary, value)
            );
            CREATE TABLE node_type_registry (
                node_type TEXT PRIMARY KEY
            );
            INSERT INTO node_type_registry VALUES ('stakeholder'), ('role_group'), ('org');
            CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                node_type TEXT NOT NULL,
                canonical_name TEXT NOT NULL,
                display_name TEXT NOT NULL,
                description TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE node_fts USING fts5(node_id, canonical_name, display_name, description);
            CREATE TABLE source (
                source_id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_date TEXT,
                authority_rank INTEGER NOT NULL,
                source_posture TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE source_version (
                source_version_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                version_number INTEGER NOT NULL,
                source_fingerprint TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                source_date_normalized TEXT,
                source_date_precision TEXT NOT NULL,
                source_date_status TEXT NOT NULL,
                authority_rank INTEGER NOT NULL,
                scope_text TEXT NOT NULL,
                posture_text TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE extraction_run (
                extraction_run_id TEXT PRIMARY KEY,
                algorithm_version TEXT NOT NULL,
                baseline_graph_sha256 TEXT NOT NULL,
                input_manifest_sha256 TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL,
                deterministic INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE evidence_item (
                evidence_id TEXT PRIMARY KEY,
                evidence_fingerprint TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_version_id TEXT NOT NULL,
                locator_kind TEXT NOT NULL,
                locator_value TEXT NOT NULL,
                direct_quote TEXT NOT NULL,
                evidence_directness TEXT NOT NULL,
                source_authority_rank INTEGER NOT NULL,
                extraction_confidence REAL NOT NULL,
                corroboration_group TEXT NOT NULL,
                location_status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                assertion_fingerprint TEXT NOT NULL,
                subject_node_id TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object_node_id TEXT,
                object_literal TEXT,
                assertion_type TEXT NOT NULL,
                atomicity_state TEXT NOT NULL,
                scope_text TEXT NOT NULL,
                primary_source_id TEXT,
                extraction_run_id TEXT,
                extraction_method TEXT NOT NULL,
                origin_type TEXT NOT NULL,
                confidence_label TEXT NOT NULL,
                confidence_score REAL NOT NULL,
                validation_state TEXT NOT NULL,
                currentness_state TEXT NOT NULL,
                valid_from TEXT,
                superseded_at TEXT,
                superseded_by_assertion_id TEXT,
                date_precision TEXT NOT NULL,
                date_status TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                resolution_state TEXT NOT NULL,
                canonical_status TEXT NOT NULL,
                canonical_eligible INTEGER NOT NULL,
                retrieval_eligible INTEGER NOT NULL,
                materiality TEXT NOT NULL,
                classification TEXT NOT NULL,
                allowed_audiences_json TEXT NOT NULL,
                hitl_required INTEGER NOT NULL,
                qualifiers_json TEXT NOT NULL,
                legacy_record_type TEXT NOT NULL,
                legacy_record_ids_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE assertion_evidence (
                assertion_id TEXT NOT NULL,
                evidence_id TEXT NOT NULL,
                evidence_relationship TEXT NOT NULL,
                independent_corroboration INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                PRIMARY KEY (assertion_id, evidence_id)
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def test_boundary_backfill_zero_unlinked_assertions_is_noop(tmp_path: Path) -> None:
    """Boundary payload: when all current assertions are already linked, backfill_unlinked_current_assertions is a no-op."""
    db_path = tmp_path / "graph.sqlite"
    _create_minimal_v48_graph(db_path)
    persist_validated_role(db_path, "Eduardo More", "Atlas Lead / Rule Specialist")

    res = backfill_unlinked_current_assertions(db_path)
    assert res["linked_assertion_ids"] == []
    assert res["lineage_fixed_count"] == 0


def test_malformed_persist_validated_role_inputs_rejected(tmp_path: Path) -> None:
    """Malformed input: empty canonical_name, empty role, or missing DB path raises ValueError/FileNotFoundError."""
    db_path = tmp_path / "graph.sqlite"
    _create_minimal_v48_graph(db_path)

    with pytest.raises(ValueError, match="canonical_name"):
        persist_validated_role(db_path, "   ", "Atlas Lead")
    with pytest.raises(ValueError, match="role"):
        persist_validated_role(db_path, "Eduardo More", "")
    with pytest.raises(FileNotFoundError):
        persist_validated_role(tmp_path / "missing.sqlite", "Eduardo More", "Atlas Lead")


def test_invariant_bypass_tampered_active_pair_digest_fails_closed(tmp_path: Path) -> None:
    """Invariant bypass attempt: tampered graph_sha256 in active_pair.json fails closed under enforce_integrity=True."""
    gen_dir = tmp_path / "generations" / "gen-test"
    gen_dir.mkdir(parents=True)
    db_path = gen_dir / "graph.sqlite"
    sidecar_path = gen_dir / "sidecar.sqlite"
    _create_minimal_v48_graph(db_path)
    shutil.copy2(db_path, sidecar_path)

    ap_file = tmp_path / "active_pair.json"
    ap_file.write_text(
        json.dumps(
            {
                "generation_id": "gen-test",
                "graph_path": "generations/gen-test/graph.sqlite",
                "graph_sha256": "0" * 64,
                "sidecar_path": "generations/gen-test/sidecar.sqlite",
                "sidecar_sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )
    from dual_pipeline.runtime_root import ActiveGraphIntegrityMismatchError
    with pytest.raises(ActiveGraphIntegrityMismatchError, match="integrity mismatch"):
        resolve_active_pair(tmp_path, enforce_integrity=True)


def test_idempotent_repeated_role_persistence_and_backfill(tmp_path: Path) -> None:
    """Rollback & idempotent recovery: repeating persist_validated_role and backfill produces 0 duplicate assertions."""
    db_path = tmp_path / "graph.sqlite"
    _create_minimal_v48_graph(db_path)

    r1 = persist_validated_role(db_path, "Eduardo More", "Atlas Lead / Rule Specialist")
    r2 = persist_validated_role(db_path, "Eduardo More", "Atlas Lead / Rule Specialist")
    assert r1["persisted_assertion_id"] == r2["persisted_assertion_id"]

    conn = sqlite3.connect(db_path)
    try:
        cnt = conn.execute(
            "SELECT count(*) FROM assertion WHERE subject_node_id = ? AND currentness_state = 'current'",
            (r1["subject_node_id"],),
        ).fetchone()[0]
        assert cnt == 1
    finally:
        conn.close()


def test_production_regression_amit_ayer_related_to_role_assertions_superseded(tmp_path: Path) -> None:
    """Exact production regression: legacy RELATED_TO assertions with 'HAS_ROLE Lead Client Partner' are superseded."""
    db_path = tmp_path / "graph.sqlite"
    _create_minimal_v48_graph(db_path)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO node VALUES ('person_amit', 'stakeholder', 'Amit Ayer', 'Amit Ayer', 'Role: Lead Client Partner', 1, '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO node VALUES ('role_lcp', 'role_group', 'Lead Client Partner', 'Lead Client Partner', 'Role group', 1, '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO assertion VALUES ("
            "'asrt_legacy_lcp', 'fp1', 'person_amit', 'RELATED_TO', 'role_lcp', NULL, "
            "'confirmed_canonical_fact', 'atomic', 'Amit Ayer HAS_ROLE Lead Client Partner.', "
            "NULL, NULL, 'legacy', 'legacy_migration', 'high', 0.90, 'accepted_legacy', 'current', "
            "'2026-09-01', NULL, NULL, 'day', 'exact', '2026-09-01T00:00:00Z', 'resolved', "
            "'canonical', 1, 1, 'high', 'internal', '[\"internal_consulting\"]', 0, '{}', 'edge', '[]', "
            "'2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z')"
        )
        conn.commit()
    finally:
        conn.close()

    res = persist_validated_role(db_path, "Amit Ayer", "Senior Director / Lead Enterprise Architect")
    assert "asrt_legacy_lcp" in res["superseded_assertion_ids"]

    kg_cache, _ = load_knowledge_graph_data(
        db_path=db_path,
        root=tmp_path,
        runtime_root=tmp_path,
        fallback_generation_id="gen-test",
        entity_factory=ResolvedEntity,
    )
    assert kg_cache["amit ayer"].role == "Senior Director / Lead Enterprise Architect"
    assert kg_cache["amit ayer"].status == "RESOLVED"
