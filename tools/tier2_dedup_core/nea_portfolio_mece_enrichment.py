"""Canonical NEA Portfolio 6-Tier MECE Hierarchy, Owner, and Multi-Horizon Financial Enrichment."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Set, Tuple

from tools.tier2_dedup_core.mece_hierarchy_remediation import (
    _ensure_governance_approval,
    _insert_canonical_assertion,
    _norm_slug,
    _upsert_node,
)

NEA_PLANNING_SOURCE_ID = "source-717c5274fcba2eeca0c87fc6"
NEA_PORTFOLIO_SOURCE_ID = "source_20261028_nea_16_programs_portfolio_view"
NEA_WORKSTREAMS_SOURCE_ID = "source_20261028_nea_planner_workstreams_headcount"

MISSING_TD_PRODUCTS: List[Tuple[str, str, str, float, float, float, int, str]] = [
    ("product_google_marketplace_glv", "Google Marketplace GLV", "Sofie Netteberg", 0.0, 0.0, 0.0, 0, "In Use"),
    ("product_pd_conduit_pc_engineer", "PD Conduit - P&C Engineer", "Michael Maione", 819.0, 6600.0, 5451.0, 1, "In Development"),
    ("product_virtual_service_planner_assistant_vspa", "Virtual Service Planner Assistant (VSPA)", "Michael Maione", 0.0, 0.0, 0.0, 0, "In Use"),
    ("product_original_timesheet_validation_product_page", "X - Original Timesheet Validation Product Page", "Michael Maione", 646.6, 2943.75, 1907.15, 0, "In Use"),
    ("product_storm_damage_model", "X - Storm Damage Model", "Casey O'Keefe", 0.0, 0.0, 0.0, 0, "In Use"),
    ("product_power_delivery_data_foundation", "Power Delivery Data Foundation", "Lori Bartlett", 0.0, 0.0, 0.0, 1, "In Development"),
    ("product_enterprise_data_governance_ai_embedded", "Enterprise Data Governance (AI Embedded)", "Divya Vedula", 0.0, 0.0, 0.0, 1, "In Development"),
]

GEN_PRODUCTS_META: List[Tuple[str, str, str, float, float, float, int, str]] = [
    ("product_center_of_work_excellence_renewable", "Center of Work Excellence - Renewable", "TJ Kudalis", 319.0, 13696.0, 13278.0, 4, "In Use"),
    ("product_conduit_fossil", "Conduit - Fossil", "Lancey Lexima", 1683.5, 9172.5, 7469.0, 9, "In Development"),
    ("product_conduit_nuclear", "Conduit - Nuclear", "Hayden Radovich", 3932.75, 52530.0, 46617.25, 8, "In Use"),
    ("product_core_renewable_central_maintenance_scheduling", "CORE - Renewable Central Maintenance Scheduling", "TJ Kudalis", 1684.0, 28875.0, 26910.5, 4, "Planning"),
    ("product_generation_entitlement_gen_e_and_ops_report", "Generation Entitlement (Gen E and Ops Report)", "Lancey Lexima", 3049.0, 17900.0, 14273.0, 10, "In Use"),
    ("product_pgd_renewables_conduit", "PGD Renewables Conduit", "TJ Kudalis", 4232.0, 15049.0, 10406.0, 18, "In Use"),
    ("product_quest_nuclear_asset_health_monitoring", "Quest - Nuclear Asset Health Monitoring", "Lancey Lexima", 2179.3, 23425.5, 20796.2, 5, "In Use"),
    ("product_rocc_conduit", "ROCC Conduit", "Lancey Lexima", 319.0, 1250.0, 903.0, 3, "Planning"),
    ("product_solar_and_storage_scheduling", "Solar and Storage Scheduling", "Jack O'Shea", 0.0, 0.0, 0.0, 1, "Draft"),
    ("product_wind_major_component_total_cost_optimization", "Wind Major Component Total Cost Optimization", "TJ Kudalis", 319.0, 2732.0, 2083.0, 3, "In Use"),
]

CWEOS_MISSIONS: List[Tuple[str, str, str, str, float, float, float]] = [
    ("2026 Q2", "Center for Work Excellence Operating System (GRID_OS) - Q2", "Complete", "Rose Stevens", 319.0, 13696.0, 13278.0),
    ("2026 Q3", "Center of Work Excellence OS (GRID_OS) - Q3", "Complete", "harper Hoffman", 364.0, 3627.25, 3260.5),
    ("2026 Q4", "Center of Work Excellence Operating System (GRID_OS) - Q4", "In Progress", "harper Hoffman", 318.5, 1916.75, 1596.87),
    ("2027 Q1", "Center of Work Excellence - 2027 Q1", "Draft", "harper Hoffman", 0.0, 0.0, 0.0),
]


def _resolve_or_create_stakeholder(conn: Any, node_cols: Set[str], full_name: str, role_desc: str, now_iso: str) -> str:
    row = conn.execute(
        "SELECT node_id FROM node WHERE active = 1 AND node_type IN ('stakeholder', 'person') AND LOWER(canonical_name) = ? LIMIT 1",
        (full_name.lower(),),
    ).fetchone()
    if row:
        return str(row[0])
    nid = f"stakeholder_{_norm_slug(full_name)}"
    _upsert_node(conn, node_cols, nid, "stakeholder", full_name, role_desc, now_iso)
    return nid


def _retire_compound_product_nodes(conn: Any, tables: Set[str], now_iso: str, sample_ids: List[str]) -> int:
    compound_ids = (
        "product_core_renewable_central_maintenance_scheduling_wind_major_compone",
        "product_conduit_fossil_quest_nuclear_asset_health_monitoring",
        "product_conduit_nuclear_conduit_fossil",
    )
    retired = 0
    for cid in compound_ids:
        row = conn.execute("SELECT active FROM node WHERE node_id = ?", (cid,)).fetchone()
        if row:
            conn.execute("UPDATE node SET active = 0, status = 'merged', updated_at = ? WHERE node_id = ?", (now_iso, cid))
            conn.execute(
                "UPDATE assertion SET currentness_state = 'historical', retrieval_eligible = 0, updated_at = ? WHERE subject_node_id = ? OR object_node_id = ?",
                (now_iso, cid, cid),
            )
            if "search_document" in tables:
                conn.execute(
                    "UPDATE search_document SET currentness_state = 'historical', retrieval_eligible = 0, updated_at = ? WHERE assertion_id IN (SELECT assertion_id FROM assertion WHERE subject_node_id = ? OR object_node_id = ?)",
                    (now_iso, cid, cid),
                )
            sample_ids.append(cid)
            retired += 1
    return retired


def _enrich_generation_and_td_products(
    conn: Any, tables: Set[str], node_cols: Set[str], asrt_cols: Set[str], now_iso: str, sample_ids: List[str]
) -> int:
    added = 0
    for nid, cname, pm_name, cost_k, ben_k, nav_k, missions_cnt, status in GEN_PRODUCTS_META:
        desc = (
            f"{cname} — Product in Program Asset Performance - Generation | Status: {status} | "
            f"Product Manager: {pm_name} | Missions: {missions_cnt} | Est. Cost: ${cost_k:,.2f}k | "
            f"Est. Benefit: ${ben_k:,.2f}k | Est. NAV: ${nav_k:,.2f}k"
        )
        _upsert_node(conn, node_cols, nid, "product", cname, desc, now_iso)
        pm_id = _resolve_or_create_stakeholder(conn, node_cols, pm_name, f"Product Manager for {cname}", now_iso)
        _insert_canonical_assertion(
            conn, tables, asrt_cols, "program_asset_performance_generation", "DELIVERS", nid,
            f"Program Asset Performance - Generation DELIVERS Product {cname} (PM: {pm_name}, Missions: {missions_cnt}, Est. Cost: ${cost_k:,.2f}k, Est. Benefit: ${ben_k:,.2f}k, Est. NAV: ${nav_k:,.2f}k).",
            NEA_PLANNING_SOURCE_ID, now_iso,
        )
        _insert_canonical_assertion(
            conn, tables, asrt_cols, nid, "PART_OF", "program_asset_performance_generation",
            f"Product {cname} is PART_OF Program Asset Performance - Generation.",
            NEA_PLANNING_SOURCE_ID, now_iso,
        )
        _insert_canonical_assertion(
            conn, tables, asrt_cols, pm_id, "OWNS_OR_LEADS", nid,
            f"{pm_name} OWNS_OR_LEADS Product {cname} as Product Manager.",
            NEA_PLANNING_SOURCE_ID, now_iso,
        )
        sample_ids.append(nid)
        added += 1

    for nid, cname, pm_name, cost_k, ben_k, nav_k, missions_cnt, status in MISSING_TD_PRODUCTS:
        desc = (
            f"{cname} — Product in Program Asset Performance - T&D | Status: {status} | "
            f"Product Manager: {pm_name} | Missions: {missions_cnt} | Est. Cost: ${cost_k:,.2f}k | "
            f"Est. Benefit: ${ben_k:,.2f}k | Est. NAV: ${nav_k:,.2f}k"
        )
        _upsert_node(conn, node_cols, nid, "product", cname, desc, now_iso)
        pm_id = _resolve_or_create_stakeholder(conn, node_cols, pm_name, f"Product Manager for {cname}", now_iso)
        _insert_canonical_assertion(
            conn, tables, asrt_cols, "program_asset_performance_td", "DELIVERS", nid,
            f"Program Asset Performance - T&D DELIVERS Product {cname} (PM: {pm_name}, Missions: {missions_cnt}, Est. Cost: ${cost_k:,.2f}k, Est. Benefit: ${ben_k:,.2f}k, Est. NAV: ${nav_k:,.2f}k).",
            NEA_PLANNING_SOURCE_ID, now_iso,
        )
        _insert_canonical_assertion(
            conn, tables, asrt_cols, nid, "PART_OF", "program_asset_performance_td",
            f"Product {cname} is PART_OF Program Asset Performance - T&D.",
            NEA_PLANNING_SOURCE_ID, now_iso,
        )
        _insert_canonical_assertion(
            conn, tables, asrt_cols, pm_id, "OWNS_OR_LEADS", nid,
            f"{pm_name} OWNS_OR_LEADS Product {cname} as Product Manager.",
            NEA_PLANNING_SOURCE_ID, now_iso,
        )
        sample_ids.append(nid)
        added += 1
    return added


def _enrich_program_and_product_financials_and_kpis(
    conn: Any, tables: Set[str], node_cols: Set[str], asrt_cols: Set[str], now_iso: str, sample_ids: List[str]
) -> int:
    # 1. Program 3: Asset Performance - Generation
    gen_fin = {
        "program_id": 3,
        "owner": "Jarod Bryant",
        "business_units": ["Power Generation Division (PGD)", "Business Management", "Nuclear"],
        "base_year": 2026,
        "useful_life_years": 5,
        "confidence_level": 0.65,
        "kpis": [
            {"metric": "Reduce ALR", "units": "$M", "baseline": 450, "goal": "Decrease", "notes": "NEER Only"},
            {"metric": "Reduce Capital", "units": "$M", "baseline": 2173, "goal": "Decrease"},
            {"metric": "Reduce O&M", "units": "$M", "baseline": 976, "goal": "Decrease"},
        ],
        "financials_usd_k": {
            "revenue_impact": {"2026": 1525, "2027": 17050, "2028": 18050, "2029": 4513, "2030": 0, "total": 41138},
            "capital_savings": {"2026": 1125, "2027": 12017, "2028": 19854, "2029": 6014, "2030": 0, "total": 39010},
            "om_savings": {"2026": 2203, "2027": 22761, "2028": 36354, "2029": 15464, "2030": 7700, "total": 84482},
            "capital_cost": {"2026": 15915.55, "2027": 1802, "2028": 0, "2029": 0, "2030": 0, "total": 17717.55},
            "om_cost": {"2026": 613.5, "2027": 1505, "2028": 1505, "2029": 487, "2030": 66, "total": 4176.5},
            "total_benefit_usd_k": 164630.0,
            "total_nav_usd_k": 142735.95,
        },
    }
    if "properties_json" in node_cols:
        conn.execute("UPDATE node SET properties_json = ?, updated_at = ? WHERE node_id = 'program_asset_performance_generation'", (json.dumps(gen_fin), now_iso))
    _insert_canonical_assertion(
        conn, tables, asrt_cols, "program_asset_performance_generation", "HAS_CLAIM_TEXT", None,
        "Program 3 'Asset Performance - Generation' (Owner: Jarod Bryant, 10 Products, 65 Missions) addresses ~$350M/yr in avoidable generation availability losses (KPI Baselines: ALR $450M NEER, Capital $2,173M, O&M $976M). 5-Year (2026-2030) Estimated Financials: Total Benefit $164,630k ($164.63M: Revenue Impact $41,138k, Capital Savings $39,010k, O&M Savings $84,482k), Capital Cost $17,717.55k, O&M Cost $4,176.5k, Net Present/Added Value (NAV) $142,735.95k ($142.74M) at 0.65 confidence.",
        NEA_PLANNING_SOURCE_ID, now_iso, qualifiers={"retrieval_kind": "nea_program_financial_summary", **gen_fin},
    )

    # 2. Program 4: Asset Performance - T&D
    td_fin = {
        "program_id": 4,
        "owner": "Lori Bartlett",
        "business_units": ["Power Delivery (PD)"],
        "base_year": 2026,
        "useful_life_years": 3,
        "confidence_level": 0.71,
        "kpis": [
            {"metric": "O&M Cost", "units": "$MM", "baseline": 178.7, "goal": "Decrease"},
            {"metric": "Grid Reliability: SAIDI", "units": "minutes", "baseline": 46, "goal": "Target"},
            {"metric": "Customer Satisfaction", "units": "%", "baseline": 80, "goal": "Increase"},
            {"metric": "OSHA Recordables", "units": "count", "baseline": 20, "goal": "Decrease"},
            {"metric": "Preventable Vehicle Accidents: ANSI", "units": "count", "baseline": 58, "goal": "Decrease"},
        ],
        "financials_usd_k": {
            "capital_savings": {"2026": 2812.5, "2027": 20523, "2028": 20523, "2029": 5168, "total": 49026.5},
            "om_savings": {"2026": 668.75, "2027": 2452, "2028": 2452, "2029": 631.5, "total": 6204.25},
            "capital_cost": {"2026": 4332.6, "2027": 0, "2028": 0, "2029": 0, "total": 4332.6},
            "om_cost": {"2026": 450, "2027": 900, "2028": 1080, "2029": 270, "total": 2700},
            "total_benefit_usd_k": 55230.75,
            "total_nav_usd_k": 48198.15,
        },
    }
    if "properties_json" in node_cols:
        conn.execute("UPDATE node SET properties_json = ?, updated_at = ? WHERE node_id = 'program_asset_performance_td'", (json.dumps(td_fin), now_iso))
    _insert_canonical_assertion(
        conn, tables, asrt_cols, "program_asset_performance_td", "HAS_CLAIM_TEXT", None,
        "Program 4 'Asset Performance - T&D' (Owner: Lori Bartlett, Business Unit: Power Delivery, 11 Products, 35 Missions) delivers a self-healing, storm-resilient grid (KPI Baselines: O&M Cost $178.7M, SAIDI 46 min, Customer Satisfaction 80%, OSHA Recordables 20, ANSI Vehicle Accidents 58). 2026-2029 Estimated Financials: Total Benefit $55,230.75k ($55.23M: Capital Savings $49,026.5k, O&M Savings $6,204.25k), Capital Cost $4,332.6k, O&M Cost $2,700k, Est. NAV $48,198.15k ($48.20M) at 0.71 confidence.",
        NEA_PLANNING_SOURCE_ID, now_iso, qualifiers={"retrieval_kind": "nea_program_financial_summary", **td_fin},
    )

    # 3. Product 3: Center of Work Excellence - Renewable (GRID_OS)
    cwe_fin = {
        "product_id": 3,
        "wbs": "A2.000800.06.C267",
        "product_manager": "TJ Kudalis",
        "q4_mission_pm": "harper Hoffman",
        "engineering_lead": "Brett Gavagni",
        "financials_usd_k": {
            "revenue_impact": {"2026": 0, "2027": 5900, "2028": 5900, "2029": 1475, "total": 13275},
            "om_savings": {"2026": 77, "2027": 153, "2028": 153, "2029": 38, "total": 421},
            "capital_cost": {"2026": 319, "2027": 0, "2028": 0, "2029": 0, "total": 319},
            "om_cost": {"2026": 18, "2027": 36, "2028": 36, "2029": 9, "total": 99},
            "missions_total_cost_k": 1001.5,
            "missions_total_benefit_k": 19240.0,
            "missions_total_nav_k": 18135.37,
        },
    }
    if "properties_json" in node_cols:
        conn.execute("UPDATE node SET properties_json = ?, updated_at = ? WHERE node_id = 'product_center_of_work_excellence_renewable'", (json.dumps(cwe_fin), now_iso))
    brett_id = _resolve_or_create_stakeholder(conn, node_cols, "Brett Gavagni", "Engineering Lead for Center of Work Excellence - Renewable", now_iso)
    _insert_canonical_assertion(
        conn, tables, asrt_cols, brett_id, "OWNS_OR_LEADS", "product_center_of_work_excellence_renewable",
        "Brett Gavagni OWNS_OR_LEADS Product Center of Work Excellence - Renewable as Engineering Lead.",
        NEA_PLANNING_SOURCE_ID, now_iso,
    )
    _insert_canonical_assertion(
        conn, tables, asrt_cols, "product_center_of_work_excellence_renewable", "HAS_CLAIM_TEXT", None,
        "Product 3 'Center of Work Excellence - Renewable' (GRID_OS, Program: Asset Performance - Generation, PM: TJ Kudalis, Q3/Q4 Mission PM: harper Hoffman, Engineering Lead: Brett Gavagni, WBS: A2.000800.06.C267, NEER PGD) targets >=90% scoping form completeness, >=85% upload recovery, and 403 -> 0 critical security vulnerabilities. 2026-2029 Financials: $13.7M 3-year benefit ($13,275k Revenue Impact from ALR capture + $421k O&M Savings) vs $319k Capital Cost and $99k O&M Cost; 4 Missions (2026 Q2-2027 Q1) total $1,001.5k cost, $19,240k benefit, and $18,135.37k NAV.",
        NEA_PLANNING_SOURCE_ID, now_iso, qualifiers={"retrieval_kind": "nea_product_financial_summary", **cwe_fin},
    )

    for qtr, m_name, m_status, m_pm, cost_k, ben_k, nav_k in CWEOS_MISSIONS:
        m_id = f"workstream_cweos_{_norm_slug(qtr)}"
        _upsert_node(
            conn, node_cols, m_id, "workstream", m_name,
            f"GRID_OS Mission ({qtr}) | Status: {m_status} | PM: {m_pm} | Est. Cost: ${cost_k}k | Est. Benefit: ${ben_k}k | Est. NAV: ${nav_k}k",
            now_iso,
        )
        pm_id = _resolve_or_create_stakeholder(conn, node_cols, m_pm, f"Product Manager for {m_name}", now_iso)
        _insert_canonical_assertion(conn, tables, asrt_cols, "product_center_of_work_excellence_renewable", "DELIVERS", m_id, f"Product Center of Work Excellence - Renewable DELIVERS Mission {m_name} ({qtr}, Status: {m_status}, PM: {m_pm}, Cost: ${cost_k}k, Benefit: ${ben_k}k, NAV: ${nav_k}k).", NEA_PLANNING_SOURCE_ID, now_iso)
        _insert_canonical_assertion(conn, tables, asrt_cols, pm_id, "OWNS_OR_LEADS", m_id, f"{m_pm} OWNS_OR_LEADS Mission {m_name}.", NEA_PLANNING_SOURCE_ID, now_iso)

    # 4. Mission 292: LENS Visual Asset Intelligence System, 2 of 2 for Maximo Updates
    lens_m292_id = "entity-c71f00c4798a227888a4aa5a5285"
    if not conn.execute("SELECT 1 FROM node WHERE node_id = ?", (lens_m292_id,)).fetchone():
        lens_m292_id = "workstream_mission_292_lens_maximo_updates"
        _upsert_node(conn, node_cols, lens_m292_id, "workstream", "LENS Visual Asset Intelligence System, 2 of 2 for Maximo Updates", "Mission 292 (2026 Q4) under Product PGD Renewables Conduit and Program Asset Performance - Generation", now_iso)
    m292_fin = {
        "mission_id": 292,
        "quarter": "2026 Q4",
        "program": "Asset Performance - Generation",
        "product": "PGD Renewables Conduit",
        "product_manager": "harper Hoffman",
        "execution_mode": "Third Party POD - PartnerAdvisory - 5 members ($517,562 NTE for 12 weeks at 42 hrs/wk)",
        "wbs": ["C1.004125.56.C003 (Technician)", "C1.004125.56.C002 (CM)"],
        "financials_usd_k": {
            "om_savings": {"2026": 0, "2027": 412, "2028": 534, "2029": 509, "total": 1455},
            "capital_cost": {"2026": 591.5, "2027": 0, "2028": 0, "2029": 0, "total": 591.5},
            "om_cost": {"2026": 2.5, "2027": 10, "2028": 10, "2029": 7.5, "total": 30},
            "project_atlas_total_improvement_by_2030_usd_m": 42.0,
            "lens_part_2_attribution_pct": 2.0,
            "lens_part_1_attribution_pct": 3.0,
        },
        "approvals": [
            {"approver": "Gordon Paynter", "status": "Approved", "date": "2026-09-18", "note": "Director and finance endorsed."},
            {"approver": "Jarod Bryant", "status": "Director Endorsement", "date": "2026-09-16"},
            {"approver": "Raisa Wieser", "status": "Finance Manager Endorsement", "date": "2026-09-09"},
            {"approver": "TJ Kudalis", "status": "Finance Endorsement Requested", "date": "2026-09-09"},
        ],
    }
    if "properties_json" in node_cols:
        conn.execute("UPDATE node SET properties_json = ?, updated_at = ? WHERE node_id = ?", (json.dumps(m292_fin), now_iso, lens_m292_id))
    _insert_canonical_assertion(
        conn, tables, asrt_cols, "product_pgd_renewables_conduit", "DELIVERS", lens_m292_id,
        "Product PGD Renewables Conduit (Program: Asset Performance - Generation) DELIVERS Mission 292 'LENS Visual Asset Intelligence System, 2 of 2 for Maximo Updates' (PM: harper Hoffman, WBS: C1.004125.56.C003 / C1.004125.56.C002, PartnerAdvisory 5-member Third-Party POD at $517,562 NTE).",
        NEA_PLANNING_SOURCE_ID, now_iso,
    )
    _insert_canonical_assertion(
        conn, tables, asrt_cols, lens_m292_id, "PART_OF", "product_pgd_renewables_conduit",
        "Mission 292 'LENS Visual Asset Intelligence System, 2 of 2 for Maximo Updates' is PART_OF Product PGD Renewables Conduit and Program Asset Performance - Generation.",
        NEA_PLANNING_SOURCE_ID, now_iso,
    )
    mandy_id = _resolve_or_create_stakeholder(conn, node_cols, "harper Hoffman", "Product Manager for LENS Mission 292 and GRID_OS", now_iso)
    _insert_canonical_assertion(
        conn, tables, asrt_cols, mandy_id, "OWNS_OR_LEADS", lens_m292_id,
        "harper Hoffman OWNS_OR_LEADS Mission 292 'LENS Visual Asset Intelligence System, 2 of 2 for Maximo Updates' as Product Manager.",
        NEA_PLANNING_SOURCE_ID, now_iso,
    )
    _insert_canonical_assertion(
        conn, tables, asrt_cols, lens_m292_id, "HAS_CLAIM_TEXT", None,
        "Mission 292 'LENS Visual Asset Intelligence System, 2 of 2 for Maximo Updates' (2026 Q4, Program: Asset Performance - Generation, Product: PGD Renewables Conduit, PM: harper Hoffman, WBS: C1.004125.56.C003 / C1.004125.56.C002) delivers the Atlas Write-Back Pipeline to Maximo via Max Core REST API (>=98% Multi ID to Asset ID mapping accuracy, 0 duplicate writes, 100% major component conflict exception reporting). Estimated Financials (2026-2029): $1,455k O&M Savings ($412k in 2027, $534k in 2028, $509k in 2029; 2% of Project Atlas $42M inventory improvement by 2030; 3% attributed to LENS Part 1), $591.5k Capital Cost (2026), $30k O&M Cost, $517,562 PartnerAdvisory 5-resource 12-week POD NTE. Approved by Gordon Paynter (2026-09-18), endorsed by Jarod Bryant (2026-09-16) and Raisa Wieser (2026-09-09).",
        NEA_PLANNING_SOURCE_ID, now_iso, qualifiers={"retrieval_kind": "nea_mission_financial_summary", **m292_fin},
    )
    sample_ids.append(lens_m292_id)
    return 10


def enrich_nea_portfolio_mece_hierarchy(conn: Any, now_iso: str, sample_ids: List[str]) -> Dict[str, int]:
    """Execute complete NEA Portfolio 6-tier MECE enrichment and financial/owner wiring."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    _ensure_governance_approval(conn, "REQ_INGEST_CEC57955C84BCE3A", tables)
    node_cols = {r[1] for r in conn.execute("PRAGMA table_info(node)").fetchall()} if "node" in tables else set()
    asrt_cols = {r[1] for r in conn.execute("PRAGMA table_info(assertion)").fetchall()} if "assertion" in tables else set()

    retired_compounds = _retire_compound_product_nodes(conn, tables, now_iso, sample_ids)
    products_enriched = _enrich_generation_and_td_products(conn, tables, node_cols, asrt_cols, now_iso, sample_ids)
    fin_assertions = _enrich_program_and_product_financials_and_kpis(conn, tables, node_cols, asrt_cols, now_iso, sample_ids)
    return {
        "compound_products_retired": retired_compounds,
        "atomic_products_enriched": products_enriched,
        "financial_and_kpi_assertions_added": fin_assertions,
    }
