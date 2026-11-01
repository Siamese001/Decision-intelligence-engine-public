"""Graph-vs-Archive posture accounting and itemized deduplication ledger for Knowledge Graph Hygiene."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set, Tuple


def _get_table_cols(conn: Any, table: str, tables: Set[str]) -> Set[str]:
    if table not in tables:
        return set()
    return {r[1] for r in conn.execute("PRAGMA table_info(" + table + ")").fetchall()}


def _snapshot_node_posture(conn: Any, cols: Set[str]) -> Dict[str, Any]:
    if not cols:
        return {"total": 0, "active_graph": 0, "staging_hold": 0, "historical_archive": 0, "breakdown": {}}
    total = int(conn.execute("SELECT COUNT(*) FROM node").fetchone()[0])
    has_active, has_status = "active" in cols, "status" in cols
    if has_active and has_status:
        active_canon = int(conn.execute("SELECT COUNT(*) FROM node WHERE active = 1 AND coalesce(status, 'canonical') != 'candidate'").fetchone()[0])
        staging = int(conn.execute("SELECT COUNT(*) FROM node WHERE active = 1 AND status = 'candidate'").fetchone()[0])
        merged = int(conn.execute("SELECT COUNT(*) FROM node WHERE active = 0 AND status = 'merged'").fetchone()[0])
        archived_orphan = int(conn.execute("SELECT COUNT(*) FROM node WHERE active = 0 AND coalesce(status, '') != 'merged'").fetchone()[0])
    elif has_active:
        active_canon = int(conn.execute("SELECT COUNT(*) FROM node WHERE active = 1").fetchone()[0])
        staging, merged = 0, 0
        archived_orphan = total - active_canon
    else:
        active_canon, staging, merged, archived_orphan = total, 0, 0, 0
    hist = merged + archived_orphan
    return {
        "total": total,
        "active_graph": active_canon,
        "staging_hold": staging,
        "historical_archive": hist,
        "breakdown": {
            "active_canonical": active_canon,
            "staging_candidate": staging,
            "archived_merged_redirected": merged,
            "archived_retired_or_orphan": archived_orphan,
        },
    }


def _snapshot_assertion_posture(conn: Any, cols: Set[str]) -> Dict[str, Any]:
    if not cols:
        return {"total": 0, "active_graph": 0, "staging_hold": 0, "historical_archive": 0, "breakdown": {}}
    total = int(conn.execute("SELECT COUNT(*) FROM assertion").fetchone()[0])
    c_curr = "coalesce(currentness_state, 'current') = 'current'" if "currentness_state" in cols else "1=1"
    c_canon = "coalesce(canonical_status, 'canonical') = 'canonical'" if "canonical_status" in cols else "1=1"
    c_cand = "canonical_status = 'candidate'" if "canonical_status" in cols else "1=0"
    c_ret1 = "coalesce(retrieval_eligible, 1) = 1" if "retrieval_eligible" in cols else "1=1"

    active_sql = f"SELECT COUNT(*) FROM assertion WHERE {c_curr} AND {c_canon} AND {c_ret1}"
    active_graph = int(conn.execute(active_sql).fetchone()[0])
    staging_sql = f"SELECT COUNT(*) FROM assertion WHERE {c_curr} AND {c_cand} AND NOT ({c_curr} AND {c_canon} AND {c_ret1})"
    staging_hold = int(conn.execute(staging_sql).fetchone()[0])
    historical_archive = max(0, total - active_graph - staging_hold)

    superseded = int(conn.execute("SELECT COUNT(*) FROM assertion WHERE validation_state = 'superseded' OR superseded_by_assertion_id IS NOT NULL").fetchone()[0]) if ("validation_state" in cols and "superseded_by_assertion_id" in cols) else 0
    rejected = int(conn.execute("SELECT COUNT(*) FROM assertion WHERE validation_state = 'rejected'").fetchone()[0]) if "validation_state" in cols else 0
    hist_cand = int(conn.execute("SELECT COUNT(*) FROM assertion WHERE canonical_status = 'historical_candidate'").fetchone()[0]) if "canonical_status" in cols else 0
    other_hist = max(0, historical_archive - superseded - rejected - hist_cand)
    return {
        "total": total,
        "active_graph": active_graph,
        "staging_hold": staging_hold,
        "historical_archive": historical_archive,
        "breakdown": {
            "active_retrievable": active_graph,
            "staging_candidate_hold": staging_hold,
            "archived_superseded_duplicate_or_temporal": superseded,
            "archived_historical_candidate": hist_cand,
            "archived_quarantined_or_rejected": rejected,
            "archived_other_historical": other_hist,
        },
    }


def _snapshot_search_doc_posture(conn: Any, cols: Set[str]) -> Dict[str, Any]:
    if not cols:
        return {"total": 0, "active_graph": 0, "staging_hold": 0, "historical_archive": 0, "breakdown": {}}
    total = int(conn.execute("SELECT COUNT(*) FROM search_document").fetchone()[0])
    c_ret1 = "coalesce(retrieval_eligible, 0) = 1" if "retrieval_eligible" in cols else "1=1"
    c_curr = "coalesce(currentness_state, 'current') = 'current'" if "currentness_state" in cols else "1=1"
    c_cand = "canonical_status = 'candidate'" if "canonical_status" in cols else "1=0"

    active_sql = f"SELECT COUNT(*) FROM search_document WHERE {c_ret1} AND {c_curr}"
    active_graph = int(conn.execute(active_sql).fetchone()[0])
    staging_sql = f"SELECT COUNT(*) FROM search_document WHERE NOT ({c_ret1} AND {c_curr}) AND {c_curr} AND {c_cand}"
    staging_hold = int(conn.execute(staging_sql).fetchone()[0])
    historical_archive = max(0, total - active_graph - staging_hold)
    return {
        "total": total,
        "active_graph": active_graph,
        "staging_hold": staging_hold,
        "historical_archive": historical_archive,
        "breakdown": {
            "active_searchable": active_graph,
            "staging_unindexed": staging_hold,
            "archived_deactivated": historical_archive,
        },
    }


def snapshot_graph_vs_archive_posture(conn: Any) -> Dict[str, Any]:
    """Snapshot mutually exclusive Active Graph vs Staging Hold vs Historical Archive counts across core tables."""
    if conn is None:
        return {"nodes": _snapshot_node_posture(None, set()), "assertions": _snapshot_assertion_posture(None, set()), "search_documents": _snapshot_search_doc_posture(None, set())}
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    n_cols = _get_table_cols(conn, "node", tables)
    a_cols = _get_table_cols(conn, "assertion", tables)
    s_cols = _get_table_cols(conn, "search_document", tables)
    er_cnt = int(conn.execute("SELECT COUNT(*) FROM entity_redirect WHERE active = 1").fetchone()[0]) if ("entity_redirect" in tables and "active" in _get_table_cols(conn, "entity_redirect", tables)) else 0
    return {
        "nodes": _snapshot_node_posture(conn, n_cols),
        "assertions": _snapshot_assertion_posture(conn, a_cols),
        "search_documents": _snapshot_search_doc_posture(conn, s_cols),
        "active_entity_redirects": er_cnt,
    }


def compute_graph_vs_archive_transition(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    """Compute before, after, and delta across Active Graph, Staging Hold, and Historical Archive."""
    delta: Dict[str, Any] = {}
    for domain in ("nodes", "assertions", "search_documents"):
        b_dom = before.get(domain) or {}
        a_dom = after.get(domain) or {}
        delta[domain] = {
            k: int(a_dom.get(k, 0)) - int(b_dom.get(k, 0))
            for k in ("total", "active_graph", "staging_hold", "historical_archive")
        }
    delta["active_entity_redirects"] = int(after.get("active_entity_redirects", 0)) - int(before.get("active_entity_redirects", 0))
    return {"before": before, "after": after, "delta": delta}


def _lookup_node_label(conn: Any, nid: str, fallback: str = "") -> Tuple[str, str]:
    try:
        row = conn.execute("SELECT canonical_name, node_type FROM node WHERE node_id = ?", (nid,)).fetchone()
        if row:
            return str(row[0] or fallback or nid), str(row[1] or "entity")
    except Exception:
        pass
    return (fallback or nid), "entity"


def _lookup_assertion_summary(conn: Any, aid: str, fallback: str = "") -> str:
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(assertion)").fetchall()}
        expr = "coalesce(scope_text, object_literal, predicate, assertion_id)" if {"scope_text", "object_literal"}.issubset(cols) else "assertion_id"
        row = conn.execute("SELECT " + expr + " FROM assertion WHERE assertion_id = ?", (aid,)).fetchone()
        if row and row[0]:
            return str(row[0])
    except Exception:
        pass
    return fallback or aid


def _build_entity_merge_entries(conn: Any, findings: Sequence[Dict[str, Any]], retired_nodes: Set[str]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    seen_retired: Set[str] = set()
    for f in findings:
        if f.get("proposed_fix") != "merge_entities":
            continue
        rd = f.get("raw_data") or {}
        surv_id = str(rd.get("survivor_node_id") or rd.get("node_id_a") or "")
        ret_id = str(rd.get("retired_node_id") or rd.get("node_id_b") or "")
        if not surv_id or not ret_id or surv_id == ret_id or ret_id in seen_retired:
            continue
        if retired_nodes and ret_id not in retired_nodes:
            continue
        seen_retired.add(ret_id)
        surv_name, ntype = _lookup_node_label(conn, surv_id, str(rd.get("canonical_name_a") or surv_id))
        ret_name, _ = _lookup_node_label(conn, ret_id, str(rd.get("canonical_name_b") or ret_id))
        entries.append({
            "dedup_type": "entity_merge",
            "node_type": ntype,
            "kept_in_active_graph": {"node_id": surv_id, "canonical_name": surv_name, "posture": "ACTIVE_GRAPH (active=1, status=canonical)"},
            "moved_to_archive": {"node_id": ret_id, "canonical_name": ret_name, "posture": "HISTORICAL_ARCHIVE (active=0, status=merged, redirect_to=" + surv_id + ")"},
            "alias_and_redirect_preserved": True,
        })
    return entries


def _build_assertion_dedup_entries(
    conn: Any, findings: Sequence[Dict[str, Any]], extra_dups: Sequence[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    seen_losers: Set[str] = set()
    raw_items: List[Dict[str, Any]] = [
        f.get("raw_data") or {} for f in findings
        if f.get("proposed_fix") == "deduplicate_assertions"
        or (f.get("proposed_fix") == "supersede_assertion" and "loser_id" in (f.get("raw_data") or {}))
    ] + list(extra_dups or [])
    for rd in raw_items:
        w_id, l_id = str(rd.get("winner_id") or ""), str(rd.get("loser_id") or "")
        if not w_id or not l_id or w_id == l_id or l_id in seen_losers:
            continue
        seen_losers.add(l_id)
        w_claim = _lookup_assertion_summary(conn, w_id, str(rd.get("winner_claim") or w_id))
        l_claim = _lookup_assertion_summary(conn, l_id, str(rd.get("loser_claim") or l_id))
        entries.append({
            "dedup_type": "assertion_deduplication",
            "kept_in_active_graph": {"assertion_id": w_id, "claim_text": w_claim, "posture": "ACTIVE_GRAPH (currentness_state=current, retrieval_eligible=1)"},
            "moved_to_archive": {"assertion_id": l_id, "claim_text": l_claim, "posture": "HISTORICAL_ARCHIVE (currentness_state=historical, validation_state=superseded, retrieval_eligible=0, superseded_by=" + w_id + ")"},
            "similarity": float(rd.get("similarity") or 1.0),
            "evidence_transferred_to_survivor": True,
        })
    return entries


_ARCHIVE_ACTION_SET = frozenset({
    "archive_candidate_assertion",
    "demote_leaked_candidate_assertion",
    "quarantine_cross_project_assertion",
    "quarantine_disjoint_role_or_owner_bleed",
    "reconcile_stale_role_and_description",
    "reconcile_evidence",
    "escalate_90d_revalidation",
    "archive_node",
})


def _extract_archive_target_ids(f: Dict[str, Any], rd: Dict[str, Any]) -> List[str]:
    if rd.get("ungrounded_assertion_ids"):
        return [str(x) for x in rd["ungrounded_assertion_ids"] if x][:50]
    if rd.get("assertion_id"):
        return [str(rd["assertion_id"])]
    return [str(x) for x in (f.get("entity_references") or []) if x][:50]


def _build_temporal_and_archive_entries(
    conn: Any, findings: Sequence[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    temporal: List[Dict[str, Any]] = []
    archived: List[Dict[str, Any]] = []
    for f in findings:
        fix = str(f.get("proposed_fix") or "")
        rd = f.get("raw_data") or {}
        if fix == "supersede_assertion" and rd.get("assertion_id_older") and rd.get("assertion_id_newer"):
            old_id, new_id = str(rd["assertion_id_older"]), str(rd["assertion_id_newer"])
            temporal.append({
                "kept_in_active_graph": {"assertion_id": new_id, "claim_text": _lookup_assertion_summary(conn, new_id, str(rd.get("claim_newer") or new_id)), "posture": "ACTIVE_GRAPH (current)"},
                "moved_to_archive": {"assertion_id": old_id, "claim_text": _lookup_assertion_summary(conn, old_id, str(rd.get("claim_older") or old_id)), "posture": "HISTORICAL_ARCHIVE (superseded_by=" + new_id + ", retrieval_eligible=0)"},
            })
        elif fix in _ARCHIVE_ACTION_SET:
            for tid in _extract_archive_target_ids(f, rd):
                archived.append({
                    "record_id": tid,
                    "action": fix,
                    "posture": "HISTORICAL_ARCHIVE (retrieval_eligible=0, excluded from active graph queries)",
                })
    return temporal, archived


def build_deduplication_ledger(
    conn: Any,
    pkg: Dict[str, Any],
    retired_nodes: Optional[Set[str]] = None,
    extra_assertion_dups: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build itemized ledger of what was deduplicated (kept in Active Graph vs moved to Historical Archive)."""
    findings = pkg.get("findings") or []
    entity_merges = _build_entity_merge_entries(conn, findings, retired_nodes or set())
    assertion_dedups = _build_assertion_dedup_entries(conn, findings, extra_assertion_dups or [])
    temporal_sups, archived_recs = _build_temporal_and_archive_entries(conn, findings)
    return {
        "counts": {
            "entity_pairs_deduplicated": len(entity_merges),
            "assertions_deduplicated": len(assertion_dedups),
            "temporal_contradictions_superseded": len(temporal_sups),
            "standalone_records_archived_or_quarantined": len(archived_recs),
        },
        "entity_merges": entity_merges,
        "assertion_deduplications": assertion_dedups,
        "temporal_supersessions": temporal_sups,
        "archived_or_quarantined_records": archived_recs,
    }


