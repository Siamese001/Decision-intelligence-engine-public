"""
End-to-End Test for the Knowledge Graph Hygiene Agent Adversarial Panel.
"""

import sqlite3

import pytest

from dual_pipeline.graph_maintenance_core.hygiene_agent import GraphHygienePanel


@pytest.fixture
def mock_graph_state(tmp_path):
    graph_db = tmp_path / "mock_graph.db"

    with sqlite3.connect(graph_db) as conn:
        conn.execute("CREATE TABLE node (id TEXT, type TEXT)")
        conn.execute("CREATE TABLE edge (source TEXT, target TEXT)")
        conn.execute("INSERT INTO node VALUES ('node-1', 'person')")
        conn.execute("INSERT INTO node VALUES ('node-2', 'org')")

        conn.execute(
            "CREATE TABLE audit_event (id TEXT, directive_id TEXT, operator_signature TEXT)"
        )
        conn.execute("INSERT INTO audit_event VALUES ('evt-1', 'dir-1', NULL)")

        conn.execute(
            "CREATE TABLE search_document (search_document_id TEXT, retrieval_eligible INTEGER, currentness_state TEXT, content TEXT)"
        )
        conn.execute("INSERT INTO search_document VALUES ('doc-1', 0, 'superseded', 'foo')")

        for table in ["dense_vector", "sparse_weight", "colbert_token", "retrieval_record"]:
            conn.execute(f"CREATE TABLE {table} (search_document_id TEXT)")

    graph_json = tmp_path / "mock_graph.json"
    graph_json.write_text("{}")

    return graph_db


def test_hygiene_agent_e2e_run(mock_graph_state, monkeypatch):
    class MockGraphScout:
        def __init__(self, *args, **kwargs):
            pass

        def scan_orphan_and_clutter(self, conn):
            return []

        def scan_duplicate_entities(self, conn):
            return []

        def scan_temporal_contradictions(self, conn):
            return []

    monkeypatch.setattr(
        "dual_pipeline.graph_maintenance_core.hygiene_agent.GraphScout", MockGraphScout
    )

    panel = GraphHygienePanel()
    pkg = panel.run_panel(mock_graph_state)

    assert pkg.status == "proposed"
    assert pkg.model_tier == "FRONTIER_HIGH_REASONING"
    assert pkg.council_size == 13
    assert len(pkg.council_debates) >= 1
    assert len(pkg.hitl_clusters) >= 1
    assert any(f.detective == "RedTeamer" for f in pkg.findings)
    assert any("quarantine" in d["fix"] for d in pkg.remediation_directives)


