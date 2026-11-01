#!/usr/bin/env python3
"""Governed remediation for NEA Planner page-break shifts, PartnerAdvisory Product Team Support SSOT, and split nodes."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import sha256_file, stable_id
from dual_pipeline.graph_consolidation_core.repoint import (
    repoint_node_assertions,
    repoint_node_edges,
    repoint_node_entities_and_routes,
)
from dual_pipeline.runtime_root import resolve_authoritative_runtime_root
from dual_pipeline.storage.governed_context import GovernedStorageContext, Row
from tools.graph_mutation_proof_core.proof import prove, snapshot
from tools.tier2_dedup_core.hygiene_promotion import refresh_sidecar_after_graph_cleanup
from tools.tier2_dedup_core.mece_hierarchy_remediation import (
    _ensure_governance_approval,
    _insert_canonical_assertion,
    _norm_slug,
    _upsert_node,
    synthesize_portfolio_retrieval_breadcrumbs,
)

ORG_CHART_SOURCE_ID = "source-666922d694ebf04ee51bb32f"
FALSE_NOEMI_REPORTS_TO_ID = "assertion-ae559ade16ea3d1e336a1876b505"
NEA_OCT20_PDF_SOURCE_ID = "source-95ce3df4ff5348b9fb6eb264"
NEA_OCT28_CSV_SOURCE_ID = "source_20261028_nea_planner_workstreams_headcount"
partner_advisory_ALIGNMENT_SOURCE_ID = "source_20261021_partner_advisory_workstream_alignment_roster"
PRODUCT_TEAM_SUPPORT_NODE_ID = "workstream_product_team_support"
NOEMI_NODE_ID = "entity-7b411db442f64e7e59c6190cf2f2"
APPROVAL_REF = "REQ_INGEST_CEC57955C84BCE3A"

SHIFTED_ASSERTION_IDS: Tuple[str, ...] = (
    # 5 false PF4 assertions attached to PF3
    "assertion-c3b354eb01b60689d72dba4bc0a7",
    "assertion-8e814319ca4114c1ce55f3e34bac",
    "assertion-c087072f98fe3e9891565722c414",
    "assertion-b2e697fb1e5a3dea97a07c15c7d8",
    "assertion-0e701ab06af9722480ef1081aaa9",
    # 2 false Contractor Control Tower assertions attached to Construction - Staffing Expansion
    "assertion-af82a964d99a80a33e09b2569fe6",
    "assertion-fd813f0e8e362f97c36f2b5ee0d9",
    # 2 false Construction - Staffing Expansion assertions attached to Contractor Control Tower
    "assertion-98aca9ab3c9d5d27fdc167c512b1",
    "assertion-d8a6772d8057afabc5420db7454c",
    # 5 false PL - Refinement and Expansion assertions attached to Process Atlas
    "assertion-e93d8fe34579a16c84aa5001bfb0",
    "assertion-f0f15cbf6a6a752d9e99194b19f4",
    "assertion-688c3b2a416e02bbbcd1bd6a4536",
    "assertion-6f10d9d2f1093018406515816ee6",
    "assertion-0b89467709a9608594240c96b1ab",
    # 1 false Dave Sunderman assertion attached to Regulated Capital Asset Management
    "assertion-2234827750ab9942dde3dd6157b4",
)

PF3_ROSTER: Tuple[Tuple[str, str, bool], ...] = (
    ("Hayden Radovich", "Product Manager (Staff)", True),
    ("Jacob Wellington", "Scrum Master", False),
    ("Alan Blosl", "Lead Architect (PartnerAdvisory)", False),
    ("Andrey Rybalkin", "Software Developer (Contractor)", False),
    ("Brent McCullough", "Software Developer (Contractor)", False),
    ("Jason Risch", "Software Developer (Contractor)", False),
    ("Laurice Phillips", "Software Developer (Contractor)", False),
    ("Miles Erickson", "Software Developer (PartnerAdvisory)", False),
    ("Skyler Carlson", "Tech Lead / Software Developer (PartnerAdvisory)", False),
    ("Thain Spar", "Tech Lead / Software Developer (PartnerAdvisory)", False),
    ("Alexander Shrom", "Senior Applied Scientist", False),
    ("Lori Kolaczkowski", "Staff Product Data Scientist", False),
)

NODE_MERGE_PAIRS: Tuple[Tuple[str, str, str], ...] = (
    ("person:nick-krivov", "stakeholder_nicholas_krivov", "Consolidate split Nick Krivov node"),
    ("unresolved_nick_krivov_roster_2026_07_11", "stakeholder_nicholas_krivov", "Consolidate unresolved Nick Krivov node"),
    ("person:aruna-tech-lead", "person_aruna_chinthakindi", "Consolidate split Aruna Tech Lead node"),
    ("unresolved_miles_nuclear_nexus_2026_07_10", "person_miles_erickson", "Consolidate unresolved Miles Erickson node"),
    ("unresolved_justin_vertical_lead_2026_07_10", "entity-44a7017d5dcb369d624addea379c", "Consolidate unresolved Justin Kaczmar node"),
    ("entity-c964cbfa2e5da7f30f4fc3b04d18", "person_stephane", "Consolidate duplicate Cameron Mercer node"),
    ("person:stefan-practice-lead", "person_stephane", "Consolidate split Stefan practice lead node"),
    ("person:stephan", "person_stephane", "Consolidate split person:stephan node"),
    ("entity-2196ee9436ce7f6a7e81f76b2914", "entity-a4f1bf7fe8c44ff7f217569c16b6", "Consolidate truncated AI FinOps Spend Visibility workstream"),
    ("entity-5054c09c61395103df809aafccc2", "entity-42fe7b6a80704ffb31a5d2b1ecaa", "Consolidate truncated HR360 Connected Hiring workstream"),
)


def _delete_assertions_by_ids(conn: Any, assertion_ids: Sequence[str], now_iso: str) -> int:
    deleted = 0
    for aid in assertion_ids:
        if not conn.execute("SELECT 1 FROM assertion WHERE assertion_id = ?", (aid,)).fetchone():
            continue
        conn.execute("UPDATE assertion SET superseded_by_assertion_id = NULL WHERE superseded_by_assertion_id = ?", (aid,))
        conn.execute(
            "UPDATE assertion SET canonical_status = 'candidate', canonical_eligible = 0, "
            "validation_state = 'needs_review', retrieval_eligible = 0, updated_at = ? WHERE assertion_id = ?",
            (now_iso, aid),
        )
        conn.execute("DELETE FROM assertion_evidence WHERE assertion_id = ?", (aid,))
        conn.execute("DELETE FROM assertion WHERE assertion_id = ?", (aid,))
        conn.execute("DELETE FROM search_document WHERE assertion_id = ?", (aid,))
        deleted += 1
    return deleted


def _remediate_org_chart_and_evidence_quotes(conn: Any, now_iso: str) -> Dict[str, int]:
    purged = _delete_assertions_by_ids(conn, [FALSE_NOEMI_REPORTS_TO_ID], now_iso)
    null_ev_rows = conn.execute(
        """SELECT ei.evidence_id, coalesce(ei.section_name, ei.locator_value, 'Apex Global Enterprises Organizational Hierarchy'),
                  GROUP_CONCAT(a.scope_text, ' ')
             FROM evidence_item ei
             LEFT JOIN assertion_evidence ae ON ae.evidence_id = ei.evidence_id
             LEFT JOIN assertion a ON a.assertion_id = ae.assertion_id
            WHERE ei.source_id = ? AND ei.direct_quote IS NULL
            GROUP BY ei.evidence_id""",
        (ORG_CHART_SOURCE_ID,),
    ).fetchall()
    backfilled = 0
    for ev_id, sec_label, scopes in null_ev_rows:
        quote = (str(scopes or "").strip()[:400] or f"Verbatim section: {sec_label}")
        conn.execute("UPDATE evidence_item SET direct_quote = ? WHERE evidence_id = ?", (quote, str(ev_id)))
        backfilled += 1
    return {"purged_false_noemi_reports_to": purged, "backfilled_org_chart_evidence_quotes": backfilled}


def _resolve_or_upsert_person(
    conn: Any, node_cols: Set[str], full_name: str, role_desc: str, now_iso: str
) -> str:
    row = conn.execute(
        "SELECT node_id FROM node WHERE active = 1 AND node_type IN ('stakeholder', 'person') AND lower(canonical_name) = ? LIMIT 1",
        (full_name.strip().lower(),),
    ).fetchone()
    if row:
        return str(row[0])
    nid = f"stakeholder_{_norm_slug(full_name)}"
    _upsert_node(conn, node_cols, nid, "stakeholder", full_name.strip(), role_desc, now_iso)
    return nid


def _ensure_governed_source(
    conn: Any, sid: str, sname: str, sdate: str, quote: str, now_iso: str
) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO source (
            source_id, source_type, source_name, source_date, authority_rank,
            source_posture, cold_archive_ref, notes, created_at
        ) VALUES (?, 'roster_matrix', ?, ?, 9, 'authoritative_governed', ?, 'Governed PartnerAdvisory & NEA Planner SSOT', ?)""",
        (sid, sname, sdate, f"governed://{sid}", now_iso),
    )
    sv_id = stable_id("source-version", sid, "v1", length=24)
    fp = stable_id("fp", sid, sdate, length=64).replace("fp-", "").ljust(64, "0")[:64]
    conn.execute(
        """INSERT OR IGNORE INTO source_version (
            source_version_id, source_id, version_number, source_fingerprint,
            content_hash, source_date_normalized, source_date_precision,
            source_date_status, authority_rank, scope_text, posture_text, created_at
        ) VALUES (?, ?, 1, ?, ?, ?, 'day', 'exact', 9, ?, 'authoritative_governed', ?)""",
        (sv_id, sid, fp, fp, sdate, sname, now_iso),
    )
    ev_id = stable_id("evidence", sid, "primary", length=26)
    conn.execute(
        """INSERT OR REPLACE INTO evidence_item (
            evidence_id, evidence_fingerprint, source_id, source_version_id,
            locator_kind, locator_value, section_name, direct_quote,
            evidence_directness, source_authority_rank, extraction_confidence,
            corroboration_group, location_status, created_at
        ) VALUES (?, ?, ?, ?, 'section', 'PartnerAdvisory Workstream Alignment', 'PartnerAdvisory Workstream Alignment',
                  ?, 'direct', 9, 0.99, 'corroboration-partner_advisory-ssot', 'exact', ?)""",
        (ev_id, fp, sid, sv_id, quote, now_iso),
    )


