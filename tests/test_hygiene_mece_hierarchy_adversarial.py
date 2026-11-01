"""Adversarial and edge-case tests for Graph Hygiene Agent MECE hierarchy consolidation and retrieval synthesis.

Covers mandatory edge-case categories:
1. Boundary payload (empty tables, missing optional columns, 0-node graphs)
2. Malformed input (broken JSON properties, unclosed parens, multi-comma person strings)
3. Invariant bypass attempt (fact_promotion_eligible: false on non-portfolio sources must remain blocked; distinct MECE tiers must never merge)
4. Rollback & idempotency recovery (idempotent re-execution of MECE remediation and breadcrumb synthesis)
5. Exact production regression (Contracts.AI - 2026Q4 cross-tier collapse, Grid Mod comma-merged products, source-717c5274fcba2eeca0c87fc6 misrouted status_report)
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from dual_pipeline.graph_scout_core.antipattern_scans import _is_distinct_mece_tier_pair
from dual_pipeline.graph_scout_core.hierarchy_mece_scans import (
    scan_compound_and_malformed_nodes,
    scan_hierarchy_connectivity_and_ownership_gaps,
    scan_retrieval_synthesis_breadcrumb_drift,
)
from tests.end_to_end.test_graph_hygiene_uwg_role_e2e import _bootstrap_test_graph
from tools.apply_graph_hygiene_remediation import apply_hygiene_remediation
from tools.tier2_dedup_core.mece_hierarchy_remediation import (
    remediate_mece_hierarchy_fixes,
    synthesize_portfolio_retrieval_breadcrumbs,
)


def _get_tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def test_boundary_empty_database_and_missing_tables(tmp_path: Path) -> None:
    """Category 1: Boundary payload — empty database with no tables or empty tables does not crash."""
    db_path = tmp_path / "empty.sqlite"
    conn = sqlite3.connect(str(db_path))
    assert scan_compound_and_malformed_nodes(conn) == []
    assert scan_hierarchy_connectivity_and_ownership_gaps(conn) == []
    assert scan_retrieval_synthesis_breadcrumb_drift(conn) == []
    res = remediate_mece_hierarchy_fixes(conn, [], set(), "2026-10-29T00:00:00Z", [])
    assert sum(res.values()) == 0
    assert synthesize_portfolio_retrieval_breadcrumbs(conn, set(), "2026-10-29T00:00:00Z", []) == 0
    conn.close()


def test_malformed_input_broken_json_and_unclosed_parentheses(tmp_path: Path) -> None:
    """Category 2: Malformed input — invalid properties_json and deeply malformed node names heal safely."""
    db_path = tmp_path / "malformed.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY, node_type TEXT, canonical_name TEXT,
            display_name TEXT, description TEXT, properties_json TEXT, active INTEGER DEFAULT 1, updated_at TEXT
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY, subject_node_id TEXT, predicate TEXT,
            object_node_id TEXT, scope_text TEXT, canonical_status TEXT, currentness_state TEXT
        );
        INSERT INTO node VALUES ('ws-bad-1', 'workstream', 'Substation Automation (Phase 1', 'Substation Automation (Phase 1', NULL, '{bad_json', 1, '2026-10-28');
        INSERT INTO node VALUES ('person-bad-1', 'person', 'Jose-Enriquez, Gerardo', 'Jose-Enriquez, Gerardo', NULL, 'not-a-dict', 1, '2026-10-28');
        """
    )
    findings = scan_compound_and_malformed_nodes(conn)
    subtypes = {f.details["issue_subtype"] for f in findings}
    assert "truncated_or_numbered_name" in subtypes
    assert "inverted_person_name" in subtypes

    formatted_findings = [{"proposed_fix": f.action_recommended, "raw_data": f.details} for f in findings]
    res = remediate_mece_hierarchy_fixes(conn, formatted_findings, _get_tables(conn), "2026-10-29T00:00:00Z", [])
    assert res["compound_or_mistyped_nodes_normalized"] >= 2
    ws_name = conn.execute("SELECT canonical_name FROM node WHERE node_id = 'ws-bad-1'").fetchone()[0]
    person_name = conn.execute("SELECT canonical_name FROM node WHERE node_id = 'person-bad-1'").fetchone()[0]
    assert ws_name == "Substation Automation (Phase 1)"
    assert person_name == "Gerardo Jose-Enriquez"
    conn.close()


