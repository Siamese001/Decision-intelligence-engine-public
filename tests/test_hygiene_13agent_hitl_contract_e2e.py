"""End-to-end tests for the 13-Agent Graph Hygiene Council and 100% Itemized HITL Mutation Contract."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from dual_pipeline.core import sha256_file
from dual_pipeline.graph_maintenance_core.hygiene_agent import GraphHygienePanel
from dual_pipeline.index_core.records import render_retrieval_records
from dual_pipeline.label_quality_core.constants import APPROVED_VALIDATION_STATES
from tools.apply_graph_hygiene_remediation import apply_hygiene_remediation


def _seed_13agent_runtime_pair(runtime_root: Path) -> tuple[Path, Path, str]:
    gen_id = "generation-init13agent00000000000001"
    gen_dir = runtime_root / "generations" / gen_id
    gen_dir.mkdir(parents=True, exist_ok=True)
    graph_path = gen_dir / "graph.sqlite"
    sidecar_path = gen_dir / "sidecar.sqlite"

    conn = sqlite3.connect(str(graph_path))
    conn.executescript(
        """
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
            validation_state TEXT,
            currentness_state TEXT,
            approval_state TEXT,
            rag_state TEXT,
            resolution_state TEXT,
            retrieval_eligibility TEXT,
            publication_state TEXT,
            review_state TEXT,
            intentional_orphan INTEGER DEFAULT 0,
            intentional_orphan_reason TEXT,
            updated_at TEXT
        );
        CREATE VIRTUAL TABLE node_fts USING fts5(node_id UNINDEXED, canonical_name, description, aliases, node_type UNINDEXED);
        CREATE TABLE alias (
            alias_id TEXT PRIMARY KEY,
            node_id TEXT,
            alias_name TEXT,
            normalized_alias TEXT,
            alias_type TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT
        );
        CREATE TABLE canonical_alias (
            alias_id TEXT PRIMARY KEY,
            entity_id TEXT,
            alias_key TEXT,
            display_alias TEXT,
            alias_type TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT
        );
        CREATE TABLE edge (
            edge_id TEXT PRIMARY KEY,
            from_node_id TEXT,
            edge_type TEXT,
            to_node_id TEXT,
            description TEXT,
            evidence_posture TEXT DEFAULT 'direct',
            confidence REAL DEFAULT 0.9,
            materiality TEXT DEFAULT 'high',
            validation_priority TEXT DEFAULT 'P1',
            validation_owner TEXT DEFAULT 'operator',
            hitl_required INTEGER DEFAULT 0,
            source_id TEXT DEFAULT 'src-1',
            superseded_by TEXT,
            updated_at TEXT
        );
        CREATE TABLE legacy_edge_assertion_map (
            edge_id TEXT PRIMARY KEY,
            assertion_id TEXT,
            migration_confidence REAL,
            migration_state TEXT,
            created_at TEXT
        );
        CREATE TABLE validation_queue (
            queue_id TEXT PRIMARY KEY,
            edge_id TEXT,
            node_id TEXT,
            status TEXT
        );
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY,
            source_type TEXT DEFAULT 'meeting_transcript',
            source_name TEXT,
            source_date TEXT DEFAULT '2026-10-20',
            authority_rank INTEGER DEFAULT 1,
            source_posture TEXT DEFAULT 'authoritative_internal',
            cold_archive_ref TEXT,
            notes TEXT,
            created_at TEXT
        );
        CREATE TABLE source_version (
            source_version_id TEXT PRIMARY KEY,
            source_id TEXT,
            version_number INTEGER DEFAULT 1,
            source_date_normalized TEXT DEFAULT '2026-10-20',
            created_at TEXT
        );
        CREATE TABLE extraction_run (
            extraction_run_id TEXT PRIMARY KEY,
            algorithm_version TEXT DEFAULT '1.0',
            baseline_graph_sha256 TEXT DEFAULT '0',
            input_manifest_sha256 TEXT DEFAULT '0',
            started_at TEXT,
            completed_at TEXT,
            status TEXT DEFAULT 'completed',
            deterministic INTEGER DEFAULT 1
        );
        CREATE TABLE evidence_item (
            evidence_id TEXT PRIMARY KEY,
            source_id TEXT,
            source_version_id TEXT,
            direct_quote TEXT,
            evidence_directness TEXT DEFAULT 'direct',
            source_authority_rank INTEGER DEFAULT 1,
            extraction_confidence REAL DEFAULT 0.95,
            created_at TEXT
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            legacy_predicate TEXT,
            object_node_id TEXT,
            object_literal TEXT,
            scope_text TEXT,
            canonical_status TEXT,
            canonical_eligible INTEGER DEFAULT 1,
            retrieval_eligible INTEGER DEFAULT 1,
            currentness_state TEXT DEFAULT 'current',
            validation_state TEXT DEFAULT 'accepted_legacy',
            confidence_score REAL DEFAULT 0.92,
            confidence_label TEXT DEFAULT 'high',
            assertion_type TEXT DEFAULT 'confirmed_canonical_fact',
            atomicity_state TEXT DEFAULT 'atomic',
            extraction_method TEXT DEFAULT 'llm_structured',
            origin_type TEXT DEFAULT 'legacy_migration',
            materiality TEXT DEFAULT 'high',
            classification TEXT DEFAULT 'internal',
            allowed_audiences_json TEXT DEFAULT '["internal_consulting"]',
            primary_source_id TEXT DEFAULT 'src-1',
            extraction_run_id TEXT DEFAULT 'er-1',
            approval_reference TEXT DEFAULT 'REQ_INGEST_CEC57955C84BCE3A',
            superseded_by_assertion_id TEXT,
            superseded_at TEXT,
            valid_from TEXT DEFAULT '2026-10-20',
            valid_to TEXT,
            recorded_at TEXT DEFAULT '2026-10-20T00:00:00Z',
            created_at TEXT DEFAULT '2026-06-01T00:00:00Z',
            updated_at TEXT DEFAULT '2026-10-20T00:00:00Z',
            qualifiers_json TEXT DEFAULT '{}'
        );
        CREATE TABLE assertion_evidence (
            assertion_id TEXT,
            evidence_id TEXT,
            evidence_relationship TEXT DEFAULT 'supports',
            independent_corroboration INTEGER DEFAULT 1,
            created_at TEXT,
            PRIMARY KEY (assertion_id, evidence_id)
        );
        CREATE TABLE governance_approval (
            approval_id TEXT PRIMARY KEY,
            approval_reference TEXT,
            status TEXT DEFAULT 'approved',
            active INTEGER DEFAULT 1
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
        CREATE VIRTUAL TABLE search_fts USING fts5(title, body, object_type UNINDEXED, object_id UNINDEXED);
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
            active INTEGER DEFAULT 1,
            created_at TEXT
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
            active INTEGER DEFAULT 1,
            created_at TEXT
        );
        CREATE TABLE entity_resolution_candidate (
            candidate_id TEXT PRIMARY KEY,
            candidate_type TEXT,
            normalized_key TEXT,
            node_type_scope TEXT,
            status TEXT,
            auto_merge_allowed INTEGER DEFAULT 0,
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
            release_version TEXT,
            operator_signature TEXT DEFAULT 'sig-ok'
        );
        """
    )

    conn.execute(
        "INSERT INTO governance_approval VALUES ('ga-1', 'REQ_INGEST_CEC57955C84BCE3A', 'approved', 1)"
    )
    conn.execute(
        "INSERT INTO source (source_id, source_name, source_date) VALUES ('src-1', 'Architecture Review 10-20-2026', '2026-10-20')"
    )
    conn.execute(
        "INSERT INTO source_version (source_version_id, source_id, version_number, source_date_normalized) VALUES ('sv-1', 'src-1', 1, '2026-10-20')"
    )
    conn.execute("INSERT INTO extraction_run (extraction_run_id, status) VALUES ('er-1', 'completed')")
    conn.execute(
        "INSERT INTO evidence_item (evidence_id, source_id, source_version_id, direct_quote) VALUES ('ev-1', 'src-1', 'sv-1', 'Tony Morano serves as VP of Engineering.')"
    )

    # Nodes:
    # 1. Duplicate pair where ASCII order ('entity_tony_morano' < 'person_tony_morano') is opposite to pick_best_survivor ('person_tony_morano' wins)
    conn.executemany(
        "INSERT INTO node (node_id, node_type, canonical_name, display_name, normalized_name, description, status, active) VALUES (?, ?, ?, ?, ?, ?, 'canonical', 1)",
        [
            ("entity_tony_morano", "stakeholder", "Tony Morano", "Tony Morano", "tony morano", "VP of Engineering"),
            ("person_tony_morano", "stakeholder", "Tony M. Morano", "Tony M. Morano", "tony m morano", "VP of Engineering"),
            ("org_nee", "org", "Apex Global Enterprises", "Apex Global Enterprises", "apex_global energy", "Enterprise utility"),
            ("role_vp_eng", "role_group", "VP / Engineering", "VP / Engineering", "vp engineering", "Leadership role"),
            ("role_generic_lead", "role_group", "Technical lead", "Technical lead", "technical lead", "Generic role"),
            ("role_atlas_lead", "role_group", "Atlas Technical Lead", "Atlas Technical Lead", "atlas technical lead", "Project role"),
            ("node_orphan_clutter", "entity", "Orphan Clutter Entity", "Orphan Clutter Entity", "orphan clutter entity", "Isolated node"),
        ],
    )
    conn.execute(
        "INSERT INTO node_state (node_id, lifecycle_state, currentness_state, retrieval_eligibility, intentional_orphan) VALUES ('node_orphan_clutter', 'active', 'current', 'eligible', 0)"
    )

    # Assertions:
    # - Active verified HITL role assertion on person_tony_morano
    # - Active canonical assertion on entity_tony_morano (to be repointed to person_tony_morano)
    # - Stale candidate assertion superseded by canonical assertion
    conn.executemany(
        """INSERT INTO assertion (
            assertion_id, subject_node_id, predicate, object_node_id, scope_text,
            canonical_status, canonical_eligible, retrieval_eligible, currentness_state,
            validation_state, confidence_score, primary_source_id, extraction_run_id,
            approval_reference, superseded_by_assertion_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'src-1', 'er-1', 'REQ_INGEST_CEC57955C84BCE3A', ?)""",
        [
            (
                "asrt-hitl-tony-role",
                "person_tony_morano",
                "HAS_ROLE_IN",
                "role_vp_eng",
                "Anthony (Tony) Morano HAS_ROLE VP / Engineering.",
                "canonical",
                1,
                1,
                "current",
                "verified",
                1.0,
                None,
            ),
            (
                "asrt-canon-tony-org",
                "person_tony_morano",
                "WORKS_AT",
                "org_nee",
                "Anthony (Tony) Morano works at Apex Global Enterprises.",
                "canonical",
                1,
                1,
                "current",
                "accepted_legacy",
                0.95,
                None,
            ),
            (
                "asrt-retired-tony-claim",
                "entity_tony_morano",
                "LEADS_INITIATIVE",
                "org_nee",
                "Tony Morano leads cloud architecture at Apex Global Enterprises.",
                "canonical",
                1,
                1,
                "current",
                "accepted_legacy",
                0.91,
                None,
            ),
            (
                "asrt-stale-cand-1",
                "person_tony_morano",
                "WORKS_AT",
                "org_nee",
                "Draft: Tony works at Apex Global.",
                "candidate",
                0,
                0,
                "current",
                "unreviewed",
                0.55,
                "asrt-canon-tony-org",
            ),
            (
                "asrt-role-generic",
                "person_tony_morano",
                "ASSOCIATED_WITH",
                "role_generic_lead",
                "Generic lead ref.",
                "canonical",
                1,
                1,
                "current",
                "accepted_legacy",
                0.90,
                None,
            ),
            (
                "asrt-role-atlas",
                "person_tony_morano",
                "ASSOCIATED_WITH",
                "role_atlas_lead",
                "Atlas lead ref.",
                "canonical",
                1,
                1,
                "current",
                "accepted_legacy",
                0.90,
                None,
            ),
        ],
    )
    for aid in (
        "asrt-hitl-tony-role",
        "asrt-canon-tony-org",
        "asrt-retired-tony-claim",
        "asrt-role-generic",
        "asrt-role-atlas",
    ):
        conn.execute(
            "INSERT INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, independent_corroboration) VALUES (?, 'ev-1', 'supports', 1)",
            (aid,),
        )

    # Search document for asrt-retired-tony-claim initially has old name 'Tony Morano'
    conn.execute(
        """INSERT INTO search_document (
            search_document_id, object_type, object_id, title, body,
            canonical_status, currentness_state, retrieval_eligible, source_id, assertion_id
        ) VALUES (
            'asrt-retired-tony-claim', 'assertion', 'asrt-retired-tony-claim',
            'entity_tony_morano LEADS_INITIATIVE org_nee',
            'entity_tony_morano LEADS_INITIATIVE org_nee. Tony Morano leads cloud architecture at Apex Global Enterprises.',
            'canonical', 'current', 1, 'src-1', 'asrt-retired-tony-claim'
        )"""
    )

    # Auto-linkable edge
    conn.execute(
        "INSERT INTO edge (edge_id, from_node_id, edge_type, to_node_id, description, source_id) VALUES ('edge-autolink-1', 'person_tony_morano', 'WORKS_AT', 'org_nee', 'Works at NEE', 'src-1')"
    )
    conn.commit()
    conn.close()

    # Sidecar DB
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
        [("generation_id", gen_id), ("graph_sha256", g_sha), ("bound_graph_sha256", g_sha)],
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
        "activated_at": "2026-10-27T00:00:00Z",
    }
    (runtime_root / "active_pair.json").write_text(json.dumps(active_pair, indent=2), encoding="utf-8")
    return graph_path, sidecar_path, gen_id


def test_13_agent_council_and_100_percent_itemized_hitl_contract_e2e(tmp_path: Path) -> None:
    """Proves 13-Agent Council pre-computes exact survivor order and verifies 100% of promised HITL mutations."""
    runtime_root = tmp_path / "runtime"
    graph_path, _, _ = _seed_13agent_runtime_pair(runtime_root)

    # 1. Verify 'verified' is in APPROVED_VALIDATION_STATES
    assert "verified" in APPROVED_VALIDATION_STATES

    # 2. Run 13-Agent Council discovery
    panel = GraphHygienePanel()
    pkg = panel.run_panel(graph_path)
    assert pkg.council_size == 13

    # 3. Verify merge_entities finding pre-computes survivor_node_id == person_tony_morano (node_id_a)
    stakeholder_merges = [
        f for f in pkg.findings
        if f.proposed_fix == "merge_entities" and set(f.entity_references) == {"entity_tony_morano", "person_tony_morano"}
    ]
    assert len(stakeholder_merges) == 1
    mf = stakeholder_merges[0]
    assert mf.raw_data["survivor_node_id"] == "person_tony_morano"
    assert mf.raw_data["retired_node_id"] == "entity_tony_morano"
    assert mf.raw_data["node_id_a"] == "person_tony_morano"
    assert mf.raw_data["node_id_b"] == "entity_tony_morano"
    assert "Merge 'Tony Morano' into 'Tony M. Morano'" in mf.raw_data["recommended_option"]

    # 4. Verify every HITLDecisionCluster embeds explicit promised_mutations
    for cluster in pkg.hitl_clusters:
        assert "promised_mutations" in cluster
        assert len(cluster["promised_mutations"]) >= 1

    # 5. Execute remediation with CLUSTER-02D2-ROLE-PROJECT-SCOPE excluded and all other clusters approved
    pkg_path = tmp_path / "hygiene_package.json"
    pkg_dict = {
        "schema": "learning-change-proposal:1.0.0",
        "status": pkg.status,
        "council_size": pkg.council_size,
        "strict_package_gating": True,
        "findings": [
            {
                "detective": f.detective,
                "severity": f.severity,
                "description": f.description,
                "entity_references": f.entity_references,
                "proposed_fix": f.proposed_fix,
                "raw_data": f.raw_data,
            }
            for f in pkg.findings
        ],
        "hitl_clusters": pkg.hitl_clusters,
    }
    pkg_path.write_text(json.dumps(pkg_dict, indent=2), encoding="utf-8")

    approved_cids = [
        c["cluster_id"] for c in pkg.hitl_clusters if c["cluster_id"] != "CLUSTER-02D2-ROLE-PROJECT-SCOPE"
    ]
    receipt_path = tmp_path / "run-10-27-2026" / "receipt.json"
    receipt = apply_hygiene_remediation(
        package_path=pkg_path,
        receipt_path=receipt_path,
        promote_generation=True,
        runtime_root=runtime_root,
        approved_clusters=approved_cids,
        excluded_clusters=["CLUSTER-02D2-ROLE-PROJECT-SCOPE"],
    )

    assert receipt["terminal_outcome"] == "PASS"
    assert "itemized_hitl_contract" in receipt
    contract_res = receipt["itemized_hitl_contract"]
    assert contract_res["status"] == "PASS"
    assert contract_res["failed_count"] == 0
    assert contract_res["coverage_ratio"] == 1.0
    assert contract_res["total_promised"] >= 5

    # 6. Independently read back promoted graph state
    active_pair = json.loads((runtime_root / "active_pair.json").read_text(encoding="utf-8"))
    promoted_graph = runtime_root / active_pair["graph_path"]
    with sqlite3.connect(f"file:{promoted_graph}?mode=ro", uri=True) as ro_conn:
        # Retired node is merged into survivor
        surv_active = ro_conn.execute("SELECT active FROM node WHERE node_id='person_tony_morano'").fetchone()[0]
        ret_active, ret_status = ro_conn.execute("SELECT active, status FROM node WHERE node_id='entity_tony_morano'").fetchone()
        assert surv_active == 1
        assert ret_active == 0 and ret_status == "merged"

        # Retired node's canonical_name ('Tony Morano') is preserved as active alias on survivor
        alias_rows = ro_conn.execute(
            "SELECT alias_name FROM alias WHERE node_id='person_tony_morano' AND active=1"
        ).fetchall()
        assert any(r[0] == "Tony Morano" for r in alias_rows)

        # node_fts populates 'Tony Morano' in aliases for active survivor person_tony_morano and leaves retired aliases empty
        fts_retired = ro_conn.execute("SELECT aliases FROM node_fts WHERE node_id='entity_tony_morano'").fetchone()
        assert fts_retired is None or fts_retired[0] == ""
        fts_surv = ro_conn.execute("SELECT aliases FROM node_fts WHERE node_id='person_tony_morano'").fetchone()
        assert fts_surv is not None and "Tony Morano" in fts_surv[0]

    # 7. Verify 'verified' role assertion is rendered into 'approved' retrieval lane
    recs = render_retrieval_records(promoted_graph, active_pair["graph_snapshot"])
    hitl_recs = [
        r for r in recs
        if r.get("extensions", {}).get("com.partner_advisory.nee.graph-object-id") == "asrt-hitl-tony-role"
    ]
    assert len(hitl_recs) == 1
    assert hitl_recs[0]["lane"] == "approved"