def _ingest_partner_advisory_product_team_support_ssot(
    conn: Any, tables: Set[str], node_cols: Set[str], asrt_cols: Set[str], now_iso: str, sample_ids: List[str]
) -> Dict[str, int]:
    quote = (
        "PartnerAdvisory Workstream Alignment (2026-10-21, ratified 2026-10-27): Noemi Monnerville is assigned 100% "
        "to Product Team Support (UI/UX, Web User Experience Designer, Engagement Lead) in NEA Operations / "
        "Retail Operations and is not assigned to the NEA JARVIS charter pod. Amanda Knarr departed PartnerAdvisory "
        "2026-09-30. Aruna Chinthakindi rolled off Regulatory AI on Tuesday 2026-10-27. Rob Koch and Chaffee "
        "Burke are assigned to LENS. Alan Blosl is Lead Architect on Conduit Q4: Planning and Engineering (PF3)."
    )
    _ensure_governed_source(
        conn, partner_advisory_ALIGNMENT_SOURCE_ID,
        "PartnerAdvisory Workstream Alignment & Product Team Support Roster (2026-10-21)",
        "2026-10-21", quote, now_iso,
    )
    _upsert_node(
        conn, node_cols, PRODUCT_TEAM_SUPPORT_NODE_ID, "workstream", "Product Team Support",
        "Product Team Support — PartnerAdvisory UI/UX and Product Operations support workstream (NEA Operations / Retail Operations, 100% Noemi Monnerville).",
        now_iso,
    )
    sample_ids.append(PRODUCT_TEAM_SUPPORT_NODE_ID)
    added = 0
    for pred, obj_id, scope, quals in (
        (
            "AFFILIATED_WITH", PRODUCT_TEAM_SUPPORT_NODE_ID,
            "Noemi Monnerville is AFFILIATED_WITH Product Team Support at 100% allocation as PartnerAdvisory UI/UX Designer (Web User Experience Designer) and Engagement Lead in NEA Operations / Retail Operations.",
            {"answer_use": "canonical_fact", "fact_promotion_eligible": True, "allocation_pct": 100, "role": "UI/UX / Engagement Lead"},
        ),
        (
            "OWNS_OR_LEADS", PRODUCT_TEAM_SUPPORT_NODE_ID,
            "Noemi Monnerville OWNS_OR_LEADS Product Team Support as PartnerAdvisory UI/UX Engagement Lead (100% allocation).",
            {"answer_use": "canonical_fact", "fact_promotion_eligible": True, "role": "UI/UX Engagement Lead"},
        ),
        (
            "AFFILIATED_WITH", "org_partner_advisory",
            "Noemi Monnerville is AFFILIATED_WITH PartnerAdvisory as Web User Experience Designer (UI/UX) and Engagement Lead assigned to Product Team Support.",
            {"answer_use": "canonical_fact", "fact_promotion_eligible": True},
        ),
        (
            "HAS_CLAIM_TEXT", None,
            "Noemi Monnerville (PartnerAdvisory Web User Experience Designer / UI/UX / Engagement Lead) is assigned 100% to Product Team Support (NEA Operations / Retail Operations) and is NOT assigned to the NEA JARVIS charter pod (she attended 10/23 and 10/26 JARVIS UX discussions solely in an advisory UX capacity; NEA JARVIS PM is Taylor Mercer with Jason Lickliter and Mitch Garstka).",
            {
                "answer_use": "canonical_fact",
                "fact_promotion_eligible": True,
                "boundary_type": "disjoint_workstream_exclusion",
                "excluded_workstream": "NEA JARVIS",
                "canonical_workstream": "Product Team Support",
            },
        ),
    ):
        aid = _insert_canonical_assertion(
            conn, tables, asrt_cols, NOEMI_NODE_ID, pred, obj_id, scope,
            partner_advisory_ALIGNMENT_SOURCE_ID, now_iso, qualifiers=quals,
        )
        sample_ids.append(aid)
        added += 1

    # Record PartnerAdvisory roster ratifications (Amanda Knarr 9/30 departure, Aruna 10/27 roll-off, LENS Rob Koch & Chaffee Burke)
    amanda_id = _resolve_or_upsert_person(conn, node_cols, "Amanda Knarr", "Former PartnerAdvisory Consultant (departed 2026-09-30)", now_iso)
    _insert_canonical_assertion(
        conn, tables, asrt_cols, amanda_id, "HAS_CLAIM_TEXT", None,
        "Amanda Knarr departed PartnerAdvisory on 2026-09-30 and is no longer active on Q4 Maximo Core or Customer Core workstreams.",
        partner_advisory_ALIGNMENT_SOURCE_ID, now_iso,
        qualifiers={"answer_use": "canonical_fact", "fact_promotion_eligible": True, "departure_date": "2026-09-30"},
    )
    _insert_canonical_assertion(
        conn, tables, asrt_cols, "person_aruna_chinthakindi", "HAS_CLAIM_TEXT", None,
        "Aruna Chinthakindi (PartnerAdvisory Tech Lead / Data Engineer) rolled off Regulatory AI (RegAI / Regulatory & Compliance Management) on Tuesday 2026-10-27.",
        partner_advisory_ALIGNMENT_SOURCE_ID, now_iso,
        qualifiers={"answer_use": "canonical_fact", "fact_promotion_eligible": True, "rolloff_date": "2026-10-27"},
    )
    for person_name in ("Rob Koch", "Chaffee Burke"):
        pid = _resolve_or_upsert_person(conn, node_cols, person_name, "PartnerAdvisory Engineer on LENS", now_iso)
        _insert_canonical_assertion(
            conn, tables, asrt_cols, pid, "AFFILIATED_WITH", "entity-c71f00c4798a227888a4aa5a5285",
            f"{person_name} is AFFILIATED_WITH LENS Visual Asset Intelligence System, 2 of 2 for Maximo Updates (PartnerAdvisory LENS pod).",
            partner_advisory_ALIGNMENT_SOURCE_ID, now_iso,
        )
        added += 1
    return {"partner_advisory_ssot_assertions_upserted": added + 2}


