"""Adversarial & edge-case tests for Wave 2 hygiene SQL remediation handlers (Defects D3, D4, D6, D7, D10)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from dual_pipeline.core import sha256_file
from dual_pipeline.graph_maintenance_core.hygiene_agent import (
    Finding,
    SynthesisJudgeAgent,
    _filter_and_vet_findings,
)
from dual_pipeline.graph_maintenance_core.hygiene_council import load_active_boundary_exclusions
from dual_pipeline.graph_scout_core.scans import scan_duplicate_and_malformed_sources
from tools.apply_graph_hygiene_remediation import apply_hygiene_remediation


def _init_full_remediation_schema(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT,
            canonical_name TEXT,
            normalized_name TEXT,
            active INTEGER DEFAULT 1,
            status TEXT DEFAULT 'active',
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE node_state (
            node_id TEXT PRIMARY KEY,
            lifecycle_state TEXT,
            currentness_state TEXT,
            retrieval_eligibility TEXT,
            resolution_state TEXT,
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
            to_node_id TEXT,
            merge_event_id TEXT,
            active INTEGER,
            created_at TEXT
        );
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY,
            source_type TEXT,
            source_name TEXT,
            source_date TEXT,
            authority_rank INTEGER DEFAULT 1,
            source_posture TEXT DEFAULT 'governed',
            cold_archive_ref TEXT,
            notes TEXT,
            created_at TEXT
        );
        CREATE TABLE source_version (
            source_version_id TEXT PRIMARY KEY,
            source_id TEXT REFERENCES source(source_id),
            version_number INTEGER,
            source_date_normalized TEXT
        );
        CREATE TABLE evidence_item (
            evidence_id TEXT PRIMARY KEY,
            source_id TEXT REFERENCES source(source_id),
            source_version_id TEXT
        );
        CREATE TABLE source_label_state (
            source_id TEXT PRIMARY KEY REFERENCES source(source_id),
            label_state TEXT
        );
        CREATE TABLE ingestion_source (
            source_id TEXT PRIMARY KEY REFERENCES source(source_id),
            ingested_at TEXT
        );
        CREATE TABLE alias (
            alias_id TEXT PRIMARY KEY,
            node_id TEXT,
            alias_name TEXT,
            normalized_alias TEXT,
            source_id TEXT REFERENCES source(source_id),
            UNIQUE(node_id, normalized_alias)
        );
        CREATE TABLE canonical_alias (
            alias_id TEXT PRIMARY KEY,
            entity_id TEXT,
            alias_text TEXT,
            normalized_alias TEXT,
            source_id TEXT REFERENCES source(source_id),
            UNIQUE(entity_id, normalized_alias)
        );
        CREATE TABLE edge (
            edge_id TEXT PRIMARY KEY,
            from_node_id TEXT,
            edge_type TEXT,
            to_node_id TEXT,
            description TEXT,
            source_id TEXT REFERENCES source(source_id),
            currentness_state TEXT DEFAULT 'current',
            superseded_by TEXT,
            updated_at TEXT
        );
        CREATE UNIQUE INDEX ux_edge_exact_current
            ON edge(from_node_id, edge_type, to_node_id, coalesce(description,''), coalesce(source_id,''))
            WHERE currentness_state = 'current';
        CREATE TABLE validation_queue (
            queue_id TEXT PRIMARY KEY,
            edge_id TEXT REFERENCES edge(edge_id)
        );
        CREATE TABLE legacy_edge_assertion_map (
            edge_id TEXT PRIMARY KEY REFERENCES edge(edge_id),
            assertion_id TEXT,
            migration_confidence REAL,
            migration_state TEXT,
            created_at TEXT
        );
        CREATE TABLE claim (
            claim_id TEXT PRIMARY KEY,
            source_id TEXT REFERENCES source(source_id),
            claim_text TEXT,
            subject_node_id TEXT,
            updated_at TEXT,
            UNIQUE(source_id, claim_text)
        );
        CREATE TABLE claim_confidence_hitl_matrix (
            claim_id TEXT PRIMARY KEY REFERENCES claim(claim_id),
            score REAL
        );
        CREATE TABLE claim_hitl_validation_queue (
            claim_id TEXT PRIMARY KEY REFERENCES claim(claim_id),
            status TEXT
        );
        CREATE TABLE governance_approval (
            approval_reference TEXT PRIMARY KEY,
            status TEXT,
            active INTEGER
        );
        INSERT INTO governance_approval VALUES ('REQ_INGEST_CEC57955C84BCE3A', 'approved', 1);
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            legacy_predicate TEXT,
            object_node_id TEXT,
            scope_text TEXT,
            canonical_status TEXT,
            canonical_eligible INTEGER,
            retrieval_eligible INTEGER,
            currentness_state TEXT,
            validation_state TEXT,
            confidence_score REAL,
            primary_source_id TEXT REFERENCES source(source_id),
            approval_reference TEXT,
            superseded_by_assertion_id TEXT,
            superseded_at TEXT,
            valid_from TEXT,
            valid_to TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE assertion_evidence (
            assertion_id TEXT,
            evidence_id TEXT,
            evidence_relationship TEXT,
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
            retrieval_eligible INTEGER,
            source_id TEXT REFERENCES source(source_id),
            assertion_id TEXT,
            updated_at TEXT
        );
        CREATE TRIGGER search_document_ad
        AFTER DELETE ON search_document
        BEGIN
          SELECT CASE
            WHEN OLD.object_type = 'assertion'
              AND EXISTS (SELECT 1 FROM assertion WHERE assertion_id = OLD.assertion_id)
            THEN RAISE(ABORT, 'cannot delete derived assertion search document while upstream exists')
          END;
        END;
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
            operator_signature TEXT,
            migration_or_ingestion_run_id TEXT,
            release_version TEXT
        );
        CREATE TABLE graph_quality_exception (
            exception_id TEXT PRIMARY KEY,
            metric_name TEXT,
            object_type TEXT,
            object_id TEXT,
            exception_type TEXT,
            reason TEXT,
            control_action TEXT,
            owner TEXT,
            approved_reference TEXT,
            active INTEGER,
            created_at TEXT
        );
        CREATE TABLE entity_resolution_candidate (
            candidate_id TEXT PRIMARY KEY,
            candidate_type TEXT,
            normalized_key TEXT,
            node_type_scope TEXT,
            status TEXT,
            auto_merge_allowed INTEGER,
            rationale TEXT,
            created_at TEXT,
            resolved_at TEXT
        );
        """
    )
    conn.commit()
    conn.close()