def test_invariant_bypass_distinct_mece_tiers_never_merge_and_non_portfolio_candidates_stay_blocked(tmp_path: Path) -> None:
    """Category 3: Invariant bypass attempt — distinct MECE tiers cannot collapse, and non-portfolio fact_promotion_eligible:false stays blocked."""
    assert _is_distinct_mece_tier_pair(
        {"id": "program:asset-performance-generation", "type": "program", "name": "Asset Performance - Generation"},
        {"id": "product:asset-performance-generation", "type": "product", "name": "Asset Performance - Generation"},
    )
    assert _is_distinct_mece_tier_pair(
        {"id": "workstream:contracts-ai-2026q4", "type": "workstream", "name": "Contracts.AI - 2026Q4"},
        {"id": "tech_contracts_ai", "type": "technical_anchor", "name": "Contracts.AI"},
    )

    db_path = tmp_path / "bypass.sqlite"
    _bootstrap_test_graph(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        ALTER TABLE assertion ADD COLUMN qualifiers_json TEXT DEFAULT '{}';
        CREATE TABLE IF NOT EXISTS source (source_id TEXT PRIMARY KEY, source_type TEXT, source_category TEXT, title TEXT, authority_rank INTEGER, source_posture TEXT);
        INSERT INTO source VALUES ('src-chat-noise', 'meeting', 'chat', 'Random Chat', 3, 'working_notes');
        INSERT INTO node (node_id, node_type, canonical_name, display_name, active) VALUES ('node-a', 'system', 'Node A', 'Node A', 1);
        INSERT INTO node (node_id, node_type, canonical_name, display_name, active) VALUES ('node-b', 'system', 'Node B', 'Node B', 1);
        INSERT INTO assertion (
            assertion_id, subject_node_id, predicate, object_node_id,
            scope_text, qualifiers_json, confidence_score, validation_state,
            canonical_status, canonical_eligible, retrieval_eligible,
            currentness_state, primary_source_id, updated_at
        ) VALUES (
            'asrt-blocked-1', 'node-a', 'RELATES_TO', 'node-b', 'Speculative note',
            '{"fact_promotion_eligible": false}', 0.99, 'accepted_legacy',
            'candidate', 0, 0, 'current', 'src-chat-noise', '2026-10-28'
        );
        INSERT INTO evidence_item (evidence_id, source_id, direct_quote) VALUES ('ev-noise', 'src-chat-noise', 'Noise quote');
        INSERT INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship) VALUES ('asrt-blocked-1', 'ev-noise', 'supports');
        """
    )
    conn.commit()
    conn.close()

    pkg_path = tmp_path / "pkg.json"
    pkg_path.write_text(
        json.dumps(
            {
                "findings": [
                    {
                        "proposed_fix": "promote_verified_candidates",
                        "entity_references": ["asrt-blocked-1"],
                        "raw_data": {"promotable_ids": ["asrt-blocked-1"]},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    apply_hygiene_remediation(graph_path=db_path, package_path=pkg_path, dry_run=False)

    conn = sqlite3.connect(str(db_path))
    status = conn.execute("SELECT canonical_status FROM assertion WHERE assertion_id = 'asrt-blocked-1'").fetchone()[0]
    assert status == "candidate"
    conn.close()


def test_idempotent_reruns_produce_zero_duplicate_edges_or_breadcrumbs(tmp_path: Path) -> None:
    """Category 4 & 5: Rollback/idempotency & exact production regression."""
    db_path = tmp_path / "idempotent.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY, node_type TEXT, canonical_name TEXT,
            display_name TEXT, description TEXT, properties_json TEXT, active INTEGER DEFAULT 1, updated_at TEXT
        );
        CREATE TABLE alias (alias_id TEXT PRIMARY KEY, node_id TEXT, alias TEXT, normalized_alias TEXT, source_id TEXT);
        CREATE TABLE entity_redirect (from_node_id TEXT PRIMARY KEY, to_node_id TEXT, redirect_reason TEXT, active INTEGER DEFAULT 1, created_at TEXT);
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY, assertion_type TEXT, subject_node_id TEXT, predicate TEXT,
            object_node_id TEXT, object_literal TEXT, scope_text TEXT, qualifiers_json TEXT,
            confidence_score REAL, validation_state TEXT, canonical_status TEXT,
            canonical_eligible INTEGER, retrieval_eligible INTEGER, currentness_state TEXT,
            superseded_by_assertion_id TEXT, primary_source_id TEXT, approval_reference TEXT,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE search_document (
            search_document_id TEXT PRIMARY KEY, object_type TEXT, object_id TEXT,
            title TEXT, body TEXT, canonical_status TEXT, currentness_state TEXT,
            retrieval_eligible INTEGER, source_id TEXT, assertion_id TEXT, updated_at TEXT
        );
        INSERT INTO node VALUES ('program:grid-mod', 'program', 'Grid Modernization', 'Grid Modernization', 'Program', '{}', 1, '2026-10-28');
        INSERT INTO node VALUES ('product:compound-1', 'product', 'Distribution Automation, Fault Location, & Self-Healing', 'Distribution Automation, Fault Location, & Self-Healing', 'PM: Jane Smith', '{}', 1, '2026-10-28');
        INSERT INTO assertion VALUES ('a-1', 'PART_OF', 'product:compound-1', 'PART_OF', 'program:grid-mod', NULL, 'Part of Grid Mod', '{}', 0.95, 'verified', 'canonical', 1, 1, 'current', NULL, 'src-1', 'HITL-1', '2026-10-28', '2026-10-28');
        """
    )
    findings = scan_compound_and_malformed_nodes(conn) + scan_hierarchy_connectivity_and_ownership_gaps(conn)
    formatted = [{"proposed_fix": f.action_recommended, "raw_data": f.details} for f in findings]
    tables = _get_tables(conn)
    res1 = remediate_mece_hierarchy_fixes(conn, formatted, tables, "2026-10-29T00:00:00Z", [])
    assert res1["compound_or_mistyped_nodes_normalized"] >= 1
    assert res1["hierarchical_retrieval_docs_synthesized"] >= 1

    count_nodes_1 = conn.execute("SELECT COUNT(*) FROM node WHERE active = 1").fetchone()[0]
    count_asrt_1 = conn.execute("SELECT COUNT(*) FROM assertion WHERE canonical_status = 'canonical'").fetchone()[0]

    # Re-run both remediation and synthesis; must be 100% idempotent
    findings_after = scan_compound_and_malformed_nodes(conn) + scan_hierarchy_connectivity_and_ownership_gaps(conn)
    formatted_after = [{"proposed_fix": f.action_recommended, "raw_data": f.details} for f in findings_after]
    remediate_mece_hierarchy_fixes(conn, formatted_after, tables, "2026-10-29T00:05:00Z", [])
    second_synth = synthesize_portfolio_retrieval_breadcrumbs(conn, tables, "2026-10-29T00:05:00Z", [])
    assert second_synth == 0

    count_nodes_2 = conn.execute("SELECT COUNT(*) FROM node WHERE active = 1").fetchone()[0]
    count_asrt_2 = conn.execute("SELECT COUNT(*) FROM assertion WHERE canonical_status = 'canonical'").fetchone()[0]
    assert count_nodes_1 == count_nodes_2
    assert count_asrt_1 == count_asrt_2
    conn.close()