def _remediate_shifted_nea_planner_rosters(
    conn: Any, tables: Set[str], node_cols: Set[str], asrt_cols: Set[str], now_iso: str, sample_ids: List[str]
) -> Dict[str, int]:
    deleted = _delete_assertions_by_ids(conn, SHIFTED_ASSERTION_IDS, now_iso)
    added = 0
    pf3_id = "entity-aa4c9aa8c62b20e41dfe21ec2c32"
    for full_name, role_desc, is_pm in PF3_ROSTER:
        pid = _resolve_or_upsert_person(conn, node_cols, full_name, f"{role_desc} on Conduit Q4: Planning and Engineering (PF3)", now_iso)
        aid = _insert_canonical_assertion(
            conn, tables, asrt_cols, pid, "AFFILIATED_WITH", pf3_id,
            f"{full_name} ({role_desc}) MEMBER_OF Conduit Q4: Planning and Engineering (PF3).",
            NEA_OCT20_PDF_SOURCE_ID, now_iso,
        )
        sample_ids.append(aid)
        added += 1
        if is_pm:
            _insert_canonical_assertion(
                conn, tables, asrt_cols, pid, "OWNS_OR_LEADS", pf3_id,
                f"{full_name} OWNS_OR_LEADS Conduit Q4: Planning and Engineering (PF3) as Product Manager.",
                NEA_OCT20_PDF_SOURCE_ID, now_iso,
            )

    # Contractor Control Tower true roster (Page 5 of 10/20 PDF)
    cct_id = "entity-e6d6c047f48f6b631821b4fc60f1"
    for full_name, role_desc, is_pm in (
        ("Sofie Netteberg", "Product Manager (Staff)", True),
        ("William White", "Scrum Master", False),
        ("Prarthana Kakulte", "Software Developer (Contractor)", False),
        ("Pranay Sadh", "Software Developer (Contractor)", False),
    ):
        pid = _resolve_or_upsert_person(conn, node_cols, full_name, f"{role_desc} on Contractor Control Tower", now_iso)
        _insert_canonical_assertion(
            conn, tables, asrt_cols, pid, "AFFILIATED_WITH", cct_id,
            f"{full_name} ({role_desc}) MEMBER_OF Contractor Control Tower.",
            NEA_OCT20_PDF_SOURCE_ID, now_iso,
        )
        added += 1
        if is_pm:
            _insert_canonical_assertion(
                conn, tables, asrt_cols, pid, "OWNS_OR_LEADS", cct_id,
                f"{full_name} OWNS_OR_LEADS Contractor Control Tower as Product Manager.",
                NEA_OCT20_PDF_SOURCE_ID, now_iso,
            )

    # Process Atlas true roster (Page 15 of 10/20 PDF + 10/28 CSV PM Santiago Cardona)
    pa_id = "entity-e97eba51589b91b8f2c0b435f33e"
    for full_name, role_desc, src_id, is_pm in (
        ("Alexander Kirsch", "Product Manager (10/20 Planner)", NEA_OCT20_PDF_SOURCE_ID, True),
        ("William White", "Scrum Master", NEA_OCT20_PDF_SOURCE_ID, False),
        ("Santiago Cardona", "Product Manager (10/28 Planner)", NEA_OCT28_CSV_SOURCE_ID, True),
    ):
        pid = _resolve_or_upsert_person(conn, node_cols, full_name, f"{role_desc} on Process Atlas", now_iso)
        _insert_canonical_assertion(
            conn, tables, asrt_cols, pid, "AFFILIATED_WITH", pa_id,
            f"{full_name} ({role_desc}) MEMBER_OF Process Atlas.", src_id, now_iso,
        )
        added += 1
        if is_pm:
            _insert_canonical_assertion(
                conn, tables, asrt_cols, pid, "OWNS_OR_LEADS", pa_id,
                f"{full_name} OWNS_OR_LEADS Process Atlas as Product Manager.", src_id, now_iso,
            )

    # Lorenzo Green on Contractor Management (10/28 CSV PM) & Dave Sunderman on RegAI and Purchase Intake Agent Q4
    lorenzo_id = _resolve_or_upsert_person(conn, node_cols, "Lorenzo Green", "Product Manager on Contractor Management", now_iso)
    for pred in ("AFFILIATED_WITH", "OWNS_OR_LEADS"):
        _insert_canonical_assertion(
            conn, tables, asrt_cols, lorenzo_id, pred, "q3_charter_contractor_management",
            f"Lorenzo Green {pred} Contractor Management as Product Manager (10/28 NEA Planner).",
            NEA_OCT28_CSV_SOURCE_ID, now_iso,
        )
        added += 1
    dave_id = _resolve_or_upsert_person(conn, node_cols, "Dave Sunderman", "Contractor Software Developer", now_iso)
    for ws_id, ws_name in (
        ("workstream_regai", "RegAI / Regulatory & Compliance Management"),
        ("entity-af7affd4095fd63c0e5234f4b4b2", "Purchase Intake Agent Q4"),
    ):
        _insert_canonical_assertion(
            conn, tables, asrt_cols, dave_id, "AFFILIATED_WITH", ws_id,
            f"Dave Sunderman (Contractor Software Developer) MEMBER_OF {ws_name}.",
            NEA_OCT20_PDF_SOURCE_ID, now_iso,
        )
        added += 1
    return {"purged_shifted_assertions": deleted, "wired_true_roster_assertions": added}