def test_source_repoint_fk_and_unique_index_collisions(tmp_path: Path) -> None:
    """Defect D3: Merging duplicate sources must repoint edge/claim/ingestion_source/alias without FK or unique errors."""
    db_path = tmp_path / "graph_d3.sqlite"
    _init_full_remediation_schema(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        INSERT INTO node VALUES ('n1', 'stakeholder', 'Alice', 'alice', 1, 'active', '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z');
        INSERT INTO node VALUES ('n2', 'workstream', 'Project X', 'project x', 1, 'active', '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z');
        INSERT INTO source VALUES ('source-win', 'meeting', 'Architecture Deep Dive Sync', '2026-10-20', 1, 'governed', 'output/p/10-20-2026/01_Architecture_Deep_Dive_Sync.md', '', '2026-10-20T00:00:00Z');
        INSERT INTO source VALUES ('source-lose', 'meeting', 'Unknown', '2026-10-20', 1, 'governed', 'output/p/10-20-2026/01_Architecture_Deep_Dive_Sync.md', '', '2026-10-20T00:00:00Z');

        INSERT INTO source_version VALUES ('sv_win', 'source-win', 1, '2026-10-20');
        INSERT INTO source_version VALUES ('sv_lose', 'source-lose', 1, '2026-10-20');
        INSERT INTO evidence_item VALUES ('ev_lose', 'source-lose', 'sv_lose');
        INSERT INTO ingestion_source VALUES ('source-win', '2026-10-20T00:00:00Z');
        INSERT INTO ingestion_source VALUES ('source-lose', '2026-10-20T00:00:00Z');
        INSERT INTO alias VALUES ('al_1', 'n1', 'Alice', 'alice', 'source-lose');
        INSERT INTO canonical_alias VALUES ('cal_1', 'n1', 'Alice', 'alice', 'source-lose');

        -- Colliding edges under ux_edge_exact_current
        INSERT INTO edge VALUES ('e_win', 'n1', 'LEADS', 'n2', 'same desc', 'source-win', 'current', NULL, NULL);
        INSERT INTO edge VALUES ('e_lose_collide', 'n1', 'LEADS', 'n2', 'same desc', 'source-lose', 'current', NULL, NULL);
        INSERT INTO validation_queue VALUES ('vq_1', 'e_lose_collide');
        INSERT INTO legacy_edge_assertion_map VALUES ('e_lose_collide', 'a_dummy', 1.0, 'structured', '2026-10-20T00:00:00Z');
        -- Non-colliding edge on loser source
        INSERT INTO edge VALUES ('e_lose_move', 'n1', 'REVIEWS', 'n2', 'distinct desc', 'source-lose', 'current', NULL, NULL);

        -- Colliding and non-colliding claims
        INSERT INTO claim VALUES ('c_win', 'source-win', 'Shared claim text', NULL, NULL);
        INSERT INTO claim VALUES ('c_lose_collide', 'source-lose', 'Shared claim text', NULL, NULL);
        INSERT INTO claim_confidence_hitl_matrix VALUES ('c_lose_collide', 0.9);
        INSERT INTO claim_hitl_validation_queue VALUES ('c_lose_collide', 'open');
        INSERT INTO claim VALUES ('c_lose_move', 'source-lose', 'Unique claim text', NULL, NULL);
        """
    )
    conn.commit()
    conn.close()

    pkg_path = tmp_path / "pkg_d3.json"
    pkg_path.write_text(
        json.dumps(
            {
                "strict_package_gating": True,
                "findings": [
                    {
                        "detector": "SourceHygieneScanner",
                        "severity": "HIGH",
                        "description": "Duplicate source cluster",
                        "entity_references": ["source-win", "source-lose"],
                        "proposed_fix": "consolidate_duplicate_sources",
                        "raw_data": {"winner_source_id": "source-win"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    receipt = apply_hygiene_remediation(graph_path=db_path, package_path=pkg_path)
    assert receipt["terminal_outcome"] == "PASS"
    assert receipt["remediation_stats"]["duplicate_sources_remediated"] >= 1

    conn = sqlite3.connect(str(db_path))
    remaining_srcs = [r[0] for r in conn.execute("SELECT source_id FROM source").fetchall()]
    assert remaining_srcs == ["source-win"]
    moved_edge_src = conn.execute("SELECT source_id FROM edge WHERE edge_id='e_lose_move'").fetchone()[0]
    assert moved_edge_src == "source-win"
    moved_claim_src = conn.execute("SELECT source_id FROM claim WHERE claim_id='c_lose_move'").fetchone()[0]
    assert moved_claim_src == "source-win"
    assert scan_duplicate_and_malformed_sources(conn) == []
    conn.close()


def test_repoint_node_assertions_preserves_null_legacy_predicate(tmp_path: Path) -> None:
    """Defect D4: Merging entities with legacy_predicate IS NULL must NOT mark distinct assertions historical."""
    db_path = tmp_path / "graph_d4.sqlite"
    _init_full_remediation_schema(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        INSERT INTO source VALUES ('src_1', 'meeting', 'Planning Sync', '2026-10-20', 1, 'governed', 'output/p/10-20-2026/01_Planning_Sync.md', '', '2026-10-20T00:00:00Z');
        INSERT INTO node VALUES ('org_survivor', 'organization', 'Acme Corp', 'acme corp', 1, 'active', '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z');
        INSERT INTO node VALUES ('node_retired', 'organization', 'Acme Corporation', 'acme corporation', 1, 'active', '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z');
        INSERT INTO node VALUES ('target_a', 'workstream', 'Stream A', 'stream a', 1, 'active', '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z');
        INSERT INTO node VALUES ('target_b', 'workstream', 'Stream B', 'stream b', 1, 'active', '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z');

        -- Survivor already has an assertion to target_a with predicate OWNS and NULL legacy_predicate
        INSERT INTO assertion VALUES (
            'asrt_surv_1', 'org_survivor', 'OWNS', NULL, 'target_a', 'Acme owns Stream A',
            'canonical', 1, 1, 'current', 'accepted_legacy', 0.95, 'src_1', 'REQ_INGEST_CEC57955C84BCE3A', NULL, NULL, NULL, NULL, '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z'
        );
        -- Retired node has an assertion to target_a with a DIFFERENT predicate SPONSORS and distinct scope_text (legacy_predicate IS NULL)
        INSERT INTO assertion VALUES (
            'asrt_ret_distinct', 'node_retired', 'SPONSORS', NULL, 'target_a', 'Acme sponsors Stream A budget',
            'canonical', 1, 1, 'current', 'accepted_legacy', 0.95, 'src_1', 'REQ_INGEST_CEC57955C84BCE3A', NULL, NULL, NULL, NULL, '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z'
        );
        -- Retired node also has an assertion to target_b
        INSERT INTO assertion VALUES (
            'asrt_ret_b', 'node_retired', 'OWNS', NULL, 'target_b', 'Acme owns Stream B',
            'canonical', 1, 1, 'current', 'accepted_legacy', 0.95, 'src_1', 'REQ_INGEST_CEC57955C84BCE3A', NULL, NULL, NULL, NULL, '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z'
        );
        """
    )
    conn.commit()
    conn.close()

    pkg_path = tmp_path / "pkg_d4.json"
    pkg_path.write_text(
        json.dumps(
            {
                "strict_package_gating": True,
                "findings": [
                    {
                        "detector": "EntityDuplicateDetector",
                        "severity": "HIGH",
                        "description": "Duplicate entity pair",
                        "entity_references": ["org_survivor", "node_retired"],
                        "proposed_fix": "merge_entities",
                        "raw_data": {"node_id_a": "org_survivor", "node_id_b": "node_retired"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    receipt = apply_hygiene_remediation(graph_path=db_path, package_path=pkg_path)
    assert receipt["terminal_outcome"] == "PASS"

    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT assertion_id, subject_node_id, canonical_status, currentness_state, retrieval_eligible FROM assertion ORDER BY assertion_id"
    ).fetchall()
    conn.close()
    by_id = {r[0]: r[1:] for r in rows}
    assert by_id["asrt_ret_distinct"] == ("org_survivor", "canonical", "current", 1)
    assert by_id["asrt_ret_b"] == ("org_survivor", "canonical", "current", 1)


def test_seven_additional_sql_handlers_and_sidecar_dedup(tmp_path: Path) -> None:
    """Defects D6 & D7: Verify all 7 new proposed_fix SQL handlers, candidate retrieval_eligible=1, and search_document dedup."""
    db_path = tmp_path / "graph_d6.sqlite"
    _init_full_remediation_schema(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        INSERT INTO source VALUES ('src_1', 'meeting', 'Sync', '2026-10-20', 1, 'governed', 'output/p/10-20-2026/01_Sync.md', '', '2026-10-20T00:00:00Z');
        INSERT INTO evidence_item VALUES ('ev_1', 'src_1', NULL);
        INSERT INTO node VALUES ('n1', 'stakeholder', 'Bob', 'bob', 1, 'active', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
        INSERT INTO node VALUES ('n2', 'workstream', 'Workstream Z', 'workstream z', 1, 'active', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');

        -- Candidate assertion to promote (must get retrieval_eligible=1)
        INSERT INTO assertion VALUES (
            'a_cand_promo', 'n1', 'OWNS', NULL, 'n2', 'Bob owns Workstream Z',
            'candidate', 1, 0, 'current', 'accepted_legacy', 0.92, 'src_1', 'REQ_INGEST_CEC57955C84BCE3A', NULL, NULL, NULL, NULL, '2026-10-10T00:00:00Z', '2026-10-10T00:00:00Z'
        );
        INSERT INTO assertion_evidence VALUES ('a_cand_promo', 'ev_1', 'supports', '2026-10-10T00:00:00Z');
        INSERT INTO search_document VALUES ('a_cand_promo', 'assertion', 'a_cand_promo', 'Bob', 'Bob owns Workstream Z', 'candidate', 'current', 0, 'src_1', 'a_cand_promo', '2026-10-10T00:00:00Z');

        -- Non-canonical assertion with leaked retrieval_eligible=1 in search_document
        INSERT INTO assertion VALUES (
            'a_leaked', 'n1', 'MENTIONS', NULL, 'n2', 'Leaked candidate',
            'historical_candidate', 0, 0, 'historical', 'pending', 0.40, 'src_1', NULL, NULL, NULL, NULL, NULL, '2026-05-01T00:00:00Z', '2026-05-01T00:00:00Z'
        );
        INSERT INTO search_document VALUES ('sd_leaked', 'assertion', 'a_leaked', 'Leaked', 'Leaked', 'historical_candidate', 'current', 1, 'src_1', 'a_leaked', '2026-05-01T00:00:00Z');

        -- Canonical assertion with both 'claim' and 'assertion' search_document rows (duplicate in search_document)
        INSERT INTO assertion VALUES (
            'a_canon_1', 'n1', 'LEADS', NULL, 'n2', 'Bob leads Workstream Z',
            'canonical', 1, 1, 'current', 'accepted_legacy', 0.95, 'src_1', 'REQ_INGEST_CEC57955C84BCE3A', NULL, NULL, NULL, NULL, '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z'
        );
        INSERT INTO assertion_evidence VALUES ('a_canon_1', 'ev_1', 'supports', '2026-10-01T00:00:00Z');
        INSERT INTO search_document VALUES ('claim_1', 'claim', 'claim_1', 'Bob leads', 'Bob leads Workstream Z', 'canonical', 'current', 1, 'src_1', 'a_canon_1', '2026-10-01T00:00:00Z');
        INSERT INTO search_document VALUES ('a_canon_1', 'assertion', 'a_canon_1', 'Bob leads', 'Bob leads Workstream Z', 'canonical', 'current', 1, 'src_1', 'a_canon_1', '2026-10-01T00:00:00Z');

        -- 90d revalidation assertion & ungrounded assertion
        INSERT INTO assertion VALUES (
            'a_90d', 'n1', 'ADVISES', NULL, 'n2', 'Old advisory',
            'canonical', 1, 1, 'current', 'accepted_legacy', 0.80, 'src_1', 'REQ_INGEST_CEC57955C84BCE3A', NULL, NULL, NULL, NULL, '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z'
        );
        INSERT INTO assertion VALUES (
            'a_ungrounded', 'n1', 'SUPPORTS', NULL, 'n2', 'Ungrounded claim',
            'canonical', 1, 1, 'current', 'accepted_legacy', 0.80, 'src_1', 'REQ_INGEST_CEC57955C84BCE3A', NULL, NULL, NULL, NULL, '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z'
        );

        -- Unmapped edge needing synthesize_edge_assertion
        INSERT INTO edge VALUES ('edge_unmapped', 'n1', 'PARTNERS_WITH', 'n2', 'Bob partners with Z', 'src_1', 'current', NULL, NULL);

        -- Orphaned assertion_evidence row for run_graph_gc & unsigned audit_event
        INSERT INTO assertion_evidence VALUES ('a_nonexistent', 'ev_1', 'supports', '2026-01-01T00:00:00Z');
        INSERT INTO audit_event VALUES ('aud_unsigned', 'user', 'write', 'node', 'n1', NULL, '{}', 'src_1', '2026-10-01T00:00:00Z', 'reason', NULL, '', '', 'run-1', '1.0');
        """
    )
    conn.commit()
    conn.close()

    pkg_path = tmp_path / "pkg_d6.json"
    pkg_path.write_text(
        json.dumps(
            {
                "strict_package_gating": True,
                "findings": [
                    {"proposed_fix": "batch_promote_candidates", "entity_references": ["a_cand_promo"], "raw_data": {"promotable_ids": ["a_cand_promo"]}},
                    {"proposed_fix": "deactivate_search_documents", "entity_references": ["a_leaked"], "raw_data": {"leaked_count": 1}},
                    {"proposed_fix": "resync_search_sidecars", "entity_references": ["a_canon_1"], "raw_data": {"missing_in_sidecar": 0}},
                    {"proposed_fix": "escalate_90d_revalidation", "entity_references": ["a_90d"], "raw_data": {"assertion_id": "a_90d"}},
                    {"proposed_fix": "reconcile_evidence", "entity_references": ["a_ungrounded"], "raw_data": {"ungrounded_assertion_ids": ["a_ungrounded"]}},
                    {"proposed_fix": "synthesize_edge_assertion", "entity_references": ["edge_unmapped"], "raw_data": {"edge_id": "edge_unmapped"}},
                    {"proposed_fix": "quarantine_audit_record", "entity_references": ["aud_unsigned"], "raw_data": {"audit_event_id": "aud_unsigned"}},
                    {"proposed_fix": "run_graph_gc", "entity_references": ["assertion_evidence"], "raw_data": {"orphaned_evidence_records": 1}},
                ],
            }
        ),
        encoding="utf-8",
    )
    receipt = apply_hygiene_remediation(graph_path=db_path, package_path=pkg_path)
    assert receipt["terminal_outcome"] == "PASS"
    stats = receipt["remediation_stats"]
    assert stats["candidates_promoted"] == 1
    assert stats["leaked_search_docs_deactivated"] == 1
    assert stats["edge_assertions_synthesized"] == 1
    assert stats["revalidation_90d_archived"] == 1
    assert stats["ungrounded_assertions_reconciled"] == 1
    assert stats["audit_records_quarantined"] == 1
    assert stats["graph_gc_records_cleaned"] >= 1

    conn = sqlite3.connect(str(db_path))
    promo_row = conn.execute("SELECT canonical_status, retrieval_eligible FROM assertion WHERE assertion_id='a_cand_promo'").fetchone()
    assert promo_row == ("canonical", 1)
    sd_cnt = conn.execute("SELECT COUNT(*) FROM search_document WHERE assertion_id='a_canon_1'").fetchone()[0]
    assert sd_cnt == 1
    mapped_edge = conn.execute("SELECT assertion_id FROM legacy_edge_assertion_map WHERE edge_id='edge_unmapped'").fetchone()
    assert mapped_edge is not None
    aud_sig = conn.execute("SELECT operator_signature FROM audit_event WHERE audit_event_id='aud_unsigned'").fetchone()[0]
    assert aud_sig == "QUARANTINED_BY_HYGIENE_COUNCIL"
    conn.close()


def test_cluster_filtering_universal_exclusions_and_dry_run(tmp_path: Path) -> None:
    """Defects D6 & D10: Verify --approved-clusters, --excluded-clusters, HYGIENE_FINDING_EXCLUDED persistence, and --dry-run."""
    db_path = tmp_path / "graph_d10.sqlite"
    _init_full_remediation_schema(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        INSERT INTO source VALUES ('src_1', 'meeting', 'Sync', '2026-10-20', 1, 'governed', 'output/p/10-20-2026/01_Sync.md', '', '2026-10-20T00:00:00Z');
        INSERT INTO node VALUES ('n_orphan', 'concept', 'Temp Scratch', 'temp scratch', 1, 'active', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
        INSERT INTO assertion VALUES (
            'a_stale_1', 'n_orphan', 'REL', NULL, 'n_orphan', 'Stale candidate',
            'candidate', 0, 0, 'current', 'pending', 0.30, 'src_1', NULL, NULL, NULL, NULL, NULL, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'
        );
        """
    )
    conn.commit()
    conn.close()

    findings = [
        {
            "detective": "CandidateBacklogAuditor",
            "severity": "MEDIUM",
            "description": "Stale candidate assertion",
            "entity_references": ["a_stale_1"],
            "proposed_fix": "archive_candidate_assertion",
            "raw_data": {"assertion_id": "a_stale_1", "age_days": 120, "confidence_score": 0.3},
        },
        {
            "detective": "OrphanClutterDetector",
            "severity": "LOW",
            "description": "Orphan node",
            "entity_references": ["n_orphan"],
            "proposed_fix": "archive_node",
            "raw_data": {"node_id": "n_orphan", "canonical_name": "Temp Scratch"},
        },
    ]
    finding_objs = [Finding(**f) for f in findings]
    clusters = SynthesisJudgeAgent().synthesize(finding_objs).hitl_clusters
    pkg_path = tmp_path / "pkg_d10.json"
    pkg_path.write_text(
        json.dumps(
            {
                "strict_package_gating": True,
                "findings": findings,
                "hitl_clusters": clusters,
            }
        ),
        encoding="utf-8",
    )

    # 1. Dry-run must leave db_path byte-for-byte identical while returning PASS
    pre_sha = sha256_file(db_path)
    dry_receipt = apply_hygiene_remediation(
        graph_path=db_path,
        package_path=pkg_path,
        approved_clusters=["CLUSTER-01-UNPROMOTED-STAGING-DRAFTS"],
        excluded_clusters=["CLUSTER-08-GOVERNANCE-AUDIT"],
        dry_run=True,
    )
    assert dry_receipt["terminal_outcome"] == "PASS"
    assert dry_receipt["dry_run"] is True
    assert sha256_file(db_path) == pre_sha

    # 2. Live run with CLUSTER-01 approved and CLUSTER-08 excluded persists HYGIENE_FINDING_EXCLUDED
    live_receipt = apply_hygiene_remediation(
        graph_path=db_path,
        package_path=pkg_path,
        approved_clusters=["CLUSTER-01-UNPROMOTED-STAGING-DRAFTS"],
        excluded_clusters=["CLUSTER-08-GOVERNANCE-AUDIT"],
        dry_run=False,
    )
    assert live_receipt["terminal_outcome"] == "PASS"
    assert live_receipt["remediation_stats"]["stale_candidates_archived"] == 1
    assert live_receipt["remediation_stats"]["orphan_nodes_archived"] == 0
    assert live_receipt["remediation_stats"]["boundary_exclusions_recorded"] == 1

    # 3. Verify _filter_and_vet_findings suppresses the excluded finding on subsequent scan
    conn = sqlite3.connect(str(db_path))
    exc_row = conn.execute(
        "SELECT exception_type, metric_name, object_id FROM graph_quality_exception WHERE exception_type='HYGIENE_FINDING_EXCLUDED'"
    ).fetchone()
    assert exc_row == ("HYGIENE_FINDING_EXCLUDED", "archive_node", "n_orphan")
    excl_set = load_active_boundary_exclusions(conn)
    conn.close()

    vetted, _ = _filter_and_vet_findings(finding_objs, set(), excluded_pairs=excl_set)
    assert [f.proposed_fix for f in vetted] == ["archive_candidate_assertion"]
