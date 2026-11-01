"""End-to-end verification of Graph Hygiene Agent MECE hierarchy consolidation and retrieval synthesis."""

from __future__ import annotations

from dataclasses import asdict
import json
import sqlite3
from pathlib import Path

from dual_pipeline.graph_maintenance_core.hygiene_agent import GraphHygienePanel
from dual_pipeline.graph_maintenance_core.hygiene_mutation_agents import compile_promised_mutations
from dual_pipeline.document_pipeline_core.document_signal_router import evaluate_document_signal_routing, DocumentArchetype
from dual_pipeline.transcript_qa_core.graph_ontology_entities import _resolve_canonical_predicate
from dual_pipeline.transcript_stages_core.graph_post_risk_healer import prune_and_budget_signals
from dual_pipeline.canonical_pipeline.publication_verifier import _inspect_node_in_graph
from tests.end_to_end.test_graph_hygiene_uwg_role_e2e import _bootstrap_test_graph
from tools.apply_graph_hygiene_remediation import apply_hygiene_remediation


def _seed_non_mece_portfolio_db(db_path: Path) -> None:
    _bootstrap_test_graph(db_path)
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.executescript(
        """
        ALTER TABLE assertion ADD COLUMN assertion_type TEXT DEFAULT 'FACT';
        ALTER TABLE assertion ADD COLUMN qualifiers_json TEXT DEFAULT '{}';
        CREATE TABLE IF NOT EXISTS source (
            source_id TEXT PRIMARY KEY,
            source_type TEXT,
            source_category TEXT,
            title TEXT,
            source_name TEXT,
            authority_rank INTEGER DEFAULT 5,
            source_posture TEXT,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS governance_approval (
            approval_id TEXT PRIMARY KEY,
            approval_reference TEXT,
            status TEXT DEFAULT 'approved',
            active INTEGER DEFAULT 1
        );
        """
    )
    cur.execute("INSERT OR IGNORE INTO governance_approval (approval_id, approval_reference, status, active) VALUES ('ga-seed', 'HITL-SEED', 'approved', 1)")
    cur.execute(
        "INSERT INTO source VALUES ('source-717c5274fcba2eeca0c87fc6', 'document', 'status_report', "
        "'NEA Workstreams Headcount (2026Q4 Portfolio)', '20261028_nea_workstreams_headcount.md', 6, 'status_update', 1)"
    )
    # 1. Comma-merged product node
    cur.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, display_name, description, properties_json, active, created_at, updated_at) "
        "VALUES ('product:grid-mod-scada-adms-ederms', 'product', "
        "'Grid Mod - SCADA, ADMS, & eDERMS', 'Grid Mod - SCADA, ADMS, & eDERMS', "
        "'Compound product row from program planning table', '{}', 1, '2026-10-28', '2026-10-28')"
    )
    # 2. Truncated parenthetical node
    cur.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, display_name, description, properties_json, active, created_at, updated_at) "
        "VALUES ('workstream_asset_ops_adms', 'workstream', "
        "'Asset Operations (ADMS', 'Asset Operations (ADMS', 'Truncated workstream name', '{}', 1, '2026-10-28', '2026-10-28')"
    )
    # 3. Comma-inverted stakeholder node
    cur.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, display_name, description, properties_json, active, created_at, updated_at) "
        "VALUES ('stakeholder_smith_jane', 'stakeholder', "
        "'Smith, Jane', 'Smith, Jane', 'Inverted person name', '{}', 1, '2026-10-28', '2026-10-28')"
    )
    # 4. Mis-typed LENS Part 1 as program
    cur.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, display_name, description, properties_json, active, created_at, updated_at) "
        "VALUES ('program_lens_part_1', 'program', "
        "'LENS Part 1', 'LENS Part 1', 'Phase 1 of LENS', '{}', 1, '2026-10-28', '2026-10-28')"
    )
    # 5. Cross-tier destructive redirect: Contracts.AI - 2026Q4 merged into technical_anchor
    cur.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, display_name, description, properties_json, status, active, created_at, updated_at) "
        "VALUES ('workstream:contracts-ai-2026q4', 'workstream', "
        "'Contracts.AI - 2026Q4', 'Contracts.AI - 2026Q4', '2026Q4 workstream', '{}', 'merged', 0, '2026-10-28', '2026-10-28')"
    )
    cur.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, display_name, description, properties_json, active, created_at, updated_at) "
        "VALUES ('tech_contracts_ai', 'technical_anchor', "
        "'Contracts.AI', 'Contracts.AI', 'Technical anchor', '{}', 1, '2026-10-28', '2026-10-28')"
    )
    cur.execute(
        "INSERT INTO alias (alias_id, node_id, alias_name, normalized_alias, alias_type, active, created_at) "
        "VALUES ('al-1', 'tech_contracts_ai', 'Contracts.AI - 2026Q4', 'contracts.ai - 2026q4', 'redirect', 1, '2026-10-28')"
    )
    cur.execute(
        "INSERT INTO entity_redirect (from_node_id, to_node_id, merge_event_id, active, created_at) "
        "VALUES ('workstream:contracts-ai-2026q4', 'tech_contracts_ai', 'exact_name_collision_dedup', 1, '2026-10-28')"
    )
    # 6. Prefix twin orphans: org:engineering-construction vs org_engineering_construction
    cur.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, display_name, description, properties_json, active, created_at, updated_at) "
        "VALUES ('org_engineering_construction', 'organization', "
        "'Engineering & Construction', 'Engineering & Construction', 'Canonical E&C vertical', '{}', 1, '2026-10-28', '2026-10-28')"
    )
    cur.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, display_name, description, properties_json, active, created_at, updated_at) "
        "VALUES ('org:engineering-construction', 'organization', "
        "'Engineering & Construction', 'Engineering & Construction', 'Prefix twin E&C', '{}', 1, '2026-10-28', '2026-10-28')"
    )
    # 7. Program and Product with PART_OF and role-hub detour + inline PM
    cur.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, display_name, description, properties_json, active, created_at, updated_at) "
        "VALUES ('program:asset-performance-generation', 'program', "
        "'Asset Performance - Generation', 'Asset Performance - Generation', "
        "'Program Owner: Jose-Enriquez, Gerardo | Value: $60M-$110M', '{}', 1, '2026-10-28', '2026-10-28')"
    )
    cur.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, display_name, description, properties_json, active, created_at, updated_at) "
        "VALUES ('product:center-of-work-excellence-renewable', 'product', "
        "'Center of Work Excellence - Renewable', 'Center of Work Excellence - Renewable', "
        "'PM: harper Hoffman | WBS: 60571759', '{}', 1, '2026-10-28', '2026-10-28')"
    )
    cur.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, display_name, description, properties_json, active, created_at, updated_at) "
        "VALUES ('stakeholder_mandy_hopmann', 'stakeholder', "
        "'harper Hoffman', 'harper Hoffman', 'Product Manager', '{}', 1, '2026-10-28', '2026-10-28')"
    )
    cur.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, display_name, description, properties_json, active, created_at, updated_at) "
        "VALUES ('role:product-manager', 'role', "
        "'Product Manager', 'Product Manager', 'Role hub', '{}', 1, '2026-10-28', '2026-10-28')"
    )
    cur.execute(
        "INSERT INTO assertion (assertion_id, assertion_type, subject_node_id, predicate, object_node_id, scope_text, qualifiers_json, confidence_score, validation_state, canonical_status, canonical_eligible, retrieval_eligible, currentness_state, primary_source_id, approval_reference, created_at, updated_at) "
        "VALUES ('asrt-part-of-1', 'PART_OF', 'product:center-of-work-excellence-renewable', "
        "'PART_OF', 'program:asset-performance-generation', "
        "'Center of Work Excellence - Renewable is part of Asset Performance - Generation.', "
        "'{}', 0.95, 'verified', 'canonical', 1, 1, 'current', 'source-717c5274fcba2eeca0c87fc6', 'HITL-SEED', '2026-10-28', '2026-10-28')"
    )
    cur.execute(
        "INSERT INTO assertion (assertion_id, assertion_type, subject_node_id, predicate, object_node_id, scope_text, qualifiers_json, confidence_score, validation_state, canonical_status, canonical_eligible, retrieval_eligible, currentness_state, primary_source_id, approval_reference, created_at, updated_at) "
        "VALUES ('asrt-role-1', 'HAS_ROLE_IN', 'stakeholder_mandy_hopmann', "
        "'HAS_ROLE_IN', 'role:product-manager', "
        "'harper Hoffman serves as Product Manager for Center of Work Excellence - Renewable.', "
        "'{}', 0.95, 'verified', 'canonical', 1, 1, 'current', 'source-717c5274fcba2eeca0c87fc6', 'HITL-SEED', '2026-10-28', '2026-10-28')"
    )
    # 8. Misrouted portfolio candidate assertion from source-717c5274fcba2eeca0c87fc6
    cur.execute(
        "INSERT INTO assertion (assertion_id, assertion_type, subject_node_id, predicate, object_node_id, scope_text, qualifiers_json, confidence_score, validation_state, canonical_status, canonical_eligible, retrieval_eligible, currentness_state, primary_source_id, approval_reference, created_at, updated_at) "
        "VALUES ('asrt-cand-port-1', 'GOVERNS', 'org:engineering-construction', "
        "'GOVERNS', 'program:asset-performance-generation', "
        "'Engineering & Construction governs Asset Performance - Generation.', "
        "'{\"fact_promotion_eligible\": false, \"source_posture\": \"status_update\"}', 0.93, "
        "'verified', 'candidate', 0, 0, 'current', 'source-717c5274fcba2eeca0c87fc6', NULL, '2026-10-28', '2026-10-28')"
    )
    cur.execute(
        "INSERT INTO evidence_item (evidence_id, source_id, direct_quote, created_at) "
        "VALUES ('ev-1', 'source-717c5274fcba2eeca0c87fc6', 'Grounded portfolio quote', '2026-10-28')"
    )
    cur.execute("INSERT INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship) VALUES ('asrt-part-of-1', 'ev-1', 'supports')")
    cur.execute("INSERT INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship) VALUES ('asrt-role-1', 'ev-1', 'supports')")
    cur.execute("INSERT INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship) VALUES ('asrt-cand-port-1', 'ev-1', 'supports')")
    cur.execute(
        "INSERT INTO search_document VALUES ('asrt-part-of-1', 'assertion', 'asrt-part-of-1', "
        "'Center of Work Excellence - Renewable', 'Center of Work Excellence - Renewable is part of Asset Performance - Generation.', "
        "'canonical', 'current', 1, 'source-717c5274fcba2eeca0c87fc6', 'asrt-part-of-1', '2026-10-28')"
    )
    conn.commit()
    conn.close()


