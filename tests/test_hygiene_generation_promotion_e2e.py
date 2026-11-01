"""End-to-end tests for Knowledge Graph Hygiene Generation Promotion, Complete Fix Handlers & Sidecar Sync.

Verifies Wave 1 & Wave 3 deliverables:
1. Governed generation promotion (`--promote-generation` / `promote_generation=True`) clones the active
   generation pair into `generations/<new_gen_id>/`, applies approved fixes, updates `sidecar.sqlite`
   digests via `refresh_sidecar_after_graph_cleanup()`, writes `pair_manifest.json`, and atomically
   promotes `active_pair.json`.
2. Complete fix handler execution for all 9 fix types, including newly wired `auto_link_edge`
   (`CLUSTER-03-EDGE-PARITY`), `archive_node` (`CLUSTER-08-GOVERNANCE-AUDIT`), and `strict_package_gating`
   for `batch_promote_candidates` and `resync_search_sidecars`.
3. Five mandatory physical proof checks (`file_changed`, `raw_bytes`, `integrity`, `readback`, `row_delta`).
4. Post-promotion `GraphHygienePanel` scan confirms 0 repeated findings on remediated and excluded items.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Tuple

import pytest

from dual_pipeline.core import sha256_file
from dual_pipeline.graph_maintenance_core.hygiene_agent import GraphHygienePanel
from tools.apply_graph_hygiene_remediation import apply_hygiene_remediation


def _init_minimal_runtime_pair(runtime_root: Path) -> Tuple[Path, Path, str]:
    """Create a minimal hermetic runtime_root with generations/gen-initial/graph.sqlite + sidecar.sqlite."""
    gen_id = "generation-initial-0001"
    gen_dir = runtime_root / "generations" / gen_id
    gen_dir.mkdir(parents=True, exist_ok=True)
    graph_path = gen_dir / "graph.sqlite"
    sidecar_path = gen_dir / "sidecar.sqlite"

    conn = sqlite3.connect(str(graph_path))
    conn.executescript(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            display_name TEXT,
            description TEXT,
            status TEXT DEFAULT 'active',
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
            intentional_orphan INTEGER DEFAULT 0,
            intentional_orphan_reason TEXT,
            updated_at TEXT
        );
        CREATE TABLE edge (
            edge_id TEXT PRIMARY KEY,
            from_node_id TEXT NOT NULL,
            to_node_id TEXT NOT NULL,
            edge_type TEXT NOT NULL,
            description TEXT,
            evidence_posture TEXT DEFAULT 'grounded',
            confidence REAL DEFAULT 0.9,
            materiality TEXT DEFAULT 'high',
            validation_priority TEXT,
            validation_owner TEXT,
            hitl_required INTEGER DEFAULT 0,
            source_id TEXT DEFAULT 'src-1',
            superseded_by TEXT,
            updated_at TEXT
        );
        CREATE TABLE edge_type_domain_range (
            edge_type_id TEXT NOT NULL,
            source_node_type TEXT NOT NULL,
            target_node_type TEXT NOT NULL,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE legacy_edge_assertion_map (
            edge_id TEXT PRIMARY KEY,
            assertion_id TEXT NOT NULL,
            migration_confidence REAL,
            migration_state TEXT,
            created_at TEXT
        );
        CREATE TABLE validation_queue (
            queue_id TEXT PRIMARY KEY,
            node_id TEXT,
            edge_id TEXT
        );
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY,
            source_name TEXT
        );
        CREATE TABLE governance_approval (
            approval_reference TEXT PRIMARY KEY,
            status TEXT,
            active INTEGER
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            assertion_type TEXT DEFAULT 'fact',
            subject_node_id TEXT NOT NULL,
            predicate TEXT NOT NULL,
            legacy_predicate TEXT DEFAULT 'AFFILIATED_WITH',
            object_node_id TEXT,
            object_literal TEXT,
            scope_text TEXT,
            canonical_status TEXT DEFAULT 'canonical',
            canonical_eligible INTEGER DEFAULT 1,
            retrieval_eligible INTEGER DEFAULT 1,
            currentness_state TEXT DEFAULT 'current',
            validation_state TEXT DEFAULT 'accepted_legacy',
            confidence_score REAL DEFAULT 0.92,
            superseded_by_assertion_id TEXT,
            superseded_at TEXT,
            approval_reference TEXT DEFAULT 'REQ_INGEST_CEC57955C84BCE3A',
            primary_source_id TEXT DEFAULT 'src-1',
            classification TEXT DEFAULT 'internal',
            allowed_audiences_json TEXT DEFAULT '["internal_consulting"]',
            qualifiers_json TEXT DEFAULT '{}',
            valid_from TEXT DEFAULT '2026-10-01',
            valid_to TEXT,
            recorded_at TEXT DEFAULT '2026-10-01T00:00:00Z',
            created_at TEXT DEFAULT '2026-08-01T00:00:00Z',
            updated_at TEXT DEFAULT '2026-10-01T00:00:00Z'
        );
        CREATE TABLE evidence_item (
            evidence_id TEXT PRIMARY KEY,
            source_id TEXT,
            direct_quote TEXT,
            locator_value TEXT,
            extraction_confidence REAL DEFAULT 0.95,
            created_at TEXT DEFAULT '2026-10-01T00:00:00Z'
        );
        CREATE TABLE assertion_evidence (
            assertion_id TEXT NOT NULL,
            evidence_id TEXT NOT NULL,
            evidence_relationship TEXT DEFAULT 'supports',
            independent_corroboration INTEGER DEFAULT 0,
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
        CREATE TABLE canonical_alias (
            alias_id TEXT PRIMARY KEY,
            entity_id TEXT,
            alias_text TEXT
        );
        CREATE TABLE canonical_route (
            route_id TEXT PRIMARY KEY,
            object_type TEXT,
            object_id TEXT,
            active INTEGER DEFAULT 1,
            is_primary INTEGER DEFAULT 1
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

    conn.execute("INSERT INTO source VALUES ('src-1', 'Executive Steering Sync')")
    conn.execute("INSERT INTO governance_approval VALUES ('REQ_INGEST_CEC57955C84BCE3A', 'approved', 1)")
    conn.executemany(
        "INSERT INTO edge_type_domain_range VALUES (?, ?, ?, 1)",
        [
            ("AFFILIATED_WITH", "stakeholder", "org"),
            ("AFFILIATED_WITH", "role_group", "org"),
            ("AFFILIATED_WITH", "workstream", "org"),
            ("HAS_ROLE", "stakeholder", "role_group"),
        ],
    )
    conn.executemany(
        "INSERT INTO node (node_id, node_type, canonical_name, display_name, description, status, active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'active', 1, '2026-08-01T00:00:00Z', '2026-10-01T00:00:00Z')",
        [
            ("stakeholder_jose_medina", "stakeholder", "Jose Medina", "Jose Medina", "Nuclear Fleet VP"),
            ("stakeholder_jose_m_dup", "stakeholder", "Jose M. Medina", "Jose M. Medina", "Nuclear Fleet VP"),
            ("org_nea", "org", "Apex Global Analytics", "Apex Global Analytics", "Analytics organization"),
            ("entity_orphan_clutter", "role_group", "Analytics Lead", "Analytics Lead", "Orphan role node"),
            ("role_generic_lead", "role_group", "Technical Lead", "Technical Lead", "Generic technical lead role"),
            ("role_atlas_lead", "role_group", "Atlas Technical Lead", "Atlas Technical Lead", "Atlas project technical lead"),
        ],
    )
    conn.execute(
        "INSERT INTO node_state (node_id, lifecycle_state, currentness_state, retrieval_eligibility, intentional_orphan) VALUES ('entity_orphan_clutter', 'active', 'current', 'eligible', 0)"
    )

    # Canonical assertion + unmapped auto-linkable edge
    conn.execute(
        """INSERT INTO assertion (
            assertion_id, subject_node_id, predicate, object_node_id, scope_text,
            canonical_status, canonical_eligible, retrieval_eligible, currentness_state, validation_state
        ) VALUES (
            'assertion-jose-affiliated-nea', 'stakeholder_jose_medina', 'AFFILIATED_WITH', 'org_nea',
            'Jose Medina is affiliated with Apex Global Analytics.', 'canonical', 1, 1, 'current', 'accepted_legacy'
        )"""
    )
    conn.execute("INSERT INTO evidence_item VALUES ('ev-1', 'src-1', 'Jose Medina leads Apex Global Analytics.', 'L10', 0.95, '2026-10-01T00:00:00Z')")
    conn.execute("INSERT INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, created_at) VALUES ('assertion-jose-affiliated-nea', 'ev-1', 'supports', '2026-10-01T00:00:00Z')")

    # Stale candidate assertion redundant with canonical assertion
    conn.execute(
        """INSERT INTO assertion (
            assertion_id, subject_node_id, predicate, object_node_id, scope_text,
            canonical_status, canonical_eligible, retrieval_eligible, currentness_state, validation_state, created_at
        ) VALUES (
            'assertion-stale-cand-1', 'stakeholder_jose_medina', 'AFFILIATED_WITH', 'org_nea',
            'Draft note: Jose Medina affiliated with Apex Global Analytics.', 'candidate', 0, 0, 'current', 'needs_review', '2026-08-01T00:00:00Z'
        )"""
    )

    # Assertions for boundary-divergent roles so they are not orphans
    conn.execute(
        """INSERT INTO assertion (
            assertion_id, subject_node_id, predicate, object_node_id, scope_text,
            canonical_status, canonical_eligible, retrieval_eligible, currentness_state, validation_state
        ) VALUES (
            'assertion-role-a', 'role_generic_lead', 'AFFILIATED_WITH', 'org_nea',
            'Technical Lead is affiliated with Apex Global Analytics.', 'canonical', 1, 1, 'current', 'accepted_legacy'
        )"""
    )
    conn.execute("INSERT INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, created_at) VALUES ('assertion-role-a', 'ev-1', 'supports', '2026-10-01T00:00:00Z')")
    conn.execute(
        """INSERT INTO assertion (
            assertion_id, subject_node_id, predicate, object_node_id, scope_text,
            canonical_status, canonical_eligible, retrieval_eligible, currentness_state, validation_state
        ) VALUES (
            'assertion-role-b', 'role_atlas_lead', 'AFFILIATED_WITH', 'org_nea',
            'Atlas Technical Lead is affiliated with Apex Global Analytics.', 'canonical', 1, 1, 'current', 'accepted_legacy'
        )"""
    )
    conn.execute("INSERT INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, created_at) VALUES ('assertion-role-b', 'ev-1', 'supports', '2026-10-01T00:00:00Z')")

    # Assertion for duplicate stakeholder so it's linked (same scope_text as survivor so repoint supersedes cleanly)
    conn.execute(
        """INSERT INTO assertion (
            assertion_id, subject_node_id, predicate, object_node_id, scope_text,
            canonical_status, canonical_eligible, retrieval_eligible, currentness_state, validation_state
        ) VALUES (
            'assertion-jose-dup', 'stakeholder_jose_m_dup', 'AFFILIATED_WITH', 'org_nea',
            'Jose Medina is affiliated with Apex Global Analytics.', 'canonical', 1, 1, 'current', 'accepted_legacy'
        )"""
    )
    conn.execute("INSERT INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, created_at) VALUES ('assertion-jose-dup', 'ev-1', 'supports', '2026-10-01T00:00:00Z')")

    # Unmapped edge that matches assertion-jose-affiliated-nea
    conn.execute(
        "INSERT INTO edge (edge_id, from_node_id, to_node_id, edge_type, superseded_by) VALUES ('edge-jose-medina-affiliated-nea', 'stakeholder_jose_medina', 'org_nea', 'AFFILIATED_WITH', NULL)"
    )
    conn.commit()
    conn.close()

    # Initialize sidecar.sqlite
    sconn = sqlite3.connect(str(sidecar_path))
    sconn.executescript(
        """
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
        CREATE TABLE retrieval_record (
            record_id TEXT PRIMARY KEY,
            lane TEXT NOT NULL,
            record_type TEXT NOT NULL,
            namespace TEXT NOT NULL,
            retrieval_text_hash TEXT NOT NULL,
            retrieval_kind TEXT NOT NULL,
            epistemic_status TEXT NOT NULL,
            provenance_kind TEXT NOT NULL,
            answer_use TEXT NOT NULL,
            artifact_json TEXT NOT NULL
        ) WITHOUT ROWID;
        CREATE TABLE retrieval_record_object_facet (
            record_id TEXT NOT NULL,
            assignment_id TEXT NOT NULL,
            object_type TEXT NOT NULL,
            object_id TEXT NOT NULL,
            vocabulary TEXT NOT NULL,
            value TEXT NOT NULL,
            assignment_method TEXT NOT NULL,
            review_state TEXT NOT NULL,
            PRIMARY KEY (record_id, assignment_id)
        ) WITHOUT ROWID;
        CREATE TABLE dense_vector (record_id TEXT PRIMARY KEY, dimensions INTEGER NOT NULL, vector BLOB NOT NULL) WITHOUT ROWID;
        CREATE TABLE sparse_weight (record_id TEXT NOT NULL, token TEXT NOT NULL, weight REAL NOT NULL, PRIMARY KEY (record_id, token)) WITHOUT ROWID;
        CREATE TABLE colbert_token (record_id TEXT NOT NULL, position INTEGER NOT NULL, token TEXT NOT NULL, dimensions INTEGER NOT NULL, vector BLOB NOT NULL, PRIMARY KEY (record_id, position)) WITHOUT ROWID;
        """
    )
    g_sha = sha256_file(graph_path)
    sconn.executemany(
        "INSERT INTO metadata (key, value) VALUES (?, ?)",
        [
            ("generation_id", gen_id),
            ("graph_sha256", g_sha),
            ("bound_graph_sha256", g_sha),
        ],
    )
    sconn.commit()
    sconn.close()

    s_sha = sha256_file(sidecar_path)
    active_pair = {
        "generation_id": gen_id,
        "previous_generation_id": None,
        "graph_path": f"generations/{gen_id}/graph.sqlite",
        "sidecar_path": f"generations/{gen_id}/sidecar.sqlite",
        "graph_sha256": g_sha,
        "sidecar_sha256": s_sha,
        "activated_at": "2026-10-24T00:00:00Z",
    }
    (runtime_root / "active_pair.json").write_text(json.dumps(active_pair, indent=2), encoding="utf-8")
    return graph_path, sidecar_path, gen_id


def test_hygiene_generation_promotion_and_zero_repeat_e2e(tmp_path: Path) -> None:
    """End-to-end test verifying generation promotion, sidecar digest sync, and 0 repeated findings on re-scan."""
    runtime_root = tmp_path / "runtime"
    old_graph_path, _, old_gen_id = _init_minimal_runtime_pair(runtime_root)

    # 1. Run GraphHygienePanel on initial generation and verify findings exist
    panel = GraphHygienePanel()
    initial_pkg = panel.run_panel(old_graph_path)
    initial_fixes = {f.proposed_fix for f in initial_pkg.findings}
    assert "archive_candidate_assertion" in initial_fixes
    assert "merge_entities" in initial_fixes
    assert "auto_link_edge" in initial_fixes
    assert "archive_node" in initial_fixes
    assert "resync_search_sidecars" in initial_fixes

    # 2. Partition findings into approved fixes vs operator-rejected keep-distinct boundary pair
    approved_findings = []
    excluded_findings = []
    for f in initial_pkg.findings:
        raw = f.raw_data or {}
        if f.proposed_fix == "merge_entities" and set(f.entity_references) == {"role_generic_lead", "role_atlas_lead"}:
            excluded_findings.append(
                {
                    "entity_references": f.entity_references,
                    "raw_data": raw,
                    "exclusion_reason": "Keep project-specific Atlas Technical Lead distinct from generic Technical Lead",
                }
            )
        else:
            approved_findings.append(
                {
                    "detective": f.detective,
                    "severity": f.severity,
                    "description": f.description,
                    "entity_references": f.entity_references,
                    "proposed_fix": f.proposed_fix,
                    "raw_data": raw,
                }
            )

    assert len(excluded_findings) == 1
    pkg_path = tmp_path / "hygiene_package_approved.json"
    pkg_path.write_text(
        json.dumps(
            {
                "status": "approved",
                "strict_package_gating": True,
                "findings": approved_findings,
                "excluded_findings": excluded_findings,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    receipt_path = tmp_path / "run-10-25-2026" / "wave1_receipt.json"
    receipt = apply_hygiene_remediation(
        package_path=pkg_path,
        receipt_path=receipt_path,
        promote_generation=True,
        runtime_root=runtime_root,
    )

    # 3. Verify 5 physical proof checks and remediation stats
    assert receipt["terminal_outcome"] == "PASS"
    assert receipt["physical_proof"]["overall"] == "PASS"
    stats = receipt["remediation_stats"]
    assert stats["stale_candidates_archived"] == 1
    assert stats["entity_pairs_merged"] == 1
    assert stats["edges_auto_linked"] == 1
    assert stats["orphan_nodes_archived"] == 1
    assert stats["search_documents_resynced"] >= 1
    assert stats["boundary_exclusions_recorded"] == 1

    # 4. Verify active_pair.json promoted to new generation and sidecar digests match
    active_pair = json.loads((runtime_root / "active_pair.json").read_text(encoding="utf-8"))
    new_gen_id = active_pair["generation_id"]
    assert new_gen_id != old_gen_id
    assert active_pair["previous_generation_id"] == old_gen_id

    promoted_graph = runtime_root / active_pair["graph_path"]
    promoted_sidecar = runtime_root / active_pair["sidecar.sqlite"] if "sidecar.sqlite" in active_pair else runtime_root / active_pair["sidecar_path"]
    assert promoted_graph.is_file()
    assert promoted_sidecar.is_file()
    assert (promoted_graph.parent / "pair_manifest.json").is_file()
    assert sha256_file(promoted_graph) == active_pair["graph_sha256"]
    assert sha256_file(promoted_sidecar) == active_pair["sidecar_sha256"]

    with sqlite3.connect(f"file:{promoted_sidecar}?mode=ro", uri=True) as sconn:
        meta = dict(sconn.execute("SELECT key, value FROM metadata").fetchall())
        assert meta["bound_graph_sha256"] == active_pair["graph_sha256"]
        assert meta["graph_sha256"] == active_pair["graph_sha256"]
        assert meta["generation_id"] == new_gen_id

    # 5. Re-run GraphHygienePanel on the newly promoted generation -> 0 repeated findings!
    post_pkg = panel.run_panel(promoted_graph)
    assert len(post_pkg.findings) == 0, f"Expected 0 repeated findings after promotion, got: {post_pkg.findings}"


def test_hygiene_promotion_strips_hex_suffixes_and_supersedes_literal_duplicates_e2e(tmp_path: Path) -> None:
    """E2E test verifying [6-hex] literal duplicates are superseded and stripped during generation promotion."""
    runtime_root = tmp_path / "runtime_hex"
    graph_path, _, _ = _init_minimal_runtime_pair(runtime_root)
    with sqlite3.connect(str(graph_path)) as conn:
        conn.execute(
            """INSERT INTO assertion (
                assertion_id, subject_node_id, predicate, object_node_id, object_literal, scope_text,
                canonical_status, canonical_eligible, retrieval_eligible, currentness_state, validation_state
            ) VALUES
            ('asrt-hex-1', 'org_nea', 'HAS_CLAIM_TEXT', NULL, 'Apex Global Analytics enterprise AI charter [8b2bc7].', 'Apex Global Analytics enterprise AI charter [8b2bc7].', 'canonical', 1, 1, 'current', 'accepted_legacy'),
            ('asrt-hex-2', 'org_nea', 'HAS_CLAIM_TEXT', NULL, 'Apex Global Analytics enterprise AI charter [8c4e10].', 'Apex Global Analytics enterprise AI charter [8c4e10].', 'canonical', 1, 1, 'current', 'accepted_legacy')"""
        )
        conn.execute("INSERT INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, created_at) VALUES ('asrt-hex-1', 'ev-1', 'supports', '2026-10-01T00:00:00Z')")
        conn.execute("INSERT INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, created_at) VALUES ('asrt-hex-2', 'ev-1', 'supports', '2026-10-01T00:00:00Z')")
        conn.commit()

    panel = GraphHygienePanel()
    pkg = panel.run_panel(graph_path)
    pkg_path = tmp_path / "hex_approved.json"
    pkg_path.write_text(
        json.dumps(
            {
                "status": "approved",
                "strict_package_gating": True,
                "findings": [f.__dict__ for f in pkg.findings if f.proposed_fix != "keep_entities_distinct"],
                "hitl_clusters": pkg.hitl_clusters,
            }
        ),
        encoding="utf-8",
    )
    receipt = apply_hygiene_remediation(package_path=pkg_path, promote_generation=True, runtime_root=runtime_root)
    assert receipt["terminal_outcome"] == "PASS"
    assert receipt["remediation_stats"]["assertions_deduplicated"] >= 1
    promoted_graph = runtime_root / receipt["promoted_generation"]["graph_path"]
    with sqlite3.connect(f"file:{promoted_graph}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT assertion_id, scope_text FROM assertion WHERE assertion_id IN ('asrt-hex-1', 'asrt-hex-2') AND currentness_state='current'"
        ).fetchall()
        assert len(rows) == 1
        assert "[8b2bc7]" not in rows[0][1] and "[8c4e10]" not in rows[0][1]