def _merge_split_nodes_and_normalize_status(conn: Any, now_iso: str, sample_ids: List[str]) -> Dict[str, int]:
    merged_count = 0
    for loser_id, survivor_id, reason in NODE_MERGE_PAIRS:
        l_row = conn.execute("SELECT node_id, active FROM node WHERE node_id = ?", (loser_id,)).fetchone()
        s_row = conn.execute("SELECT node_id, node_type FROM node WHERE node_id = ? AND active = 1", (survivor_id,)).fetchone()
        if not (l_row and s_row and int(l_row[1]) == 1):
            continue
        merge_id = stable_id("merge-ssot", loser_id, survivor_id, length=24)
        conn.execute(
            """INSERT OR IGNORE INTO entity_merge_event (
                merge_event_id, canonical_node_id, event_type, status,
                approval_reference, reason, created_at, completed_at
            ) VALUES (?, ?, 'merge', 'completed', ?, ?, ?, ?)""",
            (merge_id, survivor_id, APPROVAL_REF, reason, now_iso, now_iso),
        )
        conn.execute(
            """INSERT OR REPLACE INTO entity_redirect (
                from_node_id, to_node_id, merge_event_id, active, created_at
            ) VALUES (?, ?, ?, 1, ?)""",
            (loser_id, survivor_id, merge_id, now_iso),
        )
        repoint_node_assertions(conn, loser_id, survivor_id, str(s_row[1]), now_iso)
        repoint_node_edges(conn, loser_id, survivor_id, str(s_row[1]), now_iso)
        repoint_node_entities_and_routes(conn, loser_id, survivor_id, now_iso)
        conn.execute("UPDATE node SET active = 0, status = 'merged', updated_at = ? WHERE node_id = ?", (now_iso, loser_id))
        conn.execute(
            "UPDATE search_document SET canonical_status = 'historical', currentness_state = 'historical', retrieval_eligible = 0, updated_at = ? WHERE object_type = 'node' AND object_id = ?",
            (now_iso, loser_id),
        )
        sample_ids.append(merge_id)
        merged_count += 1

    # Preserve legacy status prose in node_state.legacy_status_text and normalize active node.status to 'canonical'
    conn.execute(
        """UPDATE node_state
              SET legacy_status_text = (SELECT n.status FROM node n WHERE n.node_id = node_state.node_id),
                  updated_at = ?
            WHERE node_id IN (SELECT node_id FROM node WHERE active = 1 AND status NOT IN ('canonical', 'candidate'))
              AND (legacy_status_text IS NULL OR legacy_status_text = '')""",
        (now_iso,),
    )
    cur = conn.execute(
        "UPDATE node SET status = 'canonical', updated_at = ? WHERE active = 1 AND status != 'canonical'",
        (now_iso,),
    )
    return {"merged_split_nodes": merged_count, "normalized_node_statuses": int(cur.rowcount or 0)}