def render_dedup_and_archive_markdown(transition: Dict[str, Any], ledger: Dict[str, Any]) -> str:
    """Render plain-English Markdown tables showing Active Graph vs Archive posture and itemized deduplications."""
    b, a, d = transition.get("before", {}), transition.get("after", {}), transition.get("delta", {})
    lines = [
        "### Active Graph vs. Historical Archive Posture",
        "| Domain | Active Graph (Before -> After, Delta) | Staging Hold (Before -> After, Delta) | Historical Archive (Before -> After, Delta) | Total Records |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]
    labels = [("nodes", "Entity Profiles (node)"), ("assertions", "Statements (assertion)"), ("search_documents", "Search Index (search_document)")]
    for key, label in labels:
        bk, ak, dk = b.get(key, {}), a.get(key, {}), d.get(key, {})
        lines.append(
            f"| {label} | {bk.get('active_graph', 0)} -> {ak.get('active_graph', 0)} ({dk.get('active_graph', 0):+d}) | "
            f"{bk.get('staging_hold', 0)} -> {ak.get('staging_hold', 0)} ({dk.get('staging_hold', 0):+d}) | "
            f"{bk.get('historical_archive', 0)} -> {ak.get('historical_archive', 0)} ({dk.get('historical_archive', 0):+d}) | "
            f"{ak.get('total', 0)} |"
        )
    lines.extend([
        "",
        "### Itemized Deduplication Ledger (Kept in Active Graph vs. Moved to Archive)",
        "| Category | Kept in Active Graph (Survivor) | Moved to Historical Archive (Retired / Superseded) | Preservation Guarantee |",
        "| :--- | :--- | :--- | :--- |",
    ])
    for em in ledger.get("entity_merges", [])[:25]:
        k, m = em["kept_in_active_graph"], em["moved_to_archive"]
        lines.append(f"| Entity Merge ({em['node_type']}) | {k['canonical_name']} ({k['node_id']}) | {m['canonical_name']} ({m['node_id']}) | Redirect + alias + quotes repointed to survivor |")
    for ad in ledger.get("assertion_deduplications", [])[:25]:
        k, m = ad["kept_in_active_graph"], ad["moved_to_archive"]
        lines.append(f"| Duplicate Statement | {k['claim_text'][:60]} ({k['assertion_id']}) | {m['claim_text'][:60]} ({m['assertion_id']}) | All supporting quotes linked to survivor |")
    for ts in ledger.get("temporal_supersessions", [])[:15]:
        k, m = ts["kept_in_active_graph"], ts["moved_to_archive"]
        lines.append(f"| Timeline Supersession | {k['claim_text'][:60]} ({k['assertion_id']}) | {m['claim_text'][:60]} ({m['assertion_id']}) | Historical lineage preserved via superseded_by |")
    if not (ledger.get("entity_merges") or ledger.get("assertion_deduplications") or ledger.get("temporal_supersessions")):
        lines.append("| None | Zero deduplications in this run | Zero records moved to archive | 100% unchanged |")
    return "\n".join(lines)