def test_hygiene_agent_antipattern_discovery_and_remediation_e2e(tmp_path):
    """Verify full E2E discovery, council clustering, and governed remediation of retrieval antipatterns."""
    import json
    from tools.apply_graph_hygiene_remediation import apply_hygiene_remediation

    graph_db = tmp_path / "antipattern_e2e.sqlite"
    with sqlite3.connect(graph_db) as conn:
        conn.execute(
            "CREATE TABLE node (node_id TEXT PRIMARY KEY, node_type TEXT, canonical_name TEXT, description TEXT, status TEXT, active INTEGER DEFAULT 1, updated_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE edge (edge_id TEXT PRIMARY KEY, from_node_id TEXT, to_node_id TEXT, edge_type TEXT, description TEXT, evidence_posture TEXT, confidence TEXT, materiality TEXT, validation_priority TEXT, validation_owner TEXT, hitl_required INTEGER, source_id TEXT, superseded_by TEXT, updated_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE entity_merge_event (merge_event_id TEXT PRIMARY KEY, canonical_node_id TEXT, event_type TEXT, status TEXT, approval_reference TEXT, reason TEXT, created_at TEXT, completed_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE entity_redirect (from_node_id TEXT PRIMARY KEY, to_node_id TEXT, merge_event_id TEXT, active INTEGER DEFAULT 1, created_at TEXT)"
        )
        conn.execute(
            """CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT,
                predicate TEXT,
                legacy_predicate TEXT,
                object_node_id TEXT,
                object_literal TEXT,
                scope_text TEXT,
                valid_from TEXT,
                valid_to TEXT,
                canonical_status TEXT,
                canonical_eligible INTEGER DEFAULT 1,
                retrieval_eligible INTEGER DEFAULT 1,
                currentness_state TEXT DEFAULT 'current',
                validation_state TEXT DEFAULT 'verified',
                confidence_score REAL DEFAULT 0.92,
                superseded_by_assertion_id TEXT,
                superseded_at TEXT,
                approval_reference TEXT,
                is_quarantined INTEGER DEFAULT 0,
                namespace TEXT DEFAULT 'CANONICAL',
                qualifiers_json TEXT DEFAULT '{}',
                primary_source_id TEXT,
                created_at TEXT,
                updated_at TEXT
            )"""
        )
        conn.execute(
            "CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT, evidence_relationship TEXT DEFAULT 'supports', created_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE evidence_item (evidence_id TEXT PRIMARY KEY, direct_quote TEXT, source_id TEXT)"
        )
        conn.execute(
            "CREATE TABLE source (source_id TEXT PRIMARY KEY, source_name TEXT)"
        )
        conn.execute(
            "CREATE TABLE governance_approval (approval_reference TEXT PRIMARY KEY, status TEXT, active INTEGER DEFAULT 1)"
        )
        conn.execute("INSERT INTO governance_approval VALUES ('REQ_INGEST_CEC57955C84BCE3A', 'approved', 1)")
        conn.execute(
            """CREATE TABLE search_document (
                search_document_id TEXT PRIMARY KEY,
                object_type TEXT,
                object_id TEXT,
                title TEXT,
                body TEXT,
                canonical_status TEXT,
                currentness_state TEXT,
                retrieval_eligible INTEGER DEFAULT 1,
                source_id TEXT,
                assertion_id TEXT,
                updated_at TEXT
            )"""
        )
        conn.execute(
            """CREATE TABLE audit_event (
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
                operator_signature TEXT DEFAULT 'sig-valid'
            )"""
        )
        # Seed split-identity cross-type/prefix twins
        conn.execute("INSERT INTO node VALUES ('person_reid_schenk', 'person', 'Reid Schenk', 'Director', 'active', 1, '2026-10-01T00:00:00Z')")
        conn.execute("INSERT INTO node VALUES ('stakeholder:reid-schenk', 'stakeholder', 'Reid Schenk', 'Director', 'active', 1, '2026-10-01T00:00:00Z')")
        conn.execute("INSERT INTO node VALUES ('workstream_lens', 'workstream', 'LENS Platform', 'LENS delivery', 'active', 1, '2026-10-01T00:00:00Z')")

        conn.execute("INSERT INTO source VALUES ('src-jarvis-1', 'Project Jarvis Weekly Sync')")
        conn.execute("INSERT INTO evidence_item VALUES ('ev-j1', 'I need to create a project in Jira for Jarvis.', 'src-jarvis-1')")
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-bleed-1', 'ev-j1', 'supports', '2026-10-20T00:00:00Z')")
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-leaked-cand-1', 'ev-j1', 'supports', '2026-10-20T00:00:00Z')")
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-adv-bleed-1', 'ev-j1', 'supports', '2026-10-20T00:00:00Z')")

        # 1. Cross-project Jarvis claim attached to workstream_lens
        conn.execute(
            """INSERT INTO assertion (
                assertion_id, subject_node_id, predicate, object_literal, scope_text,
                canonical_status, canonical_eligible, retrieval_eligible, currentness_state,
                validation_state, primary_source_id, created_at
            ) VALUES (
                'asrt-bleed-1', 'workstream_lens', 'HAS_ACTION', 'Create Jira project for Project Jarvis',
                'Create Jira project for Project Jarvis', 'canonical', 1, 1, 'current', 'verified',
                'src-jarvis-1', '2026-10-20T00:00:00Z'
            )"""
        )
        # 2. Leaked candidate assertion with retrieval_eligible = 1
        conn.execute(
            """INSERT INTO assertion (
                assertion_id, subject_node_id, predicate, object_literal, scope_text,
                canonical_status, canonical_eligible, retrieval_eligible, currentness_state,
                validation_state, confidence_score, primary_source_id, created_at
            ) VALUES (
                'asrt-leaked-cand-1', 'person_reid_schenk', 'HAS_NOTE', 'Draft staging note',
                'Draft staging note', 'candidate', 0, 1, 'current', 'needs_review', 0.60,
                'src-jarvis-1', '2026-10-25T00:00:00Z'
            )"""
        )
        # 3. Unquarantined INTERNAL_ADVISORY assertion with retrieval_eligible = 1
        conn.execute(
            """INSERT INTO assertion (
                assertion_id, subject_node_id, predicate, object_literal, scope_text,
                canonical_status, canonical_eligible, retrieval_eligible, currentness_state,
                validation_state, is_quarantined, namespace, primary_source_id, created_at
            ) VALUES (
                'asrt-adv-bleed-1', 'person_reid_schenk', 'ADVISORY_NOTE', 'Internal advisory note',
                'Internal advisory note', 'canonical', 1, 1, 'current', 'verified', 1, 'INTERNAL_ADVISORY',
                'src-jarvis-1', '2026-10-25T00:00:00Z'
            )"""
        )
        # 4. Orphan search_document referencing missing assertion + existing sidecars for canonical assertions
        conn.execute(
            """INSERT INTO search_document VALUES (
                'asrt-bleed-1', 'assertion', 'asrt-bleed-1', 'Jarvis Jira', 'Create Jira project for Project Jarvis',
                'canonical', 'current', 1, 'src-jarvis-1', 'asrt-bleed-1', '2026-10-20T00:00:00Z'
            )"""
        )
        conn.execute(
            """INSERT INTO search_document VALUES (
                'asrt-adv-bleed-1', 'assertion', 'asrt-adv-bleed-1', 'Advisory Note', 'Internal advisory note',
                'canonical', 'current', 1, 'src-jarvis-1', 'asrt-adv-bleed-1', '2026-10-25T00:00:00Z'
            )"""
        )
        conn.execute(
            """INSERT INTO search_document VALUES (
                'sd-orphan-1', 'assertion', 'asrt-missing-999', 'Orphan Doc', 'Orphan Body',
                'canonical', 'current', 1, 'src-jarvis-1', 'asrt-missing-999', '2026-10-25T00:00:00Z'
            )"""
        )
        conn.commit()

    panel = GraphHygienePanel()
    pkg = panel.run_panel(graph_db)
    fixes = {f.proposed_fix for f in pkg.findings}
    assert "merge_entities" in fixes
    assert "quarantine_cross_project_assertion" in fixes
    assert "demote_leaked_candidate_assertion" in fixes
    assert "quarantine_advisory_or_orphan_sidecar" in fixes

    pkg_path = tmp_path / "hygiene_package.json"
    pkg_dict = {
        "schema": "learning-change-proposal:1.0.0",
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
    receipt_path = tmp_path / "remediation_receipt.json"

    receipt = apply_hygiene_remediation(
        graph_path=graph_db,
        package_path=pkg_path,
        receipt_path=receipt_path,
        approved_clusters=["ALL"],
    )
    assert receipt["terminal_outcome"] == "PASS"
    assert receipt["itemized_hitl_contract"]["status"] == "PASS"
    assert receipt["itemized_hitl_contract"]["coverage_ratio"] == 1.0

    # Independent read-only verification
    with sqlite3.connect(f"file:{graph_db}?mode=ro", uri=True) as ro_conn:
        bleed_elig = ro_conn.execute("SELECT retrieval_eligible, currentness_state FROM assertion WHERE assertion_id='asrt-bleed-1'").fetchone()
        assert bleed_elig == (0, "historical")
        cand_elig = ro_conn.execute("SELECT retrieval_eligible FROM assertion WHERE assertion_id='asrt-leaked-cand-1'").fetchone()
        assert cand_elig == (0,)
        adv_elig = ro_conn.execute("SELECT retrieval_eligible FROM assertion WHERE assertion_id='asrt-adv-bleed-1'").fetchone()
        assert adv_elig == (0,)
        orphan_sd = ro_conn.execute("SELECT retrieval_eligible FROM search_document WHERE search_document_id='sd-orphan-1'").fetchone()
        assert orphan_sd == (0,)