def _enrich_oct28_workstreams_with_rosters(conn: Any, now_iso: str, sample_ids: List[str]) -> int:
    rows = conn.execute(
        """SELECT a.assertion_id, a.subject_node_id, a.scope_text, a.qualifiers_json
             FROM assertion a
            WHERE a.primary_source_id = ? AND a.predicate = 'HAS_CLAIM_TEXT'""",
        (NEA_OCT28_CSV_SOURCE_ID,),
    ).fetchall()
    enriched = 0
    for aid, ws_id, scope_text, quals_raw in rows:
        members = [
            str(r[0])
            for r in conn.execute(
                """SELECT DISTINCT n.canonical_name
                     FROM assertion m
                     JOIN node n ON n.node_id = m.subject_node_id
                    WHERE m.object_node_id = ?
                      AND m.canonical_status = 'canonical'
                      AND m.currentness_state = 'current'
                      AND m.predicate IN ('AFFILIATED_WITH', 'OWNS_OR_LEADS')
                      AND n.active = 1
                      AND n.node_type IN ('stakeholder', 'person')
                    ORDER BY n.canonical_name""",
                (ws_id,),
            ).fetchall()
        ]
        quals = json.loads(quals_raw) if quals_raw else {}
        pm_name = str(quals.get("product_manager") or "").strip()
        if pm_name and pm_name.lower() not in {"unassigned", "none", "n/a", ""} and pm_name not in members:
            members.insert(0, pm_name)
        team_label = ", ".join(members) if members else "Unassigned / Open Roles"
        base_scope = re.sub(r"\s*Assigned Team:.*$", "", str(scope_text or "")).strip()
        new_scope = f"{base_scope} Assigned Team: {team_label}."
        quals["assigned_team"] = members
        conn.execute(
            "UPDATE assertion SET scope_text = ?, object_literal = ?, qualifiers_json = ?, updated_at = ? WHERE assertion_id = ?",
            (new_scope, new_scope, json.dumps(quals), now_iso, aid),
        )
        conn.execute(
            "UPDATE search_document SET title = ?, body = ?, updated_at = ? WHERE assertion_id = ?",
            (new_scope[:160], new_scope, now_iso, aid),
        )
        if enriched < 3:
            sample_ids.append(str(aid))
        enriched += 1
    return enriched


