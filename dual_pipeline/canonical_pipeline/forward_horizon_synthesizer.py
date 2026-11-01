"""Forward Horizon Synthesizer for Executive Decision Intelligence.

Enforces a >=30% forward-looking strategic foresight floor (H1 90-day / H2 18-month)
across Apex Global Enterprises leadership: PGD (Mark Lemasney), APEX (Timothy Oliver / Faisal Bhatti),
One Tech Core (Ben Miron / logan Miller / Chakra Sankaraiah), Products (Rich Argentieri /
Matt Barrows), and M&A (Ashish Gupta).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from storage.connection import OperationalError, create_sqlite_conn

SQL_SCHEMA_FILE = Path(__file__).resolve().parents[2] / "graph" / "migrations" / "v58_forward_looking_executive_graph.sql"

ENTERPRISE_BU_MANDATES = [
    {
        "mandate_id": "mandate_pgd_fleet_outage_compression",
        "bu_name": "POWER_GENERATION_DIVISION",
        "owning_executive_id": "entity-97752d48bac01c9b2a6c4eb5ba90",
        "owning_executive_name": "Mark Lemasney",
        "title": "PGD Fleet Reliability & Autonomous Outage Compression",
        "target_horizon": "H2_18_MONTH",
        "strategic_objective": "Drive $45M generation O&M reduction across thermal, wind, and solar assets via predictive asset degradation models.",
        "kpi_metrics": ["forced_outage_rate_reduction_18pct", "om_savings_45m_usd", "predictive_dispatch_coverage_100pct"],
        "estimated_capital_budget_usd": 45000000.0,
    },
    {
        "mandate_id": "mandate_pgd_ops_support_fabric",
        "bu_name": "POWER_GENERATION_DIVISION",
        "owning_executive_id": "entity-kk-venkataraman",
        "owning_executive_name": "Krishnakumar (KK) Venkataraman",
        "title": "PGD Engineering & Technical Operations Unified Telemetry Fabric",
        "target_horizon": "H1_90_DAY",
        "strategic_objective": "Unify plant SCADA and digital maintenance records into centralized telemetry stream for cross-fleet diagnostics.",
        "kpi_metrics": ["telemetry_latency_sub_second", "asset_telemetry_onboarding_sprints_4"],
        "estimated_capital_budget_usd": 12000000.0,
    },
    {
        "mandate_id": "mandate_fpl_grid_modernization",
        "bu_name": "FLORIDA_POWER_LIGHT",
        "owning_executive_id": "entity-timothy-oliver",
        "owning_executive_name": "Timothy Oliver",
        "title": "APEX Development Technology Storm Hardening & Smart Grid Automation",
        "target_horizon": "H2_18_MONTH",
        "strategic_objective": "Scale intelligent substation automation and storm restoration models under Florida Public Service Commission rate-base mandates.",
        "kpi_metrics": ["storm_restoration_speedup_30pct", "rate_base_compliance_100pct"],
        "estimated_capital_budget_usd": 65000000.0,
    },
    {
        "mandate_id": "mandate_fpl_customer_service_ai",
        "bu_name": "FLORIDA_POWER_LIGHT",
        "owning_executive_id": "entity-faisal-bhatti",
        "owning_executive_name": "Faisal Bhatti",
        "title": "APEX Next-Gen Customer Service & Agentic Contact Resolution",
        "target_horizon": "H1_90_DAY",
        "strategic_objective": "Deploy agentic customer care resolution pods to reduce call hold duration and handle storm outage spikes autonomously.",
        "kpi_metrics": ["first_contact_resolution_85pct", "contact_center_cost_reduction_20pct"],
        "estimated_capital_budget_usd": 15000000.0,
    },
    {
        "mandate_id": "mandate_core_tech_platform_consolidation",
        "bu_name": "ONE_TECH_CORE",
        "owning_executive_id": "stakeholder_ben_miron",
        "owning_executive_name": "Ben Miron",
        "title": "Core Tech Platform Unification & Cloud Delivery Rationalization",
        "target_horizon": "H1_90_DAY",
        "strategic_objective": "Consolidate legacy enterprise platforms and infrastructure operations into unified Core Tech platform serving all BUs by November.",
        "kpi_metrics": ["platform_redundancy_reduction_40pct", "core_tech_onboarding_lead_time_minus_50pct"],
        "estimated_capital_budget_usd": 85000000.0,
    },
    {
        "mandate_id": "mandate_cpo_products_portfolio_monetization",
        "bu_name": "ONE_TECH_PRODUCTS",
        "owning_executive_id": "person_rich_argentieri",
        "owning_executive_name": "Richard Argentieri",
        "title": "One Tech Products Commercial Portfolio & Enduring Product Operating Model",
        "target_horizon": "H2_18_MONTH",
        "strategic_objective": "Transition 4 enduring product lanes (Asset Performance, Infra Dev, Energy Mgmt, Corporate Services) from project funding to value-stream P&L.",
        "kpi_metrics": ["product_velocity_increase_25pct", "commercial_ip_monetization_30m"],
        "estimated_capital_budget_usd": 55000000.0,
    },
    {
        "mandate_id": "mandate_dominion_integration_synergies",
        "bu_name": "DOMINION_INTEGRATION",
        "owning_executive_id": "entity-ashish-gupta",
        "owning_executive_name": "Ashish Gupta",
        "title": "Dominion Merger Technology Due Diligence & Day-1 Cutover Factory",
        "target_horizon": "H2_18_MONTH",
        "strategic_objective": "Execute multi-billion dollar Meridian Utility Corp IT asset unbundling, regulatory compliance isolation, and Day-1 systems cutover.",
        "kpi_metrics": ["day_1_cutover_readiness_100pct", "it_synergies_target_75m"],
        "estimated_capital_budget_usd": 120000000.0,
    },
]

PROACTIVE_WHITESPACE_THESES = [
    {
        "thesis_id": "thesis_pgd_asset_performance_twin",
        "bu_name": "POWER_GENERATION_DIVISION",
        "target_executive_id": "entity-97752d48bac01c9b2a6c4eb5ba90",
        "target_executive_name": "Mark Lemasney",
        "thesis_title": "Enterprise Asset Performance Digital Twin Across All PGD Generation Fleets",
        "opportunity_type": "CROSS_BU_EXPANSION",
        "problem_statement": "PGD engineering teams manage plant telemetry across disparate vendor tools, creating blind spots in real-time degradation.",
        "proposed_play": "PartnerAdvisory positions unified LENS Asset Performance engine across thermal and renewable fleets as the single operating system.",
        "strategic_horizon": "H1_90_DAY",
        "estimated_sow_value_usd": 3500000.0,
        "competitive_threat": "BCG / GE Digital",
    },
    {
        "thesis_id": "thesis_fpl_agentic_storm_dispatcher",
        "bu_name": "FLORIDA_POWER_LIGHT",
        "target_executive_id": "entity-timothy-oliver",
        "target_executive_name": "Timothy Oliver",
        "thesis_title": "Agentic Grid Restoration & Storm Crew Logistics Dispatcher",
        "opportunity_type": "AI_CAPABILITY_LEAP",
        "problem_statement": "Extreme weather storm restoration logistics currently suffer manual crew staging inefficiencies and delayed customer communication.",
        "proposed_play": "Deploy multi-agent simulation model for predictive crew mobilization, staged equipment routing, and live customer updates.",
        "strategic_horizon": "H2_18_MONTH",
        "estimated_sow_value_usd": 4200000.0,
        "competitive_threat": "Accenture",
    },
    {
        "thesis_id": "thesis_one_tech_genai_hub",
        "bu_name": "ONE_TECH_CORE",
        "target_executive_id": "stakeholder_ben_miron",
        "target_executive_name": "Ben Miron",
        "thesis_title": "Enterprise GenAI Token Governance & Unified Productivity Platform",
        "opportunity_type": "OPERATING_MODEL_TRANSFORMATION",
        "problem_statement": "Disjointed generative AI initiatives across IT and NEA risk redundant API costs, inconsistent guardrails, and compliance breach.",
        "proposed_play": "PartnerAdvisory establishes centralized GenAI gateway under Chakra Sankaraiah & logan Miller with cost tracking and policy enforcement.",
        "strategic_horizon": "H1_90_DAY",
        "estimated_sow_value_usd": 2800000.0,
        "competitive_threat": "Microsoft Direct",
    },
    {
        "thesis_id": "thesis_products_asset_monetization",
        "bu_name": "ONE_TECH_PRODUCTS",
        "target_executive_id": "person_rich_argentieri",
        "target_executive_name": "Richard Argentieri",
        "thesis_title": "Commercial Asset Optimization & External SaaS Packaging",
        "opportunity_type": "UNSOLICITED_PROPOSAL",
        "problem_statement": "NEA analytics models and Matt Barrows' asset performance tools hold market value beyond internal Apex Global deployments.",
        "proposed_play": "Build multi-tenant commercial packaging for NEA analytics products for external utility licensing and co-development.",
        "strategic_horizon": "H2_18_MONTH",
        "estimated_sow_value_usd": 5000000.0,
        "competitive_threat": "Internal Inertia",
    },
    {
        "thesis_id": "thesis_dominion_tech_factory",
        "bu_name": "DOMINION_INTEGRATION",
        "target_executive_id": "entity-ashish-gupta",
        "target_executive_name": "Ashish Gupta",
        "thesis_title": "M&A Integration Acceleration Factory & Systems Decoupling Playbook",
        "opportunity_type": "UNSOLICITED_PROPOSAL",
        "problem_statement": "Dominion IT separation requires carving out legacy SAP, SCADA, and customer data with zero operational downtime.",
        "proposed_play": "Provide turnkey M&A technical migration pod to reverse-engineer data schemas and automate cloud target migration.",
        "strategic_horizon": "H2_18_MONTH",
        "estimated_sow_value_usd": 3200000.0,
        "competitive_threat": "Big 4 M&A Practice",
    },
]

CROSS_BU_REORG_TENSIONS = [
    {
        "tension_id": "tension_products_vs_core_tech_capacity",
        "source_bu": "ONE_TECH_PRODUCTS",
        "target_bu": "ONE_TECH_CORE",
        "source_executive_id": "person_rich_argentieri",
        "target_executive_id": "stakeholder_ben_miron",
        "tension_category": "PLATFORM_OWNERSHIP",
        "description": "Rich Argentieri's Product VPs (Matt Barrows, Chris Nunalee) require dedicated rapid cloud engineering, but Ben Miron's Core Tech centralizes platform delivery under standard enterprise SLAs.",
        "november_reorg_impact": "Potential delivery throttling for fast-moving AI products if Core Tech imposes monolithic release gates.",
        "mitigation_strategy": "Establish federated 2-3 person full-stack pod pods under Chakra's AI-PDLC model with pre-cleared Core Tech platform templates.",
        "severity_score": 4,
    },
    {
        "tension_id": "tension_pgd_vs_products_funding",
        "source_bu": "POWER_GENERATION_DIVISION",
        "target_bu": "ONE_TECH_PRODUCTS",
        "source_executive_id": "entity-97752d48bac01c9b2a6c4eb5ba90",
        "target_executive_id": "person_rich_argentieri",
        "tension_category": "BUDGET_ALLOCATION",
        "description": "Mark Lemasney (PGD) funds specific generation site improvements, resisting CPO horizontal product allocations that do not show immediate site-level O&M ROI.",
        "november_reorg_impact": "Budget pushback during Q4 planning if Product VPs cannot prove discrete unit-level cost recovery.",
        "mitigation_strategy": "Package Matt Barrows' Asset Performance deliverable with direct plant-by-plant payback scorecards linked to PGD KPIs.",
        "severity_score": 4,
    },
]


def init_forward_looking_schema(conn: Any) -> None:
    """Apply migration schema for forward-looking executive tables."""
    if SQL_SCHEMA_FILE.exists():
        sql = SQL_SCHEMA_FILE.read_text(encoding="utf-8")
        conn.executescript(sql)
    conn.commit()


def seed_enterprise_strategic_portfolio(conn: Any) -> None:
    """Populate enterprise BU mandates, white-space theses, and cross-BU tensions."""
    init_forward_looking_schema(conn)
    now = datetime.now(timezone.utc).isoformat()

    for m in ENTERPRISE_BU_MANDATES:
        conn.execute(
            """
            INSERT OR REPLACE INTO bu_strategic_mandate (
                mandate_id, bu_name, owning_executive_id, owning_executive_name,
                title, target_horizon, strategic_objective, kpi_metrics_json,
                estimated_capital_budget_usd, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
            """,
            (
                m["mandate_id"], m["bu_name"], m["owning_executive_id"], m["owning_executive_name"],
                m["title"], m["target_horizon"], m["strategic_objective"], json.dumps(m["kpi_metrics"]),
                m["estimated_capital_budget_usd"], now, now
            ),
        )

    for w in PROACTIVE_WHITESPACE_THESES:
        conn.execute(
            """
            INSERT OR REPLACE INTO whitespace_thesis (
                thesis_id, bu_name, target_executive_id, target_executive_name,
                thesis_title, opportunity_type, problem_statement, proposed_play,
                strategic_horizon, partner_advisory_role, estimated_sow_value_usd, competitive_threat, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'STRATEGIC_ADVISORY_PARTNER', ?, ?, ?)
            """,
            (
                w["thesis_id"], w["bu_name"], w["target_executive_id"], w["target_executive_name"],
                w["thesis_title"], w["opportunity_type"], w["problem_statement"], w["proposed_play"],
                w["strategic_horizon"], w["estimated_sow_value_usd"], w["competitive_threat"], now
            ),
        )

    for t in CROSS_BU_REORG_TENSIONS:
        conn.execute(
            """
            INSERT OR REPLACE INTO cross_bu_tension (
                tension_id, source_bu, target_bu, source_executive_id, source_executive_name,
                target_executive_id, target_executive_name, tension_category, description,
                friction_points_json, november_reorg_impact, mitigation_strategy, severity_score, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, ?, ?)
            """,
            (
                t["tension_id"], t["source_bu"], t["target_bu"], t["source_executive_id"],
                "Richard Argentieri" if "argentieri" in t["source_executive_id"] else "Mark Lemasney",
                t["target_executive_id"],
                "Ben Miron" if "miron" in t["target_executive_id"] else "Richard Argentieri",
                t["tension_category"], t["description"], t["november_reorg_impact"],
                t["mitigation_strategy"], t["severity_score"], now
            ),
        )
    conn.commit()


def get_executive_horizon_portfolio(
    identifier_or_name: str,
    conn: Any,
    min_strategic_ratio: float = 0.30
) -> Dict[str, Any]:
    """Retrieve executive portfolio with >=30% forward-looking strategic foresight."""
    init_forward_looking_schema(conn)
    cur = conn.cursor()
    clean_name = identifier_or_name.strip().lower()
    clean_slug = clean_name.replace(" ", "_")
    query = f"%{clean_name}%"
    slug_query = f"%{clean_slug}%"

    # 1. Fetch BU Mandates (Forward H1/H2)
    cur.execute(
        """
        SELECT mandate_id, bu_name, owning_executive_name, title, target_horizon,
               strategic_objective, estimated_capital_budget_usd
        FROM bu_strategic_mandate
        WHERE LOWER(owning_executive_name) LIKE ? OR LOWER(owning_executive_id) LIKE ? OR LOWER(owning_executive_id) LIKE ?
        """,
        (query, query, slug_query),
    )
    mandates = [
        {"id": r[0], "bu": r[1], "exec": r[2], "title": r[3], "horizon": r[4], "objective": r[5], "budget": r[6]}
        for r in cur.fetchall()
    ]

    # 2. Fetch White-space theses (Forward H1/H2)
    cur.execute(
        """
        SELECT thesis_id, bu_name, target_executive_name, thesis_title, opportunity_type,
               proposed_play, strategic_horizon, estimated_sow_value_usd, competitive_threat
        FROM whitespace_thesis
        WHERE LOWER(target_executive_name) LIKE ? OR LOWER(target_executive_id) LIKE ? OR LOWER(target_executive_id) LIKE ?
        """,
        (query, query, slug_query),
    )
    whitespace = [
        {"id": r[0], "bu": r[1], "exec": r[2], "title": r[3], "type": r[4], "play": r[5], "horizon": r[6], "sow_value": r[7], "threat": r[8]}
        for r in cur.fetchall()
    ]

    # 3. Fetch Reorg Tensions
    cur.execute(
        """
        SELECT tension_id, source_bu, target_bu, tension_category, description,
               november_reorg_impact, mitigation_strategy, severity_score
        FROM cross_bu_tension
        WHERE LOWER(source_executive_id) LIKE ? OR LOWER(source_executive_id) LIKE ?
           OR LOWER(target_executive_id) LIKE ? OR LOWER(target_executive_id) LIKE ?
           OR LOWER(source_executive_name) LIKE ? OR LOWER(target_executive_name) LIKE ?
        """,
        (query, slug_query, query, slug_query, query, query),
    )
    tensions = [
        {"id": r[0], "source_bu": r[1], "target_bu": r[2], "category": r[3], "desc": r[4], "reorg_impact": r[5], "mitigation": r[6], "severity": r[7]}
        for r in cur.fetchall()
    ]

    # 4. Fetch Grounded past assertions (H0)
    grounded_facts = []
    try:
        cur.execute(
            """
            SELECT a.assertion_id, a.predicate, a.object_literal, a.scope_text
            FROM assertion a
            JOIN node n ON (a.subject_node_id = n.node_id OR a.object_node_id = n.node_id)
            WHERE a.canonical_status = 'canonical'
              AND a.canonical_eligible = 1
              AND a.currentness_state = 'current'
              AND (LOWER(n.canonical_name) LIKE ? OR LOWER(n.node_id) LIKE ?)
            LIMIT 25
            """,
            (query, query),
        )
        grounded_facts = [
            {"id": r[0], "predicate": r[1], "claim": r[2] or r[3], "horizon": "H0_PAST_FACT"}
            for r in cur.fetchall()
        ]
    except OperationalError:
        grounded_facts = []

    forward_count = len(mandates) + len(whitespace)
    total_count = forward_count + len(grounded_facts)
    strategic_ratio = (forward_count / total_count) if total_count > 0 else 1.0

    return {
        "query": identifier_or_name,
        "strategic_ratio": round(strategic_ratio, 3),
        "meets_strategic_floor": strategic_ratio >= min_strategic_ratio,
        "forward_horizon_counts": {
            "mandates_count": len(mandates),
            "whitespace_count": len(whitespace),
            "tensions_count": len(tensions),
            "grounded_facts_count": len(grounded_facts),
        },
        "mandates": mandates,
        "whitespace_theses": whitespace,
        "reorg_tensions": tensions,
        "grounded_facts": grounded_facts,
    }
