"""End-to-end verification of Wave 2 NEA Portfolio 6-Tier MECE Hierarchy Reconciliation."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tests.end_to_end.test_graph_hygiene_uwg_role_e2e import _bootstrap_test_graph
from tools.reconcile_nea_portfolio_mece_hierarchy import reconcile_nea_portfolio_graph


def _seed_nea_portfolio_db(db_path: Path) -> None:
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
            source_name TEXT,
            authority_rank INTEGER DEFAULT 5,
            source_posture TEXT
        );
        INSERT INTO source VALUES ('source-717c5274fcba2eeca0c87fc6', 'document', 'Document 20261028 Nea Program Planning Lens grid_os Asset Performance', 5, 'derivative_internal');
        INSERT INTO node (node_id, node_type, canonical_name, display_name, description, properties_json, status, active, created_at, updated_at) VALUES
            ('program_asset_performance_generation', 'program', 'Asset Performance - Generation', 'Asset Performance - Generation', 'Program 3', '{}', 'canonical', 1, '2026-10-28', '2026-10-28'),
            ('program_asset_performance_td', 'program', 'Asset Performance - T&D', 'Asset Performance - T&D', 'Program 4', '{}', 'canonical', 1, '2026-10-28', '2026-10-28'),
            ('product_core_renewable_central_maintenance_scheduling_wind_major_compone', 'product', 'CORE - Renewable Central Maintenance Scheduling,Wind Major Component Total Cost Optimization', 'CORE - Renewable Central Maintenance Scheduling,Wind Major Component Total Cost Optimization', 'Compound product', '{}', 'canonical', 1, '2026-10-28', '2026-10-28'),
            ('product_conduit_fossil_quest_nuclear_asset_health_monitoring', 'product', 'Conduit - Fossil,Quest - Nuclear Asset Health Monitoring', 'Conduit - Fossil,Quest - Nuclear Asset Health Monitoring', 'Compound product', '{}', 'canonical', 1, '2026-10-28', '2026-10-28'),
            ('product_conduit_nuclear_conduit_fossil', 'product', 'Conduit - Nuclear, Conduit - Fossil', 'Conduit - Nuclear, Conduit - Fossil', 'Compound product', '{}', 'canonical', 1, '2026-10-28', '2026-10-28'),
            ('entity-505c1a972dfa558c1d1680722bb6', 'program', 'LENS Part 1', 'LENS Part 1', 'Mis-typed workstream', '{}', 'canonical', 1, '2026-10-28', '2026-10-28'),
            ('entity-dc7c4030f31deacc94dee496b0f0', 'workstream', 'Contracts.AI - 2026Q4', 'Contracts.AI - 2026Q4', 'Q4 workstream', '{}', 'merged', 0, '2026-10-28', '2026-10-28'),
            ('entity-80cef0ae3a7443c1a2c776f7a6ba', 'technical_anchor', 'Contracts AI', 'Contracts AI', 'Tech anchor', '{}', 'canonical', 1, '2026-10-28', '2026-10-28'),
            ('entity-c71f00c4798a227888a4aa5a5285', 'workstream', 'LENS Visual Asset Intelligence System, 2 of 2 for Maximo Updates', 'LENS Visual Asset Intelligence System, 2 of 2 for Maximo Updates', 'Mission 292', '{}', 'canonical', 1, '2026-10-28', '2026-10-28');
        INSERT INTO entity_redirect (from_node_id, to_node_id, merge_event_id, active, created_at) VALUES
            ('entity-dc7c4030f31deacc94dee496b0f0', 'entity-80cef0ae3a7443c1a2c776f7a6ba', 'merge-contracts-1', 1, '2026-10-28');
        INSERT INTO evidence_item (evidence_id, source_id, direct_quote, created_at) VALUES
            ('ev-nea-1', 'source-717c5274fcba2eeca0c87fc6', 'NEA Program Planning quote', '2026-10-28');
        INSERT INTO assertion (
            assertion_id, assertion_type, subject_node_id, predicate, object_node_id,
            scope_text, qualifiers_json, confidence_score, validation_state,
            canonical_status, canonical_eligible, retrieval_eligible, currentness_state,
            primary_source_id, created_at, updated_at
        ) VALUES (
            'asrt-cand-292', 'HAS_CLAIM_TEXT', 'entity-c71f00c4798a227888a4aa5a5285', 'HAS_CLAIM_TEXT', NULL,
            'LENS Part 2 will deliver an Atlas Write-Back Pipeline.',
            '{"fact_promotion_eligible": false}', 0.94, 'verified',
            'candidate', 0, 0, 'current', 'source-717c5274fcba2eeca0c87fc6', '2026-10-28', '2026-10-28'
        );
        INSERT INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship) VALUES
            ('asrt-cand-292', 'ev-nea-1', 'supports');
        """
    )
    conn.commit()
    conn.close()