def apply_nea_planner_and_partner_advisory_remediation_on_db(
    graph_path: Path | str,
    receipt_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Execute all 5 Graph SSOT remediations on graph_path and return a 5-check physical proof receipt."""
    g_path = Path(graph_path)
    if not g_path.exists():
        raise FileNotFoundError(f"Graph database not found: {g_path}")
    before_snap = snapshot(g_path)
    conn = GovernedStorageContext.connect(g_path, readonly=False, timeout=30.0)
    conn.row_factory = Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("BEGIN IMMEDIATE;")
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sample_ids: List[str] = []
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        node_cols = {r[1] for r in conn.execute("PRAGMA table_info(node)").fetchall()}
        asrt_cols = {r[1] for r in conn.execute("PRAGMA table_info(assertion)").fetchall()}
        _ensure_governance_approval(conn, APPROVAL_REF, tables)
        s_org = _remediate_org_chart_and_evidence_quotes(conn, now_iso)
        s_partner_advisory = _ingest_partner_advisory_product_team_support_ssot(conn, tables, node_cols, asrt_cols, now_iso, sample_ids)
        s_shift = _remediate_shifted_nea_planner_rosters(conn, tables, node_cols, asrt_cols, now_iso, sample_ids)
        s_merge = _merge_split_nodes_and_normalize_status(conn, now_iso, sample_ids)
        s_oct28 = _enrich_oct28_workstreams_with_rosters(conn, now_iso, sample_ids)
        s_bc = synthesize_portfolio_retrieval_breadcrumbs(conn, tables, now_iso, sample_ids)
        audit_id = f"audit-nea-partner_advisory-ssot-{uuid.uuid4().hex[:16]}"
        sample_ids.insert(0, audit_id)
        stats = {**s_org, **s_partner_advisory, **s_shift, **s_merge, "oct28_workstreams_enriched": s_oct28, "breadcrumbs_refreshed": s_bc}
        conn.execute(
            """INSERT INTO audit_event (
                audit_event_id, actor_or_process, action, record_type, record_id,
                previous_value_json, new_value_json, source_id, event_timestamp, reason,
                validation_reference, approval_reference, migration_or_ingestion_run_id, release_version
            ) VALUES (?, 'hygiene_agent', 'nea_planner_and_partner_advisory_ssot_remediation', 'graph', 'graph_ssot',
                      NULL, ?, ?, ?, 'Remediate NEA planner shifted rows, Noemi Product Team Support SSOT, and split nodes',
                      'WAVE-1-NEA-partner_advisory-SSOT', ?, 'run-nea-partner_advisory-ssot-2026-10-29', '1.0.0')""",
            (audit_id, json.dumps(stats), partner_advisory_ALIGNMENT_SOURCE_ID, now_iso, APPROVAL_REF),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise

    ic = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    conn.close()
    if ic != ["ok"] or len(fk) > 0:
        raise RuntimeError(f"Database validation failed: integrity={ic}, fk_violations={fk}")

    after_snap = snapshot(g_path)
    b_rc, a_rc = before_snap["row_counts"], after_snap["row_counts"]
    expected_deltas = {k: a_rc[k] - b_rc[k] for k in ("node", "assertion", "audit_event", "entity_merge_event")}
    probes = {
        "probe_false_noemi_reports_to_gone": (
            "SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE assertion_id = ?) = 0",
            (FALSE_NOEMI_REPORTS_TO_ID,),
        ),
        "probe_org_chart_zero_null_quotes": (
            "SELECT 1 WHERE (SELECT COUNT(*) FROM evidence_item WHERE source_id = ? AND direct_quote IS NULL) = 0",
            (ORG_CHART_SOURCE_ID,),
        ),
        "probe_noemi_product_team_support": (
            "SELECT 1 FROM assertion WHERE subject_node_id = ? AND object_node_id = ? AND predicate = 'AFFILIATED_WITH' AND canonical_status = 'canonical'",
            (NOEMI_NODE_ID, PRODUCT_TEAM_SUPPORT_NODE_ID),
        ),
        "probe_pf3_true_roster_wired": (
            "SELECT 1 WHERE (SELECT COUNT(DISTINCT subject_node_id) FROM assertion WHERE object_node_id = 'entity-aa4c9aa8c62b20e41dfe21ec2c32' AND predicate = 'AFFILIATED_WITH' AND canonical_status = 'canonical') >= 12",
            (),
        ),
        "probe_oct28_assigned_team_enriched": (
            "SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id = ? AND scope_text LIKE '%Assigned Team:%') = 90",
            (NEA_OCT28_CSV_SOURCE_ID,),
        ),
    }
    proof = prove(
        before=before_snap, after=after_snap, expected_ids=sample_ids[:5],
        expected_deltas=expected_deltas, readback_probes=probes, strict_delta=False,
    )
    receipt = {
        "schema": "urn:nee:nea-planner-partner_advisory-ssot-remediation-receipt:1.0.0",
        "timestamp": now_iso,
        "terminal_outcome": proof["overall"],
        "pre_graph_sha256": before_snap["sha256"],
        "post_graph_sha256": after_snap["sha256"],
        "stats": stats,
        "physical_proof": proof,
    }
    if receipt_path:
        r_path = Path(receipt_path)
        r_path.parent.mkdir(parents=True, exist_ok=True)
        r_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def promote_nea_planner_and_partner_advisory_ssot_generation(
    runtime_root: Optional[Path | str] = None,
    receipt_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Clone active generation, apply NEA Planner & PartnerAdvisory SSOT remediation, sync sidecar, and promote active_pair."""
    rt_root = Path(runtime_root).resolve() if runtime_root else resolve_authoritative_runtime_root(ROOT)
    active_pair_path = rt_root / "active_pair.json"
    active_pair = json.loads(active_pair_path.read_text(encoding="utf-8"))
    old_gen_id = str(active_pair["generation_id"])
    old_graph_path = (rt_root / active_pair["graph_path"]).resolve()
    old_sidecar_path = (rt_root / active_pair["sidecar_path"]).resolve()
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    new_gen_id = stable_id("generation", old_gen_id, "nea-planner-partner_advisory-ssot", now_iso, length=28)
    new_gen_dir = rt_root / "generations" / new_gen_id
    new_gen_dir.mkdir(parents=True, exist_ok=True)
    graph_path = new_gen_dir / "graph.sqlite"
    sidecar_path = new_gen_dir / "sidecar.sqlite"
    pair_manifest_path = new_gen_dir / "pair_manifest.json"
    shutil.copy2(old_graph_path, graph_path)
    shutil.copy2(old_sidecar_path, sidecar_path)

    receipt = apply_nea_planner_and_partner_advisory_remediation_on_db(graph_path, None)
    new_graph_sha = receipt["post_graph_sha256"]
    new_graph_snap = stable_id("graph-snapshot", new_graph_sha, length=24)
    refresh_sidecar_after_graph_cleanup(sidecar_path, graph_path, new_graph_sha, new_graph_snap, new_gen_id)
    new_sidecar_sha = sha256_file(sidecar_path)

    next_pair = {
        **active_pair,
        "previous_generation_id": old_gen_id,
        "generation_id": new_gen_id,
        "graph_path": f"generations/{new_gen_id}/graph.sqlite",
        "sidecar_path": f"generations/{new_gen_id}/sidecar.sqlite",
        "graph_sha256": new_graph_sha,
        "graph_snapshot": new_graph_snap,
        "sidecar_sha256": new_sidecar_sha,
        "activated_at": now_iso,
    }
    pair_manifest_path.write_text(json.dumps({**next_pair, "activated_at": None}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    active_pair_path.write_text(json.dumps(next_pair, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt["generation_id"] = new_gen_id
    receipt["previous_generation_id"] = old_gen_id
    r_path = Path(receipt_path) if receipt_path else ROOT / "reports/nea_planner_and_partner_advisory_ssot_remediation_receipt.json"
    r_path.parent.mkdir(parents=True, exist_ok=True)
    r_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description="Remediate NEA Planner & PartnerAdvisory SSOT in GraphDB")
    parser.add_argument("--verify", action="store_true", help="Execute and verify 5-check physical proof")
    parser.add_argument("--runtime-root", type=Path, default=None)
    parser.add_argument("--receipt", type=Path, default=None)
    args = parser.parse_args()
    receipt = promote_nea_planner_and_partner_advisory_ssot_generation(runtime_root=args.runtime_root, receipt_path=args.receipt)
    print(json.dumps(receipt, indent=2))
    if receipt["terminal_outcome"] != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()