def test_production_sqlite_trigger_and_fk_restrict_edge_cases(tmp_path: Path) -> None:
    """Category 5 & 6: Production SQLite triggers (candidate-first promotion, node_type_registry, ON DELETE RESTRICT FK)."""
    db_path = tmp_path / "prod_triggers.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE node_type_registry (node_type_id TEXT PRIMARY KEY, registry_state TEXT);
        INSERT INTO node_type_registry VALUES ('program', 'active'), ('product', 'active'), ('workstream', 'active'), ('stakeholder', 'active'), ('org', 'active');
        CREATE TABLE assertion_predicate_registry (predicate TEXT PRIMARY KEY, active INTEGER);
        INSERT INTO assertion_predicate_registry VALUES ('DELIVERS', 1), ('AFFILIATED_WITH', 1), ('OWNS_OR_LEADS', 1), ('HAS_CLAIM_TEXT', 1);
        CREATE TABLE governance_approval (approval_id TEXT PRIMARY KEY, approval_reference TEXT UNIQUE, status TEXT, active INTEGER, scope_json TEXT);
        CREATE TABLE source (source_id TEXT PRIMARY KEY);
        INSERT INTO source VALUES ('source_canonical_graph');
        CREATE TABLE evidence_item (evidence_id TEXT PRIMARY KEY, source_id TEXT);
        INSERT INTO evidence_item VALUES ('ev-1', 'source_canonical_graph');
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY, node_type TEXT NOT NULL, canonical_name TEXT NOT NULL,
            display_name TEXT, description TEXT, status TEXT, active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TRIGGER node_type_guard_bi BEFORE INSERT ON node
        WHEN NOT EXISTS (SELECT 1 FROM node_type_registry r WHERE r.node_type_id = new.node_type AND r.registry_state = 'active')
        BEGIN SELECT RAISE(ABORT, 'unregistered active node type'); END;
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY, assertion_fingerprint TEXT NOT NULL UNIQUE CHECK (length(assertion_fingerprint) = 64),
            subject_node_id TEXT NOT NULL REFERENCES node(node_id) ON DELETE RESTRICT,
            predicate TEXT NOT NULL, object_node_id TEXT REFERENCES node(node_id) ON DELETE RESTRICT,
            object_literal TEXT, object_literal_type TEXT, assertion_type TEXT NOT NULL,
            atomicity_state TEXT NOT NULL, scope_text TEXT NOT NULL, primary_source_id TEXT REFERENCES source(source_id),
            origin_type TEXT NOT NULL, confidence_label TEXT NOT NULL, confidence_score REAL NOT NULL,
            validation_state TEXT NOT NULL, currentness_state TEXT NOT NULL, resolution_state TEXT NOT NULL,
            canonical_status TEXT NOT NULL, canonical_eligible INTEGER NOT NULL, retrieval_eligible INTEGER NOT NULL,
            materiality TEXT NOT NULL, classification TEXT NOT NULL, allowed_audiences_json TEXT NOT NULL,
            hitl_required INTEGER NOT NULL, legacy_record_type TEXT NOT NULL, legacy_record_ids_json TEXT NOT NULL,
            approval_reference TEXT REFERENCES governance_approval(approval_reference),
            qualifiers_json TEXT NOT NULL, valid_from TEXT, date_precision TEXT NOT NULL, date_status TEXT NOT NULL,
            recorded_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE assertion_evidence (
            assertion_id TEXT REFERENCES assertion(assertion_id) ON DELETE CASCADE,
            evidence_id TEXT REFERENCES evidence_item(evidence_id) ON DELETE RESTRICT,
            evidence_relationship TEXT, created_at TEXT, PRIMARY KEY (assertion_id, evidence_id)
        );
        CREATE TRIGGER assertion_no_direct_canonical_bi BEFORE INSERT ON assertion
        WHEN new.canonical_status = 'canonical'
        BEGIN SELECT RAISE(ABORT, 'canonical assertions must be inserted as candidates and explicitly promoted'); END;
        INSERT INTO node VALUES ('prog-1', 'program', 'Gen Program', 'Gen Program', 'Desc', 'canonical', 1, '2026-10-29T00:00:00Z', '2026-10-29T00:00:00Z');
        INSERT INTO node VALUES ('prod-comp', 'product', 'Prod Alpha, Prod Beta', 'Prod Alpha, Prod Beta', 'PM: Alice Carter', 'canonical', 1, '2026-10-29T00:00:00Z', '2026-10-29T00:00:00Z');
        """
    )
    findings = scan_compound_and_malformed_nodes(conn)
    formatted = [{"proposed_fix": f.action_recommended, "raw_data": f.details} for f in findings]
    formatted.append(
        {
            "proposed_fix": "reconcile_hierarchy_and_ownership_edges",
            "raw_data": {"node_id": "prod-comp", "object_node_id": "prod-comp", "owner_names": ["Alice Carter"], "predicate": "OWNS_OR_LEADS"},
        }
    )
    # Also test PART_OF fallback to AFFILIATED_WITH and ON CONFLICT upsert when FK RESTRICT is active
    formatted.append(
        {
            "proposed_fix": "reconcile_hierarchy_and_ownership_edges",
            "raw_data": {"subject_node_id": "prod-comp", "object_node_id": "prog-1", "predicate": "PART_OF"},
        }
    )
    res = remediate_mece_hierarchy_fixes(conn, formatted, _get_tables(conn), "2026-10-29T07:20:00.123456+00:00", [])
    assert res["compound_or_mistyped_nodes_normalized"] == 1
    assert res["hierarchy_and_ownership_items_reconciled"] == 2
    # Verify PART_OF fell back to AFFILIATED_WITH and promoted to canonical cleanly
    aff_cnt = conn.execute(
        "SELECT COUNT(*) FROM assertion WHERE predicate = 'AFFILIATED_WITH' AND canonical_status = 'canonical'"
    ).fetchone()[0]
    assert aff_cnt >= 1
    conn.close()


def test_tabular_row_extractor_adversarial_edge_cases() -> None:
    """Category 1 & 2: Tabular charter extractor handles empty tables, null/TBD owners, and malformed cells safely."""
    from dual_pipeline.document_pipeline_core.tabular_row_extractor import (
        extract_tabular_workbook_and_table_projection,
    )

    assert extract_tabular_workbook_and_table_projection("")["graph_mutations"]["entity_upserts"] == []
    md_edge = (
        "| Workstream | PM | Owner |\n"
        "| --- | --- | --- |\n"
        "| Beacon | Unassigned | TBD |\n"
        "| Process Atlas | null | N/A |\n"
        "| Security Portal | Chris Nunalee | |\n"
    )
    proj = extract_tabular_workbook_and_table_projection(md_edge, {"title": "Edge Case Charter"})
    upserts = proj["graph_mutations"]["entity_upserts"]
    signals = proj["graph_mutations"]["relationship_signals"]
    names = {u["name"]: u["type"] for u in upserts}
    assert names.get("Beacon") == "workstream"
    assert "Unassigned" not in names and "TBD" not in names and "null" not in names
    assert names.get("Chris Nunalee") == "person"
    own_sigs = [s for s in signals if s["predicate"] == "OWNS_OR_LEADS"]
    assert len(own_sigs) == 1
