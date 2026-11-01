"""Adversarial edge-case tests for entity redirect resolution, post-merge assertion dedup, and parity promotion guards."""
from __future__ import annotations

from pathlib import Path

from tests.test_hygiene_parity_reduplication_e2e import _init_minimal_hygiene_schema
from tools.apply_graph_hygiene_remediation import _remediate_duplicate_assertions, _remediate_entity_merges
from tools.tier2_dedup_core.document_graph_parity_core import (
    _ensure_node_exists,
    promote_all_candidates_and_empty_sources,
    promote_candidate_nodes_and_link_orphans,
)


def test_adversarial_boundary_empty_and_null_node_inputs(tmp_path: Path) -> None:
    """Category 1 (Boundary payload) & Category 2 (Malformed input): empty/whitespace node_id and name."""
    conn = _init_minimal_hygiene_schema(tmp_path / "adv1.sqlite")
    now = "2026-10-27T06:00:00Z"
    nid = _ensure_node_exists(conn, "   ", "", "", "", now)
    assert nid == "org_nea"
    conn.close()


def test_adversarial_redirect_chain_and_inactive_merged_node_bypass(tmp_path: Path) -> None:
    """Category 5 (Invariant bypass) & Category 6 (Recovery): multi-hop redirect chain and candidate duplicate promotion."""
    conn = _init_minimal_hygiene_schema(tmp_path / "adv2.sqlite")
    now = "2026-10-27T06:00:00Z"
    conn.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, normalized_name, status, active) "
        "VALUES ('entity-canon', 'stakeholder', 'Chris Morris', 'chris morris', 'canonical', 1)"
    )
    conn.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, normalized_name, status, active) "
        "VALUES ('person:chris-morris-v1', 'stakeholder', 'Chris Morris', 'chris morris', 'merged', 0)"
    )
    conn.execute(
        "INSERT INTO entity_redirect (from_node_id, to_node_id, merge_event_id, active, created_at) "
        "VALUES ('person:chris-morris-v1', 'entity-canon', 'merge-1', 1, ?)"
        , (now,)
    )
    conn.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, normalized_name, status, active) "
        "VALUES ('person:chris-morris-cand', 'stakeholder', 'Chris Morris', 'chris morris', 'candidate', 1)"
    )
    conn.commit()

    # Calling _ensure_node_exists on a merged node ID must return the redirect target, not the inactive merged node
    resolved = _ensure_node_exists(conn, "person:chris-morris-v1", "person", "Chris Morris", "", now)
    assert resolved == "entity-canon"

    # Promoting candidate nodes must merge/retire candidate duplicates when an active canonical node with same name exists
    promote_candidate_nodes_and_link_orphans(conn, now)
    active_rows = conn.execute(
        "SELECT node_id FROM node WHERE normalized_name = 'chris morris' AND active = 1 AND status = 'canonical'"
    ).fetchall()
    assert [r[0] for r in active_rows] == ["entity-canon"]
    conn.close()


