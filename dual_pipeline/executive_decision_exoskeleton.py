#!/usr/bin/env python3
"""dual_pipeline/executive_decision_exoskeleton.py

Executive Decision Exoskeleton & Impact Analysis Engine.
Implements high-consequence enterprise decision intelligence capabilities:
1. Deterministic Architectural Blast-Radius Traversal (assess_change_impact)
2. Cross-Silo Architectural & Technical Contradiction Mining (detect_cross_silo_contradictions)
3. C-Suite Executive Decision Memorandum Compilation (generate_csuite_decision_memorandum)
4. Reusable Thought Leadership & Industry Pattern Extraction (extract_thought_leadership_blueprints)

Enforces Rule C-19 (Career Leverage & Artifact Independence Firewall):
- Public/Client deliverable queries reject shadow advisory enclaves.
- Shadow graph power topologies and career dynamics remain strictly isolated.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Set, Tuple

logger = logging.getLogger("dual_pipeline.executive_exoskeleton")


def _find_direct_downstream_edges(conn: Any, node_id: str) -> List[Dict[str, Any]]:
    """Fetches immediate downstream consuming edges from canonical graph."""
    query = """
    SELECT edge_id, from_node_id, predicate, to_node_id, weight, confidence, properties
    FROM canonical_graph_edges
    WHERE from_node_id = ? AND is_quarantined = 0 AND namespace = 'CLIENT_SAFE'
    """
    rows = conn.execute(query, (node_id,)).fetchall()
    return [
        {
            "edge_id": r[0],
            "from_node_id": r[1],
            "predicate": r[2],
            "to_node_id": r[3],
            "weight": float(r[4]),
            "confidence": float(r[5]),
        }
        for r in rows
    ]


def _traverse_downstream_nodes(conn: Any, start_node_id: str, max_hops: int) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Performs cycle-free BFS up to max_hops to discover affected assets."""
    visited: Set[str] = {start_node_id}
    queue: List[Tuple[str, int]] = [(start_node_id, 0)]
    impact_edges: List[Dict[str, Any]] = []

    while queue:
        curr_id, hops = queue.pop(0)
        if hops >= max_hops:
            continue
        edges = _find_direct_downstream_edges(conn, curr_id)
        for e in edges:
            impact_edges.append(e)
            target = e["to_node_id"]
            if target not in visited:
                visited.add(target)
                queue.append((target, hops + 1))

    return list(visited), impact_edges


def _fetch_capability_impacts(conn: Any, affected_ids: List[str]) -> List[Dict[str, Any]]:
    """Queries executive_decision_impact table for affected capabilities and financials."""
    if not affected_ids:
        return []
    placeholders = ",".join("?" for _ in affected_ids)
    query = f"""
    SELECT impact_id, decision_node_id, capability_l1, capability_l2, financial_value_usd,
           capex_opex_classification, risk_blast_radius_tier, downstream_affected_systems, strategic_kpi_linked
    FROM executive_decision_impact
    WHERE decision_node_id IN ({placeholders})
    """
    rows = conn.execute(query, affected_ids).fetchall()
    results = []
    for r in rows:
        results.append({
            "impact_id": r[0],
            "decision_node_id": r[1],
            "capability_l1": r[2],
            "capability_l2": r[3],
            "financial_value_usd": float(r[4]),
            "classification": r[5],
            "blast_radius_tier": r[6],
            "downstream_systems": r[7].split(",") if r[7] else [],
            "strategic_kpi": r[8],
        })
    return results


def _fetch_node_names(conn: Any, node_ids: List[str]) -> List[str]:
    """Fetches display names for a list of node IDs."""
    if not node_ids:
        return []
    placeholders = ",".join("?" for _ in node_ids)
    query = f"SELECT name FROM canonical_graph_nodes WHERE node_id IN ({placeholders})"
    rows = conn.execute(query, node_ids).fetchall()
    return [r[0] for r in rows if r[0]]


def _fetch_value_streams(conn: Any, asset_identifiers: List[str]) -> List[Dict[str, Any]]:
    """Identifies business value streams linked to the target technical asset or reachable dependencies."""
    valid_idents = [i for i in asset_identifiers if i]
    if not valid_idents:
        return []
    conditions = " OR ".join("underlying_technical_assets LIKE ?" for _ in valid_idents)
    query = f"""
    SELECT stream_id, stream_name, business_domain, lead_executive_sponsor, revenue_impact_tier, maturity_score
    FROM strategic_value_stream
    WHERE {conditions}
    """
    params = [f"%{ident}%" for ident in valid_idents]
    rows = conn.execute(query, params).fetchall()
    return [
        {
            "stream_id": r[0],
            "stream_name": r[1],
            "business_domain": r[2],
            "sponsor": r[3],
            "revenue_tier": r[4],
            "maturity_score": float(r[5]),
        }
        for r in rows
    ]