def test_reconcile_nea_portfolio_graph_end_to_end(tmp_path: Path) -> None:
    db_path = tmp_path / "graph.sqlite"
    receipt_path = tmp_path / "wave2_receipt.json"
    _seed_nea_portfolio_db(db_path)

    receipt = reconcile_nea_portfolio_graph(db_path, receipt_path=receipt_path)
    assert receipt["terminal_outcome"] == "PASS"
    assert receipt["physical_proof"]["overall"] == "PASS"
    assert receipt_path.exists()

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # 1. All 3 compound products retired and replaced by atomic products
    for cid in (
        "product_core_renewable_central_maintenance_scheduling_wind_major_compone",
        "product_conduit_fossil_quest_nuclear_asset_health_monitoring",
        "product_conduit_nuclear_conduit_fossil",
    ):
        assert cur.execute("SELECT active FROM node WHERE node_id = ?", (cid,)).fetchone()[0] == 0

    active_products = {
        r[0]
        for r in cur.execute("SELECT canonical_name FROM node WHERE active = 1 AND node_type = 'product'").fetchall()
    }
    assert {
        "CORE - Renewable Central Maintenance Scheduling",
        "Wind Major Component Total Cost Optimization",
        "Conduit - Fossil",
        "Conduit - Nuclear",
        "Quest - Nuclear Asset Health Monitoring",
        "Center of Work Excellence - Renewable",
        "PGD Renewables Conduit",
        "Google Marketplace GLV",
        "PD Conduit - P&C Engineer",
        "Virtual Service Planner Assistant (VSPA)",
        "X - Original Timesheet Validation Product Page",
        "X - Storm Damage Model",
    }.issubset(active_products)

    # 2. Contracts.AI - 2026Q4 restored as active workstream and LENS Part 1 re-typed to workstream
    assert cur.execute("SELECT node_type, active FROM node WHERE node_id = 'entity-dc7c4030f31deacc94dee496b0f0'").fetchone() == ("workstream", 1)
    assert cur.execute("SELECT node_type, active FROM node WHERE node_id = 'entity-505c1a972dfa558c1d1680722bb6'").fetchone() == ("workstream", 1)

    # 3. Candidate assertions in source-717c5274fcba2eeca0c87fc6 promoted to canonical
    assert cur.execute("SELECT canonical_status FROM assertion WHERE assertion_id = 'asrt-cand-292'").fetchone()[0] == "canonical"

    # 4. Multi-horizon financials and KPIs persisted on Programs 3 & 4, Product 3 (GRID_OS), and Mission 292
    gen_props = json.loads(cur.execute("SELECT properties_json FROM node WHERE node_id = 'program_asset_performance_generation'").fetchone()[0])
    assert gen_props["financials_usd_k"]["total_benefit_usd_k"] == 164630.0
    assert gen_props["financials_usd_k"]["total_nav_usd_k"] == 142735.95

    td_props = json.loads(cur.execute("SELECT properties_json FROM node WHERE node_id = 'program_asset_performance_td'").fetchone()[0])
    assert td_props["financials_usd_k"]["total_benefit_usd_k"] == 55230.75

    m292_props = json.loads(cur.execute("SELECT properties_json FROM node WHERE node_id = 'entity-c71f00c4798a227888a4aa5a5285'").fetchone()[0])
    assert m292_props["mission_id"] == 292
    assert m292_props["financials_usd_k"]["om_savings"]["total"] == 1455

    # 5. Search documents synthesized with [MECE Hierarchy: ...] breadcrumbs
    bread_cnt = cur.execute(
        "SELECT COUNT(*) FROM search_document WHERE retrieval_eligible = 1 AND body LIKE '%[MECE Hierarchy:%'"
    ).fetchone()[0]
    assert bread_cnt >= 15
    conn.close()