def test_adversarial_post_entity_merge_newly_exposed_duplicate_assertions(tmp_path: Path) -> None:
    """Category 7 (Exact production regression): post-merge newly exposed duplicate assertions are swept under strict_package_gating."""
    conn = _init_minimal_hygiene_schema(tmp_path / "adv3.sqlite")
    now = "2026-10-27T06:00:00Z"
    conn.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, normalized_name, status, active) "
        "VALUES ('entity-petter', 'stakeholder', 'Petter Skantze', 'petter skantze', 'canonical', 1)"
    )
    conn.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, normalized_name, status, active) "
        "VALUES ('org_nee', 'org', 'Apex Global Enterprises', 'apex_global energy', 'canonical', 1)"
    )
    conn.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, normalized_name, status, active) "
        "VALUES ('org:apex_global-energy-inc', 'org', 'Apex Global Enterprises, Inc.', 'apex_global energy inc', 'canonical', 1)"
    )
    conn.execute("INSERT INTO source (source_id, source_name, source_date) VALUES ('src-1', 'M1', '2026-10-26')")
    conn.execute("INSERT INTO evidence_item (evidence_id, source_id, direct_quote) VALUES ('ev-1', 'src-1', 'Q1')")

    # Before merge, object_node_id differs ('org_nee' vs 'org:apex_global-energy-inc'), so pre-merge scan did not pair them
    conn.execute(
        "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, scope_text, "
        "primary_source_id, confidence_score, validation_state, currentness_state, canonical_status, "
        "canonical_eligible, retrieval_eligible, recorded_at, created_at, updated_at) "
        "VALUES ('a-new', 'entity-petter', 'AFFILIATED_WITH', 'org_nee', 'Petter Skantze MEMBER_OF Apex Global Enterprises.', "
        "'src-1', 0.95, 'accepted_legacy', 'current', 'canonical', 1, 1, '2026-10-24T18:55:30Z', ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, scope_text, "
        "primary_source_id, confidence_score, validation_state, currentness_state, canonical_status, "
        "canonical_eligible, retrieval_eligible, recorded_at, created_at, updated_at) "
        "VALUES ('a-old', 'entity-petter', 'AFFILIATED_WITH', 'org:apex_global-energy-inc', 'Petter Skantze MEMBER_OF Apex Global Enterprises, Inc..', "
        "'src-1', 0.95, 'accepted_legacy', 'current', 'canonical', 1, 1, '2026-10-20T12:45:09Z', ?, ?)",
        (now, now),
    )
    conn.execute("INSERT INTO assertion_evidence VALUES ('a-new', 'ev-1', 'supports', 0, ?)", (now,))
    conn.execute("INSERT INTO assertion_evidence VALUES ('a-old', 'ev-1', 'supports', 0, ?)", (now,))

    pkg = {
        "strict_package_gating": True,
        "findings": [
            {
                "proposed_fix": "merge_entities",
                "raw_data": {"node_id_a": "org_nee", "node_id_b": "org:apex_global-energy-inc"},
            },
            {
                "proposed_fix": "deduplicate_assertions",
                "raw_data": {"winner_id": "a-new", "loser_id": "nonexistent-other"},
            },
        ],
    }
    _remediate_entity_merges(conn, pkg, now, [])
    _remediate_duplicate_assertions(conn, pkg, now)
    conn.commit()

    active_asrt = conn.execute(
        "SELECT assertion_id FROM assertion WHERE currentness_state = 'current' AND canonical_status = 'canonical'"
    ).fetchall()
    assert [r[0] for r in active_asrt] == ["a-new"]

    # Ensure promote_all_candidates_and_empty_sources does not resurrect 'a-old'
    promote_all_candidates_and_empty_sources(conn, now)
    active_after_parity = conn.execute(
        "SELECT assertion_id FROM assertion WHERE currentness_state = 'current' AND canonical_status = 'canonical'"
    ).fetchall()
    assert [r[0] for r in active_after_parity] == ["a-new"]
    conn.close()