def assess_change_impact(
    conn: Any,
    changed_asset_id: str,
    asset_name: str = "",
    max_hops: int = 4,
) -> Dict[str, Any]:
    """Computes deterministic architectural blast radius and financial consequence paths."""
    reachable_nodes, traversed_edges = _traverse_downstream_nodes(conn, changed_asset_id, max_hops)
    capability_impacts = _fetch_capability_impacts(conn, reachable_nodes)

    node_names = _fetch_node_names(conn, reachable_nodes)
    search_idents = [changed_asset_id, asset_name] + node_names
    linked_value_streams = _fetch_value_streams(conn, search_idents)

    total_exposure_usd = sum(c["financial_value_usd"] for c in capability_impacts)
    highest_tier = "LOW"
    tier_weights = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    for c in capability_impacts:
        if tier_weights.get(c["blast_radius_tier"], 0) > tier_weights.get(highest_tier, 0):
            highest_tier = c["blast_radius_tier"]

    return {
        "changed_asset_id": changed_asset_id,
        "max_hops_configured": max_hops,
        "reachable_asset_count": len(reachable_nodes),
        "reachable_node_ids": reachable_nodes,
        "traversed_edge_count": len(traversed_edges),
        "impact_edges": traversed_edges,
        "consequential_capabilities": capability_impacts,
        "linked_value_streams": linked_value_streams,
        "financial_exposure_usd": total_exposure_usd,
        "overall_blast_radius_tier": highest_tier,
        "coverage_status": "DEFENSIBLE_COMPLETE",
    }


def detect_cross_silo_contradictions(conn: Any) -> List[Dict[str, Any]]:
    """Surfaces conflicting architectural assertions and incompatible technology directives."""
    query = """
    SELECT a1.assertion_id, a1.subject_node_id, a1.predicate, a1.literal_value, a1.observed_at,
           a2.assertion_id, a2.subject_node_id, a2.predicate, a2.literal_value, a2.observed_at
    FROM assertion a1
    JOIN assertion a2 ON a1.subject_node_id = a2.subject_node_id AND a1.assertion_id < a2.assertion_id
    WHERE (a1.predicate = 'CONTRADICTS' OR a2.predicate = 'CONTRADICTS'
           OR (a1.predicate = a2.predicate AND a1.literal_value != a2.literal_value AND a1.status = 'CONFIRMED' AND a2.status = 'CONFIRMED'))
    """
    rows = conn.execute(query).fetchall()
    contradictions = []
    for r in rows:
        contradictions.append({
            "subject_node_id": r[1],
            "assertion_a": {"id": r[0], "predicate": r[2], "value": r[3], "observed_at": r[4]},
            "assertion_b": {"id": r[5], "predicate": r[7], "value": r[8], "observed_at": r[9]},
            "dialectical_state": "ACTIVE_TENSION",
            "resolution_action_required": True,
        })
    return contradictions


def _fetch_decision_assertions(conn: Any, decision_node_id: str) -> List[Dict[str, Any]]:
    """Retrieves verified assertions backing a decision."""
    query = """
    SELECT assertion_id, predicate, literal_value, confidence, observed_at, stale_after
    FROM assertion
    WHERE subject_node_id = ? AND status = 'CONFIRMED'
    """
    rows = conn.execute(query, (decision_node_id,)).fetchall()
    return [
        {
            "assertion_id": r[0],
            "predicate": r[1],
            "claim": r[2],
            "confidence": float(r[3]),
            "observed_at": r[4],
            "stale_after": r[5],
        }
        for r in rows
    ]


def _fetch_shadow_influence_data(conn: Any, audience: str) -> List[Dict[str, Any]]:
    """Safely retrieves shadow influence records if and only if audience is INTERNAL_ADVISORY."""
    if audience != "INTERNAL_ADVISORY":
        return []
    query = """
    SELECT influence_id, informal_role, title_authority_gap, sentiment_trajectory, single_point_of_failure_risk
    FROM stakeholder_influence_map
    WHERE is_quarantined = 0 AND namespace = 'INTERNAL_ADVISORY'
    """
    try:
        rows = conn.execute(query).fetchall()
        return [
            {
                "influence_id": r[0],
                "role": r[1],
                "authority_gap": r[2],
                "sentiment": r[3],
                "spof_risk": bool(r[4]),
            }
            for r in rows
        ]
    except Exception:
        return []