def test_unified_charter_breakout_and_tabular_extractor_end_to_end(tmp_path: Path) -> None:
    """Verify both the Tabular Charter Extractor and Unified NEA Q4 Charter Breakout work end-to-end with Graph Hygiene."""
    from dual_pipeline.document_pipeline_core.tabular_row_extractor import (
        extract_tabular_workbook_and_table_projection,
    )
    from tools.tier2_dedup_core.mece_hierarchy_remediation import (
        remediate_mece_hierarchy_fixes,
        synthesize_portfolio_retrieval_breadcrumbs,
    )
    from tools.tier2_dedup_core.nea_q4_open_roles_enrichment import (
        enrich_nea_q4_charters_and_open_roles,
    )

    # 1. Verify ingest-time tabular row extractor classifies Program/Product/Workstream and wires OWNS_OR_LEADS
    md_table = (
        "## Sheet: NEA Portfolio\n"
        "| Product | Program | Product Manager | Status |\n"
        "| --- | --- | --- | --- |\n"
        "| PGD Renewables Conduit | Asset Performance - Generation | TJ Kudalis | In Use |\n"
        "| Center of Work Excellence - Renewable | Asset Performance - Generation | harper Hoffman | In Progress |\n"
    )
    proj = extract_tabular_workbook_and_table_projection(md_table, {"document_title": "NEA Portfolio View"})
    upserts = proj["graph_mutations"]["entity_upserts"]
    signals = proj["graph_mutations"]["relationship_signals"]
    prod_types = {u["name"]: u["type"] for u in upserts}
    assert prod_types.get("PGD Renewables Conduit") == "product"
    assert prod_types.get("TJ Kudalis") == "person"
    assert any(s["predicate"] == "OWNS_OR_LEADS" for s in signals)

    # 2. Verify Unified NEA Q4 Charter Breakout + Graph Hygiene Agent end-to-end
    db_path = tmp_path / "unified_charter.sqlite"
    _seed_nea_portfolio_db(db_path)
    conn = sqlite3.connect(str(db_path))
    sample_ids: list[str] = []
    stats = enrich_nea_q4_charters_and_open_roles(conn, "2026-10-29T00:00:00Z", sample_ids)
    assert stats["workstreams_enriched"] == 90
    assert stats["open_role_nodes_upserted"] == 44
    assert stats["atomic_products_enriched"] == 17

    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    remediate_mece_hierarchy_fixes(conn, [], tables, "2026-10-29T00:00:00Z", sample_ids)
    synth_cnt = synthesize_portfolio_retrieval_breadcrumbs(conn, tables, "2026-10-29T00:00:00Z", sample_ids)
    assert synth_cnt > 0

    # Verify Product -> DELIVERS -> Workstream and PM -> OWNS_OR_LEADS -> Workstream edges exist from catalog
    delivers_cnt = conn.execute(
        "SELECT COUNT(*) FROM assertion WHERE predicate = 'DELIVERS' AND subject_node_id LIKE 'product_%'"
    ).fetchone()[0]
    pm_leads_cnt = conn.execute(
        "SELECT COUNT(*) FROM assertion WHERE predicate = 'OWNS_OR_LEADS' AND subject_node_id LIKE 'stakeholder_%'"
    ).fetchone()[0]
    assert delivers_cnt >= 20
    assert pm_leads_cnt >= 20
    conn.close()