def test_adversarial_uwg_role_persistence_with_semantic_guard_trigger_and_sealed_gen_guard(tmp_path: Path) -> None:
    """Category 4 & 5: UWG role persistence satisfies semantic guard triggers and c05_title_gate blocks implicit sealed generation mutation."""
    import json
    import sqlite3
    from dual_pipeline.transcript_stages_core.c05_title_gate import _persist_role_to_graphdb
    from dual_pipeline.uwg_core.universal_write_gateway_role import persist_validated_role

    db_path = tmp_path / "uwg_trig.sqlite"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE controlled_value_registry (
            vocabulary TEXT NOT NULL,
            value TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (vocabulary, value)
        );
        INSERT INTO controlled_value_registry (vocabulary, value, active) VALUES
            ('validation_state', 'accepted_legacy', 1),
            ('validation_state', 'needs_review', 1),
            ('validation_state', 'superseded', 1),
            ('currentness_state', 'current', 1),
            ('currentness_state', 'historical', 1),
            ('canonical_status', 'canonical', 1);
        CREATE TABLE governance_approval (
            approval_id TEXT PRIMARY KEY,
            approval_reference TEXT,
            status TEXT,
            active INTEGER
        );
        INSERT INTO governance_approval VALUES ('app-1', 'REQ_INGEST_CEC57955C84BCE3A', 'approved', 1);
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT,
            canonical_name TEXT,
            display_name TEXT,
            normalized_name TEXT,
            description TEXT,
            status TEXT DEFAULT 'canonical',
            active INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE node_state (
            node_id TEXT PRIMARY KEY,
            lifecycle_state TEXT,
            currentness_state TEXT,
            retrieval_eligibility TEXT,
            updated_at TEXT
        );
        CREATE VIRTUAL TABLE node_fts USING fts5(node_id, canonical_name, display_name, description);
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY,
            source_type TEXT,
            source_name TEXT,
            source_date TEXT,
            authority_rank INTEGER,
            source_posture TEXT,
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
            object_node_id TEXT,
            scope_text TEXT,
            confidence_score REAL,
            confidence_label TEXT,
            validation_state TEXT,
            currentness_state TEXT,
            canonical_status TEXT,
            canonical_eligible INTEGER,
            retrieval_eligible INTEGER,
            valid_from TEXT,
            superseded_by_assertion_id TEXT,
            superseded_at TEXT,
            classification TEXT,
            allowed_audiences_json TEXT,
            primary_source_id TEXT,
            extraction_run_id TEXT,
            approval_reference TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE assertion_evidence (
            assertion_id TEXT,
            evidence_id TEXT,
            evidence_relationship TEXT,
            independent_corroboration INTEGER DEFAULT 1,
            created_at TEXT,
            PRIMARY KEY (assertion_id, evidence_id)
        );
        CREATE TRIGGER assertion_cvr_guard_bu BEFORE UPDATE ON assertion
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM controlled_value_registry WHERE vocabulary='validation_state' AND value=new.validation_state AND active=1
            ) THEN RAISE(ABORT, 'unregistered assertion controlled value') END;
        END;
        """
    )
    conn.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, display_name, description, active) "
        "VALUES ('person_eduardo_more', 'stakeholder', 'Eduardo (Ed) Juan More', 'Eduardo More', 'Role: Old', 1)"
    )
    conn.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, display_name, description, active) "
        "VALUES ('entity_a391e4b5', 'role_group', 'Atlas Lead / Rule Specialist', 'Atlas Lead / Rule Specialist', 'Role group', 1)"
    )
    conn.execute(
        "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, scope_text, "
        "confidence_score, validation_state, currentness_state, canonical_status, canonical_eligible, retrieval_eligible) "
        "VALUES ('asrt-hitl-a39b5f71138b', 'person_eduardo_more', 'HAS_ROLE_IN', 'entity_a391e4b5', "
        "'Eduardo More HAS_ROLE Atlas Lead / Rule Specialist.', 1.0, 'accepted_legacy', 'current', 'canonical', 1, 1)"
    )
    conn.commit()
    conn.close()

    # Must succeed without raising sqlite3.IntegrityError: unregistered assertion controlled value
    res1 = persist_validated_role(db_path, "Eduardo (Ed) Juan More", "Atlas Lead / Rule Specialist")
    assert res1["status"] == "PERSISTED_SSOT"
    res2 = persist_validated_role(db_path, "Eduardo (Ed) Juan More", "Principal UX Designer")
    assert res2["status"] == "PERSISTED_SSOT"

    ro = sqlite3.connect(db_path)
    assert ro.execute("SELECT count(*) FROM node").fetchone()[0] == ro.execute("SELECT count(*) FROM node_fts").fetchone()[0]
    ro.close()

    # Verify _persist_role_to_graphdb refuses to mutate a sealed generation in place when graph_path is not explicitly provided
    gen_dir = tmp_path / ".local" / "dual_pipeline" / "generations" / "generation-sealed"
    gen_dir.mkdir(parents=True)
    sealed_db = gen_dir / "graph.sqlite"
    sealed_db.write_bytes(db_path.read_bytes())
    (gen_dir / "pair_manifest.json").write_text(json.dumps({"generation_id": "generation-sealed"}), encoding="utf-8")
    (tmp_path / ".local" / "dual_pipeline" / "active_pair.json").write_text(
        json.dumps({"generation_id": "generation-sealed", "graph_path": "generations/generation-sealed/graph.sqlite"}),
        encoding="utf-8",
    )
    before_sha = sealed_db.read_bytes()
    meta: dict = {"runtime_root": str(tmp_path / ".local" / "dual_pipeline")}
    write_res = _persist_role_to_graphdb(meta, tmp_path, "Marcus Vance", "Principal UX Designer", "HUMAN_OPERATOR")
    assert write_res is None
    assert sealed_db.read_bytes() == before_sha