def test_hygiene_panel_detects_clusters_and_remediates_mece_hierarchy_end_to_end(tmp_path: Path) -> None:
    db_path = tmp_path / "graph.sqlite"
    _seed_non_mece_portfolio_db(db_path)

    panel = GraphHygienePanel(graph_path=db_path)
    report = panel.run_full_audit()

    proposed_fixes = {f.proposed_fix for f in report.findings}
    assert "split_or_normalize_compound_node" in proposed_fixes
    assert "reconcile_hierarchy_and_ownership_edges" in proposed_fixes
    assert "synthesize_hierarchical_retrieval_docs" in proposed_fixes

    cluster_ids = {c["cluster_id"] for c in report.hitl_clusters}
    assert "CLUSTER-02G-COMPOUND-AND-MISTYPED-NODES" in cluster_ids
    assert "CLUSTER-03D-MECE-HIERARCHY-AND-OWNERSHIP" in cluster_ids
    assert "CLUSTER-07E-RETRIEVAL-BREADCRUMB-SYNTHESIS" in cluster_ids

    pkg_dict = {
        "status": report.status,
        "findings": [asdict(f) for f in report.findings],
        "remediation_directives": report.remediation_directives,
        "hitl_clusters": report.hitl_clusters,
    }
    conn = sqlite3.connect(str(db_path))
    promised = compile_promised_mutations(conn, pkg_dict)
    assert len(promised) >= 3
    conn.close()

    pkg_path = tmp_path / "remediation_pkg.json"
    pkg_path.write_text(json.dumps(pkg_dict, indent=2), encoding="utf-8")
    receipt_path = tmp_path / "receipt.json"
    receipt = apply_hygiene_remediation(graph_path=db_path, package_path=pkg_path, receipt_path=receipt_path, dry_run=False)
    assert receipt["terminal_outcome"] == "PASS", json.dumps({"proof": receipt["physical_proof"], "contract": receipt["itemized_hitl_contract"]})
    assert receipt["itemized_hitl_contract"]["status"] == "PASS"
    assert receipt["remediation_stats"]["compound_or_mistyped_nodes_normalized"] > 0
    assert receipt["remediation_stats"]["hierarchy_and_ownership_items_reconciled"] > 0
    assert receipt["remediation_stats"]["hierarchical_retrieval_docs_synthesized"] > 0

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    # 1. Compound product split into 3 atomic products
    atomic_names = {
        r[0]
        for r in cur.execute(
            "SELECT canonical_name FROM node WHERE active = 1 AND node_type = 'product' AND canonical_name LIKE 'Grid Mod - %'"
        ).fetchall()
    }
    assert {"Grid Mod - SCADA", "Grid Mod - ADMS", "Grid Mod - eDERMS"}.issubset(atomic_names)
    # 2. Truncated parenthetical repaired
    repaired_ws = cur.execute("SELECT canonical_name FROM node WHERE node_id = 'workstream_asset_ops_adms'").fetchone()[0]
    assert repaired_ws == "Asset Operations (ADMS)"
    # 3. Comma-inverted stakeholder normalized
    repaired_person = cur.execute("SELECT canonical_name FROM node WHERE node_id = 'stakeholder_smith_jane'").fetchone()[0]
    assert repaired_person == "Jane Smith"
    # 4. LENS Part 1 re-typed to workstream
    lens_type = cur.execute("SELECT node_type FROM node WHERE node_id = 'program_lens_part_1'").fetchone()[0]
    assert lens_type == "workstream"
    # 5. Contracts.AI - 2026Q4 unmerged back into active workstream node
    contracts_ws = cur.execute(
        "SELECT node_type, active FROM node WHERE node_id = 'workstream:contracts-ai-2026q4'"
    ).fetchone()
    assert contracts_ws == ("workstream", 1)
    # 6. Prefix twin merged into canonical underscore ID
    prefix_active = cur.execute("SELECT active FROM node WHERE node_id = 'org:engineering-construction'").fetchone()[0]
    assert prefix_active == 0
    # 7. Direct OWNS_OR_LEADS edges created for harper Hoffman and Gerardo Jose-Enriquez
    owns_rows = cur.execute(
        "SELECT subject_node_id, object_node_id FROM assertion WHERE predicate = 'OWNS_OR_LEADS' AND canonical_status = 'canonical'"
    ).fetchall()
    assert ("stakeholder_mandy_hopmann", "product:center-of-work-excellence-renewable") in owns_rows
    # 8. Misrouted portfolio candidate promoted to canonical
    cand_status = cur.execute("SELECT canonical_status FROM assertion WHERE assertion_id = 'asrt-cand-port-1'").fetchone()[0]
    assert cand_status == "canonical"
    # 9. Search documents enriched with [MECE Hierarchy: ...] breadcrumbs
    bodies = [r[0] for r in cur.execute("SELECT body FROM search_document WHERE retrieval_eligible = 1").fetchall()]
    assert any("[MECE Hierarchy:" in (b or "") for b in bodies)
    conn.close()