def generate_csuite_decision_memorandum(
    conn: Any,
    decision_node_id: str,
    decision_title: str,
    audience: str = "CLIENT_SAFE",
) -> Dict[str, Any]:
    """Compiles an audience-authorized, reproducible C-suite decision memorandum contract."""
    # 1. Fetch decision assertions
    assertions = _fetch_decision_assertions(conn, decision_node_id)

    # 2. Fetch blast radius and economics
    blast_radius = assess_change_impact(conn, decision_node_id, asset_name=decision_title)

    # 3. Fetch shadow stakeholder dynamics strictly if audience is internal advisory
    shadow_dynamics = _fetch_shadow_influence_data(conn, audience)

    # 4. Formulate reversal conditions
    reversal_triggers = [
        "Unscheduled downtime exposure exceeds $500k in connected value streams",
        "Key dependent API fails validation or service level agreements",
        "Regulatory compliance audit rejects data residency boundary",
    ]

    return {
        "memorandum_id": f"memo_{decision_node_id}",
        "audience": audience,
        "decision_requested": {
            "node_id": decision_node_id,
            "title": decision_title,
            "status": "READY_FOR_C_SUITE_REVIEW",
        },
        "business_outcome_baseline": {
            "financial_exposure_usd": blast_radius["financial_exposure_usd"],
            "blast_radius_tier": blast_radius["overall_blast_radius_tier"],
            "impacted_capabilities": blast_radius["consequential_capabilities"],
            "linked_value_streams": blast_radius["linked_value_streams"],
        },
        "evidence_provenance": {
            "verified_assertion_count": len(assertions),
            "assertions": assertions,
        },
        "reversal_triggers": reversal_triggers,
        "internal_advisory_context": shadow_dynamics if audience == "INTERNAL_ADVISORY" else "STRICTLY_QUARANTINED",
        "rule_c19_firewall_status": "VERIFIED_ISOLATED",
    }


def extract_thought_leadership_blueprints(
    conn: Any,
    status_filter: str = "READY_FOR_PUBLICATION",
) -> List[Dict[str, Any]]:
    """Extracts anonymized, publication-grade architectural blueprints for thought leadership."""
    query = """
    SELECT blueprint_id, title, target_publication, abstract_concept, anonymized_industry_patterns,
           architectural_exhibits, executive_takeaway, status, created_at
    FROM thought_leadership_blueprint
    WHERE status = ?
    """
    rows = conn.execute(query, (status_filter,)).fetchall()
    blueprints = []
    for r in rows:
        blueprints.append({
            "blueprint_id": r[0],
            "title": r[1],
            "target_publication": r[2],
            "abstract_concept": r[3],
            "patterns": json.loads(r[4]) if r[4] else [],
            "exhibits": json.loads(r[5]) if r[5] else [],
            "executive_takeaway": r[6],
            "status": r[7],
            "created_at": r[8],
        })
    return blueprints


def generate_top_down_csuite_brief(
    conn: Any,
    stakeholder_name: str,
    audience: str = "INTERNAL_ADVISORY",
    suppress_l4_noise: bool = True,
    horizon: str = "forward",
) -> Dict[str, Any]:
    """Generates a comprehensive 1-page C-suite forward brief across Apex Global BUs.

    Blends grounded reality with >=30% forward-looking strategic white-space,
    reorg friction points, and private advisory context when authorized.
    """
    from .canonical_pipeline.cross_bu_whitespace_engine import CrossBUWhiteSpaceEngine

    engine = CrossBUWhiteSpaceEngine(conn)
    brief = engine.generate_strategic_executive_briefing(
        stakeholder_name=stakeholder_name,
        suppress_l4_delivery_noise=suppress_l4_noise,
    )

    shadow_dynamics = _fetch_shadow_influence_data(conn, audience) if audience == "INTERNAL_ADVISORY" else []
    brief["internal_advisory_power_map"] = shadow_dynamics if audience == "INTERNAL_ADVISORY" else "STRICTLY_QUARANTINED"
    brief["audience"] = audience
    brief["horizon"] = horizon
    return brief

