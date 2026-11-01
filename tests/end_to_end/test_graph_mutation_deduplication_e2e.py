"""End-to-end tests for Wave 6: Cross-Cutting Graph Mutation, Identity Resolution & Assertion Deduplication."""
from __future__ import annotations

from pathlib import Path
from storage.database import sqlite3

from dual_pipeline.graph_writer_core.assertions import (
    _build_assertion_qualifiers,
    compute_cross_source_scope_signature,
)
from dual_pipeline.graph_writer_core.helpers import _restore_fingerprint_replacement
from dual_pipeline.identity_core.catalog import GraphEntityCatalog
from dual_pipeline.identity_core.matching import determine_resolution_outcome
from dual_pipeline.identity_core.proposals import _new_node_assessment
from dual_pipeline.index import CLASSIFICATION_RANK
from dual_pipeline.release_core.delta_rebase import detect_mutation_conflicts


def _create_test_graph(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                canonical_name TEXT,
                node_type TEXT,
                description TEXT,
                active INTEGER DEFAULT 1
            );
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT,
                object_node_id TEXT,
                classification TEXT,
                allowed_audiences_json TEXT,
                currentness_state TEXT DEFAULT 'current',
                superseded_at TEXT,
                superseded_by_assertion_id TEXT
            );
            CREATE TABLE canonical_alias (
                normalized_alias TEXT,
                entity_id TEXT,
                active INTEGER DEFAULT 1
            );
            CREATE TABLE audit_event (
                record_id TEXT,
                action TEXT,
                record_type TEXT,
                new_value_json TEXT
            );
            CREATE TABLE validation_queue (
                queue_id TEXT PRIMARY KEY,
                target_id TEXT,
                status TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO node VALUES ('ent_person_1', 'Alex Rivera', 'person', 'Lead Architect', 1)"
        )
        conn.execute(
            "INSERT INTO node VALUES ('ent_ws_1', 'Alex Rivera', 'workstream', 'Workstream homonym', 1)"
        )
        conn.execute(
            "INSERT INTO node VALUES ('ent_zero_assert', 'Zero Assertion Node', 'system', 'Newly created', 1)"
        )
        valid_cls = next(iter(CLASSIFICATION_RANK))
        conn.execute(
            "INSERT INTO assertion VALUES ('as_1', 'ent_person_1', 'ent_ws_1', ?, '[\"internal_delivery\"]', 'current', NULL, NULL)",
            (valid_cls,),
        )
        conn.commit()
    finally:
        conn.close()


def test_catalog_admits_zero_assertion_entities_and_narrows_by_declared_type(tmp_path: Path) -> None:
    """Verify zero-assertion active nodes remain visible to authorized catalogs and exact() narrows by declared_type."""
    db_path = tmp_path / "graph.sqlite"
    _create_test_graph(db_path)

    valid_cls = next(iter(CLASSIFICATION_RANK))
    catalog = GraphEntityCatalog(
        db_path,
        classification=valid_cls,
        audiences=["internal_delivery"],
    )
    assert "ent_zero_assert" in catalog.entities

    matches_person, basis = catalog.exact("Alex Rivera", None, declared_type="person")
    assert matches_person == ["ent_person_1"]
    assert basis == "exact_normalized_name"

    matches_ws, _ = catalog.exact("Alex Rivera", None, declared_type="workstream")
    assert matches_ws == ["ent_ws_1"]


def test_cross_source_scope_signature_and_candidate_entity_dedup() -> None:
    """Verify cross-source scope signature strips trailing meeting titles and cross_source_dedup unifies candidate IDs."""
    sig_a = compute_cross_source_scope_signature(
        "ent_person_1",
        "HAS_CLAIM_TEXT",
        None,
        "Conduit migration completes Q4 cutover in Weekly Architecture Sync.",
    )
    sig_b = compute_cross_source_scope_signature(
        "ent_person_1",
        "has claim text",
        None,
        "conduit migration completes q4 cutover in Executive Steering Review.",
    )
    assert sig_a == sig_b

    cand_dict = {
        "candidate_id": "cand-1",
        "candidate_type": "claim",
        "extraction_confidence": 0.92,
        "normalized_text": "Conduit migration completes Q4 cutover in Weekly Architecture Sync.",
        "normalized_topic": "conduit_cutover",
    }
    qualifiers = _build_assertion_qualifiers(
        cand_dict,
        {
            "candidate": cand_dict,
            "disposition": "CANDIDATE",
            "subject_node_id": "ent_person_1",
            "predicate": "HAS_CLAIM_TEXT",
        },
    )
    assert qualifiers["cross_source_scope_signature"] == sig_a

    assess_1 = _new_node_assessment(
        bundle={"source_id": "src-001", "source_version_id": "sv-001"},
        mention={"reported_text": "Project Horizon", "context": "Core initiative"},
        profile={
            "entity_intent": "resolve_or_create",
            "node_type": "workstream",
            "stable_identifier": "workstream:project-horizon",
            "source_profile_digest": "sha256:" + "a" * 64,
            "cross_source_dedup": True,
        },
        group={"group_id": "grp-1", "evidence_ids": ["ev-1"], "candidate_ids": ["cand-1"], "source_locations": []},
        valid_node_types=["workstream"],
        status="PROPOSE_NEW_CANDIDATE",
        alternatives=[],
        reasons=["New candidate"],
    )
    assess_2 = _new_node_assessment(
        bundle={"source_id": "src-002", "source_version_id": "sv-002"},
        mention={"reported_text": "Project Horizon", "context": "Core initiative"},
        profile={
            "entity_intent": "resolve_or_create",
            "node_type": "workstream",
            "stable_identifier": "workstream:project_horizon",
            "source_profile_digest": "sha256:" + "b" * 64,
            "cross_source_dedup": True,
        },
        group={"group_id": "grp-2", "evidence_ids": ["ev-2"], "candidate_ids": ["cand-2"], "source_locations": []},
        valid_node_types=["workstream"],
        status="PROPOSE_NEW_CANDIDATE",
        alternatives=[],
        reasons=["New candidate"],
    )
    assert assess_1["proposal"]["entity_id"] == assess_2["proposal"]["entity_id"]


def test_fingerprint_replacement_preserves_validation_queue_and_idempotent_rebase(tmp_path: Path) -> None:
    """Verify validation_queue target_id is rebound on fingerprint replacement and idempotent entity ops pass rebase."""
    db_path = tmp_path / "graph.sqlite"
    _create_test_graph(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("INSERT INTO validation_queue VALUES ('vq_1', 'old_assert_1', 'open')")
        _restore_fingerprint_replacement(
            conn,
            "new_assert_2",
            prior_ev=[],
            superseded_ids=[],
            evidence_ids=[],
            existing_evidence=set(),
            now="2026-10-25T00:00:00Z",
            old_assertion_id="old_assert_1",
        )
        conn.commit()
        row = conn.execute("SELECT target_id FROM validation_queue WHERE queue_id='vq_1'").fetchone()
        assert row[0] == "new_assert_2"
    finally:
        conn.close()

    change_set = {
        "operations": [
            {
                "operation_id": "op_ent_1",
                "operation": "create_entity",
                "target_id": "ent_person_1",
                "idempotent_upsert": True,
            }
        ],
        "extensions": {
            "com.partner_advisory.nee.operation-payloads": {
                "op_ent_1": {
                    "entity_kind": "internal",
                    "entity": {
                        "entity_id": "ent_person_1",
                        "canonical_name": "Alex Rivera",
                        "node_type": "person",
                    },
                }
            }
        },
    }
    conflicts = detect_mutation_conflicts(change_set, db_path)
    assert conflicts == []