def test_ingestion_healers_preserve_portfolio_hierarchy_and_resolve_redirects(tmp_path: Path) -> None:
    # 1. Document signal router routes Program/Product/Mission charters to SOW_CONTRACT
    decision = evaluate_document_signal_routing(
        "# Program Planning - Products\n## Financial Summary (5-Year $M)\n| 2026 | 2027 |",
        title="Asset Performance - Generation",
        metadata={"type": "program"},
    )
    assert decision.archetype == DocumentArchetype.SOW_CONTRACT
    assert decision.policy.fact_promotion_eligible is True
    assert decision.policy.authority_rank >= 9

    # 2. Ontology healer preserves PART_OF and CONTAINS for portfolio tiers
    changes: list[str] = []
    assert _resolve_canonical_predicate("PART_OF", "product:cwe-renewable", "program:asset-performance-generation", changes) == "PART_OF"
    assert _resolve_canonical_predicate("CONTAINS", "program:asset-performance-generation", "product:cwe-renewable", changes) == "CONTAINS"

    # 3. Risk healer preserves DELIVERS on portfolio relationships
    mutations = {
        "entities": [],
        "relationship_signals": [
            {
                "signal_id": "rel-prog-prod-DELIVERS",
                "from_id": "program:asset-performance-generation",
                "to_id": "product:cwe-renewable",
                "relationship": "DELIVERS",
                "predicate": "DELIVERS",
                "evidence_ids": ["ev-1"],
            }
        ],
    }
    prune_and_budget_signals(mutations, ["ev-1"], is_document=True)
    assert len(mutations["relationship_signals"]) == 1
    assert mutations["relationship_signals"][0]["relationship"] == "DELIVERS"

    # 4. Publication verifier resolves entity_redirect targets cleanly
    db_path = tmp_path / "pub_verify.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT, display_name TEXT, description TEXT, properties_json TEXT, active INTEGER);
        CREATE TABLE alias (alias_id TEXT PRIMARY KEY, node_id TEXT, alias TEXT);
        CREATE TABLE entity_redirect (from_node_id TEXT PRIMARY KEY, to_node_id TEXT, active INTEGER);
        INSERT INTO node VALUES ('stakeholder_mandy_hopmann', 'harper Hoffman', 'harper Hoffman', 'PM', '{}', 1);
        INSERT INTO entity_redirect VALUES ('person:mandy-hopmann', 'stakeholder_mandy_hopmann', 1);
        """
    )
    found = _inspect_node_in_graph(conn, "person:mandy-hopmann")
    assert found is not None
    assert found["node_id"] == "stakeholder_mandy_hopmann"
    conn.close()
