"""NEA Q4 2026 Charter Mission, NAV Budget, NEA JARVIS, and 44 Open/Future-Hire Role Enrichment."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.core import ROOT, stable_id
from tools.tier2_dedup_core.nea_portfolio_mece_enrichment import enrich_nea_portfolio_mece_hierarchy

FULL_NEA_SOURCE_ID = "source-95ce3df4ff5348b9fb6eb264"
CATALOG_REL_PATH = Path("reports/visualizations/nea_q4_charters_and_open_roles_catalog.json")


def _norm_alnum(val: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (val or "").lower())


_ALLOWED_PRAGMA_TABLES = {
    "source_version": "PRAGMA table_info(source_version)",
    "extraction_run": "PRAGMA table_info(extraction_run)",
    "evidence_item": "PRAGMA table_info(evidence_item)",
    "node_type_registry": "PRAGMA table_info(node_type_registry)",
    "node_state": "PRAGMA table_info(node_state)",
    "node_fts": "PRAGMA table_info(node_fts)",
    "assertion": "PRAGMA table_info(assertion)",
    "assertion_evidence": "PRAGMA table_info(assertion_evidence)",
}


def _has_col(conn: Any, table: str, col: str) -> bool:
    pragma_sql = _ALLOWED_PRAGMA_TABLES.get(table)
    if not pragma_sql or not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        return False
    return col in {r[1] for r in conn.execute(pragma_sql).fetchall()}


def _load_nea_q4_catalog() -> List[Dict[str, Any]]:
    for base in (Path(__file__).resolve().parents[2], ROOT):
        cand = base / CATALOG_REL_PATH
        if cand.is_file():
            return json.loads(cand.read_text(encoding="utf-8"))
    return []


def _table_cols(conn: Any, table: str) -> set[str]:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        return set()
    clean_table = re.sub(r"[^a-zA-Z0-9_]", "", table)
    sql_pragma = f"PRAGMA table_info({clean_table})"
    return {r[1] for r in conn.execute(sql_pragma).fetchall()}


def _insert_filtered(conn: Any, table: str, vals: Dict[str, Any]) -> None:
    cols = _table_cols(conn, table)
    present = [(k, v) for k, v in vals.items() if k in cols]
    if present:
        clean_table = re.sub(r"[^a-zA-Z0-9_]", "", table)
        col_names = ", ".join(re.sub(r"[^a-zA-Z0-9_]", "", k) for k, _ in present)
        placeholders = ", ".join("?" for _ in present)
        sql_insert = f"INSERT OR IGNORE INTO {clean_table} ({col_names}) VALUES ({placeholders})"
        conn.execute(sql_insert, [v for _, v in present])


def _ensure_source_provenance(conn: Any, now_iso: str) -> Tuple[str, str]:
    sdate, ts, fp = "2026-10-20", "2026-10-22T05:03:18Z", "4d2533431fc4fe5d006dc6f366ef05eac02373c1298e19060d74bdee32225956"
    _insert_filtered(
        conn, "source",
        {
            "source_id": FULL_NEA_SOURCE_ID, "source_type": "dual_pipeline_intake",
            "source_name": "Document 20261020 Nea Q4 Planning Assignments", "source_date": sdate,
            "authority_rank": 5, "source_posture": "derivative_internal",
            "cold_archive_ref": "output/nea/10-20-2026/Assignments_Nea_Planning/03_Assignments_Nea_Planning_10-20-2026_0927PM_ET_canonical_signal_projection.json",
            "notes": "Governed NEE dual-pipeline source package.", "created_at": ts,
        },
    )
    sv_row = conn.execute("SELECT source_version_id FROM source_version WHERE source_id=? LIMIT 1", (FULL_NEA_SOURCE_ID,)).fetchone() if _has_col(conn, "source_version", "source_version_id") else None
    sv_id = sv_row[0] if sv_row else "source-version-76e7114d619406ec96353107"
    if _has_col(conn, "source_version", "source_version_id") and not sv_row:
        _insert_filtered(
            conn, "source_version",
            {
                "source_version_id": sv_id, "source_id": FULL_NEA_SOURCE_ID, "version_number": 1,
                "source_fingerprint": fp, "content_hash": fp, "source_date_normalized": sdate,
                "source_date_precision": "day", "source_date_status": "exact", "authority_rank": 5,
                "scope_text": "Governed source-bound candidate intake.", "posture_text": "derivative_internal", "created_at": ts,
            },
        )
    er_id = "extraction-run-9fde51b4dea20a11912d1849be8e"
    if _has_col(conn, "extraction_run", "extraction_run_id"):
        conn.execute("INSERT OR IGNORE INTO extraction_run VALUES (?, 'dual-pipeline-structured-adapter-1.0.0', ?, ?, ?, ?, 'completed', 1)", (er_id, fp, fp, ts, ts))
    ev_row = conn.execute("SELECT evidence_id FROM evidence_item WHERE source_id=? ORDER BY evidence_id LIMIT 1", (FULL_NEA_SOURCE_ID,)).fetchone()
    ev_id = ev_row[0] if ev_row else "evidence-nea-q4-planning-20261020"
    if not ev_row:
        ev_fp = stable_id("evfp", FULL_NEA_SOURCE_ID, length=64).replace("evfp-", "").ljust(64, "a")[:64]
        _insert_filtered(
            conn, "evidence_item",
            {
                "evidence_id": ev_id, "evidence_fingerprint": ev_fp, "source_id": FULL_NEA_SOURCE_ID,
                "source_version_id": sv_id, "locator_kind": "section", "locator_value": "## Page 1",
                "direct_quote": "All Workstream Assignments (90) - UNFILLED FTE 30.4 of 404.9",
                "evidence_directness": "direct", "source_authority_rank": 5, "extraction_confidence": 0.90,
                "location_status": "exact" if _has_col(conn, "evidence_item", "source_version_id") else "verified", "created_at": now_iso,
            },
        )
    return er_id, ev_id


def _upsert_node_and_state(
    conn: Any, node_id: str, node_type: str, name: str, desc: str, now_iso: str
) -> bool:
    if _has_col(conn, "node_type_registry", "node_type_id"):
        _insert_filtered(
            conn, "node_type_registry",
            {
                "node_type_id": node_type, "canonical_name": node_type.replace("_", " ").title(),
                "definition": f"Governed {node_type} node.",
                "identity_rule": "Never auto-merge on name similarity; require exact ID or human confirmation.",
                "registry_state": "active", "retrieval_use": "Canonical entity node", "created_at": now_iso,
            },
        )
    row = conn.execute("SELECT node_id FROM node WHERE node_id=?", (node_id,)).fetchone()
    if row:
        conn.execute("UPDATE node SET description=?, status='canonical', active=1, updated_at=? WHERE node_id=?", (desc, now_iso, node_id))
        created = False
    else:
        _insert_filtered(
            conn, "node",
            {
                "node_id": node_id, "node_type": node_type, "canonical_name": name,
                "display_name": name, "description": desc, "status": "candidate",
                "active": 1, "created_at": now_iso, "updated_at": now_iso,
            },
        )
        created = True
    if _has_col(conn, "node_state", "node_id"):
        conn.execute("DELETE FROM node_state WHERE node_id=?", (node_id,))
        _insert_filtered(
            conn, "node_state",
            {
                "node_id": node_id, "lifecycle_state": "active", "validation_state": "accepted_legacy",
                "currentness_state": "current", "approval_state": "approved", "rag_state": "green",
                "resolution_state": "resolved", "retrieval_eligibility": "eligible",
                "publication_state": "internal", "review_state": "reviewed",
                "legacy_status_text": "canonical", "intentional_orphan": 0,
                "intentional_orphan_reason": None, "updated_at": now_iso,
            },
        )
    if _has_col(conn, "node_fts", "node_id"):
        conn.execute("DELETE FROM node_fts WHERE node_id=?", (node_id,))
        _insert_filtered(conn, "node_fts", {"node_id": node_id, "canonical_name": name, "display_name": name, "description": desc})
    return created


def _insert_candidate_assertion(
    conn: Any, aid: str, subj_id: str, pred: str, obj_id: Optional[str],
    obj_lit: Optional[str], scope_text: str, er_id: str, ev_id: str, now_iso: str
) -> bool:
    if conn.execute("SELECT 1 FROM assertion WHERE assertion_id=?", (aid,)).fetchone():
        return False
    afp = stable_id("afp", aid, scope_text, length=64).replace("afp-", "").ljust(64, "b")[:64]
    _insert_filtered(
        conn, "assertion",
        {
            "assertion_id": aid, "assertion_fingerprint": afp, "subject_node_id": subj_id,
            "predicate": pred, "object_node_id": obj_id, "object_literal": obj_lit,
            "object_literal_type": None if obj_id else "text", "assertion_type": "extracted_claim",
            "atomicity_state": "atomic", "scope_text": scope_text,
            "speaker_or_author": "NEA Q4 Planning Dashboard", "primary_source_id": FULL_NEA_SOURCE_ID,
            "extraction_run_id": er_id,
            "extraction_method": "dual_pipeline_structured_adapter" if _has_col(conn, "assertion", "valid_from") else "dual_pipeline_intake",
            "origin_type": "human_source", "confidence_label": "high", "confidence_score": 0.90,
            "validation_state": "needs_review", "currentness_state": "current", "valid_from": "2026-10-20",
            "date_precision": "day", "date_status": "exact", "recorded_at": now_iso,
            "resolution_state": "resolved", "canonical_status": "candidate",
            "canonical_eligible": 0, "retrieval_eligible": 0, "materiality": "high",
            "classification": "internal", "allowed_audiences_json": '["internal_consulting"]',
            "hitl_required": 0, "validation_priority": "P1", "qualifiers_json": "{}",
            "legacy_record_type": "edge" if obj_id else "claim", "legacy_record_ids_json": "[]",
            "created_at": now_iso, "updated_at": now_iso,
        },
    )
    _insert_filtered(
        conn, "assertion_evidence",
        {
            "assertion_id": aid, "evidence_id": ev_id, "evidence_relationship": "supports",
            "independent_corroboration": 0, "created_at": now_iso,
        },
    )
    _insert_filtered(
        conn, "search_document",
        {
            "search_document_id": aid, "object_type": "assertion", "object_id": aid,
            "title": scope_text, "body": scope_text, "canonical_status": "candidate",
            "currentness_state": "current", "retrieval_eligible": 0,
            "source_id": FULL_NEA_SOURCE_ID, "assertion_id": aid, "updated_at": now_iso,
        },
    )
    return True


def _build_ws_lookup(conn: Any) -> Dict[str, str]:
    rows = conn.execute(
        "SELECT node_id, canonical_name FROM node WHERE active=1 AND node_type IN ('workstream', 'program')"
    ).fetchall()
    by_norm = {_norm_alnum(r[1]): str(r[0]) for r in rows}
    if "workstream_customer_ai" in {r[0] for r in rows}:
        by_norm[_norm_alnum("APEX Customer.AI")] = "workstream_customer_ai"
    if "workstream_regai" in {r[0] for r in rows}:
        by_norm[_norm_alnum("Regulatory AI")] = "workstream_regai"
    return by_norm


def _wire_nea_jarvis_edges(conn: Any, ws_id: str, er_id: str, ev_id: str, now_iso: str, sample_ids: List[str]) -> int:
    added = 0
    bindings = [
        ("entity-bcc4f6f3fd5f07116fb96970856b", "org", "TechSales (Reid Schenk)", ws_id, "AFFILIATED_WITH", "entity-bcc4f6f3fd5f07116fb96970856b", "NEA JARVIS MEMBER_OF TechSales (Reid Schenk)."),
        ("entity-100afc4873b2846e1c5aa660ade1", "org", "NEA Operations (Chris Nunalee)", ws_id, "AFFILIATED_WITH", "entity-100afc4873b2846e1c5aa660ade1", "NEA JARVIS MEMBER_OF NEA Operations (Chris Nunalee)."),
        ("entity-4b51afc96e42d5686cb30ff131b7", "stakeholder", "Taylor Mercer", "entity-4b51afc96e42d5686cb30ff131b7", "RELATED_TO", ws_id, "Product Manager Taylor Mercer (Contractor) leads workstream NEA JARVIS ($284K NAV, 100% assigned)."),
        ("entity-4b51afc96e42d5686cb30ff131b7", "stakeholder", "Taylor Mercer", "entity-4b51afc96e42d5686cb30ff131b7", "AFFILIATED_WITH", ws_id, "Taylor Mercer (Contractor) MEMBER_OF NEA JARVIS."),
        ("stakeholder_kaleena_mchugo", "stakeholder", "Kaleena McHugo", "stakeholder_kaleena_mchugo", "AFFILIATED_WITH", ws_id, "Scrum Master Kaleena McHugo MEMBER_OF NEA JARVIS."),
        ("entity-2fc3c71ac731e0e35d72b305dc40", "stakeholder", "Jason Lickliter", "entity-2fc3c71ac731e0e35d72b305dc40", "AFFILIATED_WITH", ws_id, "Jason Lickliter (Contractor) MEMBER_OF NEA JARVIS."),
        ("entity-4552b9a6a09422165c8fc95658d0", "stakeholder", "Mitch Garstka", "entity-4552b9a6a09422165c8fc95658d0", "AFFILIATED_WITH", ws_id, "Mitch Garstka (Contractor) MEMBER_OF NEA JARVIS."),
    ]
    for target_nid, ntype, nlabel, subj, pred, obj, scope in bindings:
        if not conn.execute("SELECT 1 FROM node WHERE node_id=?", (target_nid,)).fetchone():
            _upsert_node_and_state(conn, target_nid, ntype, nlabel, f"{nlabel} in NEA Q4 2026 Planning.", now_iso)
        aid = stable_id("assertion", FULL_NEA_SOURCE_ID, "nea-jarvis", subj, pred, obj, scope, length=28)
        if _insert_candidate_assertion(conn, aid, subj, pred, obj, None, scope, er_id, ev_id, now_iso):
            added += 1
            sample_ids.append(aid)
    return added


def _wire_catalog_hierarchy_edges(
    conn: Any, ws_id: str, wname: str, item: Dict[str, Any], er_id: str, ev_id: str, now_iso: str, sample_ids: List[str]
) -> int:
    added = 0
    prod_name = str(item.get("product") or "").strip()
    if prod_name and prod_name.lower() not in ("n/a", "none", "unassigned", "no associated product"):
        prod_id = f"product_{_norm_alnum(prod_name)}"
        _upsert_node_and_state(conn, prod_id, "product", prod_name, f"NEA Product {prod_name} in {item.get('vertical') or 'NEA'}.", now_iso)
        aid_p = stable_id("assertion", FULL_NEA_SOURCE_ID, "prod-delivers-ws", prod_id, ws_id, length=28)
        if _insert_candidate_assertion(conn, aid_p, prod_id, "DELIVERS", ws_id, None, f"Product {prod_name} DELIVERS Workstream {wname}.", er_id, ev_id, now_iso):
            added += 1
            sample_ids.append(aid_p)
    pm_raw = str(item.get("pm") or "").replace("(Contractor)", "").strip()
    if pm_raw and pm_raw.lower() not in ("n/a", "none", "unassigned", "tbd"):
        pm_id = f"stakeholder_{_norm_alnum(pm_raw)}"
        _upsert_node_and_state(conn, pm_id, "stakeholder", pm_raw, f"Product Manager {pm_raw} for {wname}.", now_iso)
        aid_pm = stable_id("assertion", FULL_NEA_SOURCE_ID, "pm-leads-ws", pm_id, ws_id, length=28)
        if _insert_candidate_assertion(conn, aid_pm, pm_id, "OWNS_OR_LEADS", ws_id, None, f"{pm_raw} OWNS_OR_LEADS Workstream {wname} as Product Manager.", er_id, ev_id, now_iso):
            added += 1
            sample_ids.append(aid_pm)
    return added


def _enrich_single_catalog_item(
    conn: Any, item: Dict[str, Any], ws_lookup: Dict[str, str], er_id: str, ev_id: str, now_iso: str, sample_ids: List[str]
) -> Tuple[int, int]:
    wname = item["name"]
    wnorm = _norm_alnum(wname)
    ws_id = ws_lookup.get(wnorm) or ("workstream_nea_jarvis" if wnorm == "neajarvis" else stable_id("entity", "nea-q4-ws", wnorm, length=28))
    nav_str = f"${item['nav_k']:,}K NAV, " if item.get("has_nav") else "No NAV listed, "
    charter_str = item.get("charter_mission") or "No Associated Charter"
    note_str = f" | Resource Note: {item['resource_note']}" if item.get("resource_note") else ""
    open_list = item.get("open_roles") or []
    open_summary = f" | Open Q4 Roles ({len(open_list)}): " + ", ".join(f"{r['name']} ({r['tier']})" for r in open_list) if open_list else " | Open Q4 Roles: 0"
    desc = (
        f"{wname} ({item['status']} Q4 2026 NEA workstream in {item['vertical']} / {item['dept']}; "
        f"PM: {item['pm'] or 'Unassigned'}, SM: {item['sm'] or 'Unassigned'}; Product: {item['product'] or 'N/A'}; "
        f"Charter Mission: {charter_str}; {nav_str}{item['progress_pct']}% Assigned{note_str}{open_summary})."
    )
    _upsert_node_and_state(conn, ws_id, "workstream", wname, desc, now_iso)
    ws_lookup[wnorm] = ws_id
    role_nodes, role_assertions = 0, _wire_catalog_hierarchy_edges(conn, ws_id, wname, item, er_id, ev_id, now_iso, sample_ids)
    charter_scope = (
        f"Workstream {wname} ({item['vertical']} / {item['dept']}, {item['status']}) has Q4 2026 Charter Mission '{charter_str}', "
        f"{nav_str}{item['progress_pct']}% assignment progress, PM {item['pm'] or 'Unassigned'}, and SM {item['sm'] or 'Unassigned'}{note_str}{open_summary}."
    )
    caid = stable_id("assertion", FULL_NEA_SOURCE_ID, "charter-summary", ws_id, length=28)
    if _insert_candidate_assertion(conn, caid, ws_id, "HAS_CLAIM_TEXT", None, charter_scope, charter_scope, er_id, ev_id, now_iso):
        role_assertions += 1
    if wnorm == "neajarvis":
        role_assertions += _wire_nea_jarvis_edges(conn, ws_id, er_id, ev_id, now_iso, sample_ids)
    for idx, r in enumerate(open_list, 1):
        rname, rtier = r["name"], r["tier"]
        rid = stable_id("entity", "nea-q4-open-role", wnorm, _norm_alnum(rname), str(idx), length=28)
        rlabel = f"{rname} ({wname})" if rname.startswith("Unfilled") else f"{rname} ({rtier} on {wname})"
        rdesc = f"Open Q4 2026 role slot {rname} ({rtier}) on workstream {wname} ({item['vertical']} / {item['dept']}, {item['progress_pct']}% assigned)."
        _upsert_node_and_state(conn, rid, "role_group", rlabel, rdesc, now_iso)
        role_nodes += 1
        sample_ids.append(rid)
        rel_scope = f"Open Q4 2026 role {rname} ({rtier}) is an unfilled capacity slot CANDIDATE_FOR workstream {wname} in {item['vertical']} ({item['dept']}, {item['progress_pct']}% assigned, Charter: {charter_str})."
        raid = stable_id("assertion", FULL_NEA_SOURCE_ID, "open-role-edge", rid, ws_id, length=28)
        if _insert_candidate_assertion(conn, raid, rid, "CANDIDATE_FOR", ws_id, None, rel_scope, er_id, ev_id, now_iso):
            role_assertions += 1
            sample_ids.append(raid)
    return role_nodes, role_assertions


def enrich_nea_q4_charters_and_open_roles(
    conn: Any, now_iso: str, sample_ids: List[str]
) -> Dict[str, int]:
    """Unified NEA charter, 6-tier MECE portfolio hierarchy, 2026-2030 financial, and 44 open-role breakout."""
    mece_stats = enrich_nea_portfolio_mece_hierarchy(conn, now_iso, sample_ids)
    catalog = _load_nea_q4_catalog()
    if not catalog:
        return {"workstreams_enriched": 0, "open_role_nodes_upserted": 0, "open_role_assertions_added": 0, **mece_stats}
    er_id, ev_id = _ensure_source_provenance(conn, now_iso)
    ws_lookup = _build_ws_lookup(conn)
    ws_enriched, role_nodes, role_assertions = 0, 0, 0
    for item in catalog:
        rn, ra = _enrich_single_catalog_item(conn, item, ws_lookup, er_id, ev_id, now_iso, sample_ids)
        ws_enriched += 1
        role_nodes += rn
        role_assertions += ra
    nea_ops_id = "entity-100afc4873b2846e1c5aa660ade1"
    if conn.execute("SELECT 1 FROM node WHERE node_id=?", (nea_ops_id,)).fetchone():
        port_scope = (
            "NEA Q4 2026 Team Assignments & Planning Dashboard portfolio rollup across 90 workstreams: "
            "404.9 Requested People, 376 Assignable People, 425 Assignments, 95% Assigned, "
            "30.4 Unfilled FTE (of 404.9 requested), 42 Unassigned People (of 376 assignable), "
            "and 44 explicit open role slots (26 Future Hire #1-#26 Staff slots and 18 Unfilled role slots across 30 workstreams)."
        )
        paid = stable_id("assertion", FULL_NEA_SOURCE_ID, "portfolio-open-roles-rollup", length=28)
        if _insert_candidate_assertion(conn, paid, nea_ops_id, "HAS_CLAIM_TEXT", None, port_scope, port_scope, er_id, ev_id, now_iso):
            role_assertions += 1
    return {
        "workstreams_enriched": ws_enriched,
        "open_role_nodes_upserted": role_nodes,
        "open_role_assertions_added": role_assertions,
        **mece_stats,
    }
