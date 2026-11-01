"""Temporal contradiction and clutter diagnostic scans for Graph Scout."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from storage.database import sqlite3

from .models import ClutterFinding, ContradictionFinding, EdgeParityFinding

logger = logging.getLogger(__name__)

_FUNCTIONAL_PREDICATES = frozenset({
    "REPORTS_TO",
    "HAS_ROLE",
    "HAS_TITLE",
    "HAS_STATUS",
    "HAS_DEPLOYMENT_STATUS",
    "ASSIGNED_TO_ORG",
    "LOCATED_IN",
    "EMPLOYED_BY",
    "HEAD_OF",
    "OWNS_PRIMARY",
})

_SPECIFIC_PREDICATES = frozenset({
    "AFFILIATED_WITH",
    "EMPLOYED_BY",
    "REPORTS_TO",
    "OWNS_PRIMARY",
    "HEAD_OF",
    "ASSIGNED_TO_ORG",
    "HAS_ROLE",
})


def _is_functional_predicate(predicate: str) -> bool:
    """Return True if predicate is single-valued over time."""
    up = predicate.upper()
    if up == "HAS_CLAIM_TEXT" or up.endswith("_CLAIM_TEXT"):
        return False
    if up in _FUNCTIONAL_PREDICATES:
        return True
    return up.endswith("_STATUS") or up.startswith("STATUS_")


def _table_exists(cursor: sqlite3.Cursor, name: str) -> bool:
    return cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _col_names(cursor: sqlite3.Cursor, table: str) -> Set[str]:
    pragma_sql = f"PRAGMA table_info({table})"
    return {r[1] for r in cursor.execute(pragma_sql).fetchall()}


def _opt_col(cols: Set[str], name: str, alias: Optional[str] = None) -> str:
    target = alias or name
    return f"a.{name}" if name in cols else f"NULL AS {target}"


def _evidence_select_and_joins(cursor: sqlite3.Cursor) -> Tuple[List[str], List[str]]:
    if not (_table_exists(cursor, "assertion_evidence") and _table_exists(cursor, "evidence_item")):
        return ["NULL AS evidence_quote", "NULL AS source_title"], []
    ei_cols = _col_names(cursor, "evidence_item")
    quote_parts = ["ei.direct_quote"] if "direct_quote" in ei_cols else []
    if "locator_value" in ei_cols:
        quote_parts.append("ei.locator_value")
    quote_expr = f"coalesce({', '.join(quote_parts)})" if len(quote_parts) > 1 else (quote_parts[0] if quote_parts else "NULL")
    cols = [f"{quote_expr} AS evidence_quote"]
    if "extraction_confidence" in ei_cols:
        cols.append("ei.extraction_confidence AS evidence_confidence")
    cols.append("ae.evidence_id AS linked_evidence_id")
    joins = [
        "LEFT JOIN assertion_evidence ae ON a.assertion_id = ae.assertion_id",
        "LEFT JOIN evidence_item ei ON ae.evidence_id = ei.evidence_id",
    ]
    if _table_exists(cursor, "source"):
        cols.append("s.source_name AS source_title")
        joins.append("LEFT JOIN source s ON ei.source_id = s.source_id")
    else:
        cols.append("NULL AS source_title")
    return cols, joins


def _fetch_temporal_assertions(cursor: sqlite3.Cursor) -> List[Dict[str, Any]]:
    """Fetch current functional assertions with resolved names and evidence context."""
    asrt_cols = _col_names(cursor, "assertion")
    if "assertion_id" not in asrt_cols or "subject_node_id" not in asrt_cols:
        return []
    ev_cols, ev_joins = _evidence_select_and_joins(cursor)
    cols = [
        "a.assertion_id",
        "a.subject_node_id",
        "a.predicate",
        "a.object_node_id",
        _opt_col(asrt_cols, "object_literal"),
        _opt_col(asrt_cols, "valid_from"),
        _opt_col(asrt_cols, "recorded_at"),
        _opt_col(asrt_cols, "confidence_score"),
        _opt_col(asrt_cols, "scope_text"),
        "n.canonical_name AS subject_name",
        "no.canonical_name AS object_name",
        *ev_cols,
    ]
    joins = [
        "JOIN node n ON a.subject_node_id = n.node_id",
        "LEFT JOIN node no ON a.object_node_id = no.node_id",
        *ev_joins,
    ]
    order_valid = "a.valid_from ASC" if "valid_from" in asrt_cols else "a.assertion_id ASC"
    order_recorded = ", a.recorded_at ASC" if "recorded_at" in asrt_cols else ""
    query = f"""
        SELECT {', '.join(cols)}
        FROM assertion a
        {' '.join(joins)}
        WHERE a.currentness_state = 'current'
          AND a.canonical_status IN ('canonical', 'candidate')
        GROUP BY a.assertion_id
        ORDER BY a.subject_node_id, a.predicate, {order_valid}{order_recorded}
    """
    cursor.execute(query)
    return [dict(r) for r in cursor.fetchall()]


def _build_contradiction_finding(
    subject_id: str, predicate: str, older: Dict[str, Any], newer: Dict[str, Any], margin_threshold: float
) -> Optional[ContradictionFinding]:
    val_older = older["object_literal"] or older["object_name"] or older["object_node_id"] or ""
    val_newer = newer["object_literal"] or newer["object_name"] or newer["object_node_id"] or ""
    if val_older == val_newer or not val_older or not val_newer:
        return None

    ts_older = older["valid_from"] or older["recorded_at"] or ""
    ts_newer = newer["valid_from"] or newer["recorded_at"] or ""
    c_supersede, c_coexist = (0.85, 0.35) if ts_newer > ts_older else (0.55, 0.45)
    delta = round(abs(c_supersede - c_coexist), 2)
    rationale = (
        f"Divergent assertions for subject '{older['subject_name']}' on predicate '{predicate}': "
        f"Older ({ts_older}): '{val_older}' vs Newer ({ts_newer}): '{val_newer}'. "
        f"Supersession recommended. Margin Delta={delta:.2f}."
    )
    return ContradictionFinding(
        subject_node_id=subject_id,
        subject_name=older["subject_name"],
        predicate=predicate,
        assertion_id_older=older["assertion_id"],
        assertion_id_newer=newer["assertion_id"],
        claim_older=str(val_older),
        claim_newer=str(val_newer),
        timestamp_older=str(ts_older),
        timestamp_newer=str(ts_newer),
        confidence_supersede=c_supersede,
        confidence_coexist=c_coexist,
        margin_delta=delta,
        hitl_required=delta < margin_threshold,
        rationale=rationale,
        scope_text_older=older.get("scope_text"),
        scope_text_newer=newer.get("scope_text"),
        evidence_quote_older=older.get("evidence_quote"),
        evidence_quote_newer=newer.get("evidence_quote"),
        source_title_older=older.get("source_title"),
        source_title_newer=newer.get("source_title"),
    )


def scan_temporal_contradictions(conn: sqlite3.Connection) -> List[ContradictionFinding]:
    """Scan current assertions for temporal divergence on functional single-valued predicates."""
    if conn.row_factory is None:
        conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    rows = _fetch_temporal_assertions(cursor)
    from dual_pipeline.parameters import get_hitl_ambiguity_margin_threshold
    margin_threshold = get_hitl_ambiguity_margin_threshold()

    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault((r["subject_node_id"], r["predicate"]), []).append(r)

    findings: List[ContradictionFinding] = []
    for (subject_id, predicate), assertions in groups.items():
        if len(assertions) < 2 or not _is_functional_predicate(predicate):
            continue
        for i in range(len(assertions) - 1):
            finding = _build_contradiction_finding(subject_id, predicate, assertions[i], assertions[i + 1], margin_threshold)
            if finding:
                findings.append(finding)
    return findings


def _fetch_stale_candidate_assertions(cursor: sqlite3.Cursor) -> List[Dict[str, Any]]:
    """Fetch current candidate assertions with resolved names, evidence, and supersession context."""
    asrt_cols = _col_names(cursor, "assertion")
    if "assertion_id" not in asrt_cols or "canonical_status" not in asrt_cols:
        return []
    ev_cols, ev_joins = _evidence_select_and_joins(cursor)
    has_node = _table_exists(cursor, "node")
    cols = [
        "a.assertion_id",
        "a.subject_node_id",
        "a.predicate",
        "a.created_at",
        _opt_col(asrt_cols, "updated_at"),
        _opt_col(asrt_cols, "validation_state"),
        _opt_col(asrt_cols, "confidence_score"),
        _opt_col(asrt_cols, "superseded_by_assertion_id"),
        _opt_col(asrt_cols, "scope_text"),
        _opt_col(asrt_cols, "object_literal"),
        _opt_col(asrt_cols, "object_node_id"),
        *(["ns.canonical_name AS subject_name"] if has_node else ["NULL AS subject_name"]),
        *(["no.canonical_name AS object_name"] if (has_node and "object_node_id" in asrt_cols) else ["NULL AS object_name"]),
        *ev_cols,
    ]
    node_joins = []
    if has_node:
        node_joins.append("LEFT JOIN node ns ON a.subject_node_id = ns.node_id")
        if "object_node_id" in asrt_cols:
            node_joins.append("LEFT JOIN node no ON a.object_node_id = no.node_id")
    joins = node_joins + ev_joins
    curr_filter = "AND a.currentness_state = 'current'" if "currentness_state" in asrt_cols else ""
    val_filter = (
        "AND a.validation_state IN ('needs_review', 'source_statement', 'unreviewed')"
        if "validation_state" in asrt_cols
        else ""
    )
    query = f"""
        SELECT {', '.join(cols)}
        FROM assertion a
        {' '.join(joins)}
        WHERE a.canonical_status = 'candidate'
          {curr_filter}
          {val_filter}
          AND a.created_at IS NOT NULL
        GROUP BY a.assertion_id
    """
    cursor.execute(query)
    return [dict(r) for r in cursor.fetchall()]


def _first_newer_or_canonical(rows: Sequence[Any], created_at: str) -> Optional[str]:
    for match in rows:
        if match[1] == "canonical" or (match[2] and str(match[2]) > created_at):
            return str(match[0])
    return None


def _match_same_predicate_supersession(
    cursor: sqlite3.Cursor, subj: str, pred: str, aid: str, obj_node: str, obj_lit: str, created_at: str, curr_cond: str
) -> Optional[str]:
    cursor.execute(
        f"""SELECT assertion_id, canonical_status, created_at FROM assertion
            WHERE subject_node_id = ? AND upper(predicate) = ? AND assertion_id != ?
              {curr_cond} AND canonical_status IN ('canonical', 'candidate')
              AND (coalesce(object_node_id, '') = ? AND coalesce(object_literal, '') = ?)""",
        (subj, pred, aid, obj_node, obj_lit),
    )
    exact = _first_newer_or_canonical(cursor.fetchall(), created_at)
    if exact or not _is_functional_predicate(pred):
        return exact
    cursor.execute(
        f"""SELECT assertion_id, canonical_status, created_at FROM assertion
            WHERE subject_node_id = ? AND upper(predicate) = ? AND assertion_id != ?
              {curr_cond} AND canonical_status IN ('canonical', 'candidate')""",
        (subj, pred, aid),
    )
    return _first_newer_or_canonical(cursor.fetchall(), created_at)


def _find_superseding_assertion_id(cursor: sqlite3.Cursor, row: Dict[str, Any], asrt_cols: Set[str]) -> Optional[str]:
    """Return the assertion_id that supersedes this candidate, if any."""
    if row.get("superseded_by_assertion_id"):
        return str(row["superseded_by_assertion_id"])
    subj = str(row.get("subject_node_id") or "")
    pred = str(row.get("predicate") or "").upper()
    if not (subj and pred):
        return None
    curr_cond = "AND currentness_state = 'current'" if "currentness_state" in asrt_cols else ""
    aid = str(row.get("assertion_id") or "")
    obj_node = str(row.get("object_node_id") or "")
    obj_lit = str(row.get("object_literal") or "")
    created_at = str(row.get("created_at") or "")

    same_pred = _match_same_predicate_supersession(cursor, subj, pred, aid, obj_node, obj_lit, created_at, curr_cond)
    if same_pred or pred != "RELATED_TO" or not obj_node:
        return same_pred

    placeholders = ",".join("?" for _ in _SPECIFIC_PREDICATES)
    cursor.execute(
        f"""SELECT assertion_id FROM assertion
            WHERE subject_node_id = ? AND object_node_id = ? AND assertion_id != ?
              {curr_cond} AND canonical_status IN ('canonical', 'candidate')
              AND upper(predicate) IN ({placeholders})""",
        (subj, obj_node, aid, *_SPECIFIC_PREDICATES),
    )
    res = cursor.fetchone()
    return str(res[0]) if res else None


def _is_candidate_evictable(
    cursor: sqlite3.Cursor, row: Dict[str, Any], asrt_cols: Set[str]
) -> Tuple[bool, Optional[str], str]:
    """Check whether an aged candidate assertion is superseded or ungrounded noise."""
    sup_id = _find_superseding_assertion_id(cursor, row, asrt_cols)
    if sup_id:
        return True, sup_id, "superseded_by_newer_or_canonical_assertion"
    conf = row.get("confidence_score")
    conf_val = float(conf) if conf is not None else 0.0
    ev_conf = row.get("evidence_confidence")
    if ev_conf is not None and float(ev_conf) > conf_val:
        conf_val = float(ev_conf)
    quote = (row.get("evidence_quote") or "").strip()
    has_linked_ev = bool(row.get("linked_evidence_id"))
    if conf_val < 0.70 and not quote and not has_linked_ev:
        return True, None, "ungrounded_low_confidence_candidate"
    return False, None, "active_grounded_unsuperseded"


def _parse_utc_dt(ts_val: Any) -> Optional[datetime]:
    if not ts_val:
        return None
    dt_val = datetime.fromisoformat(str(ts_val).replace("Z", "+00:00"))
    return dt_val.replace(tzinfo=timezone.utc) if dt_val.tzinfo is None else dt_val


def _is_within_days(ts_val: Any, now_dt: datetime, max_days: int) -> bool:
    try:
        dt_val = _parse_utc_dt(ts_val)
        return (now_dt - dt_val).days < max_days if dt_val else False
    except Exception:
        return False


def _has_recent_corroboration(
    cursor: sqlite3.Cursor, row: Dict[str, Any], now_dt: datetime, revalidation_age_days: int
) -> bool:
    """Return True if the assertion was re-verified or gained newer corroborating evidence within the window."""
    created_str = str(row.get("created_at") or "")
    updated_str = str(row.get("updated_at") or "")
    if updated_str and updated_str != created_str and _is_within_days(updated_str, now_dt, revalidation_age_days):
        return True
    if not (_table_exists(cursor, "assertion_evidence") and _table_exists(cursor, "evidence_item")):
        return False
    ei_cols = _col_names(cursor, "evidence_item")
    ts_col = "created_at" if "created_at" in ei_cols else ("recorded_at" if "recorded_at" in ei_cols else None)
    if not ts_col:
        return False
    cursor.execute(
        f"""SELECT ei.{ts_col} FROM assertion_evidence ae
            JOIN evidence_item ei ON ae.evidence_id = ei.evidence_id
            WHERE ae.assertion_id = ? AND ei.{ts_col} IS NOT NULL""",
        (row.get("assertion_id"),),
    )
    return any(_is_within_days(ev_ts, now_dt, revalidation_age_days) for (ev_ts,) in cursor.fetchall())


def _build_orphan_query(cursor: sqlite3.Cursor, node_cols: Set[str], node_id_col: str) -> str:
    asrt_cols = _col_names(cursor, "assertion") if _table_exists(cursor, "assertion") else set()
    s_col = "subject_node_id" if "subject_node_id" in asrt_cols else ("entity_id" if "entity_id" in asrt_cols else None)
    o_col = "object_node_id" if "object_node_id" in asrt_cols else None
    sub_not_in = f"AND n.{node_id_col} NOT IN (SELECT DISTINCT {s_col} FROM assertion WHERE {s_col} IS NOT NULL)" if s_col else ""
    obj_not_in = f"AND n.{node_id_col} NOT IN (SELECT DISTINCT {o_col} FROM assertion WHERE {o_col} IS NOT NULL)" if o_col else ""
    active_cond = "AND n.active = 1" if "active" in node_cols else ""
    name_col = "n.canonical_name" if "canonical_name" in node_cols else ("n.label" if "label" in node_cols else f"n.{node_id_col}")
    type_col = "n.node_type" if "node_type" in node_cols else "'entity'"
    status_col = "n.status" if "status" in node_cols else "'active'"
    created_col = "n.created_at" if "created_at" in node_cols else "NULL"
    join_ns = (
        f"LEFT JOIN node_state ns ON n.{node_id_col} = ns.node_id WHERE coalesce(ns.intentional_orphan, 0) = 0"
        if _table_exists(cursor, "node_state")
        else "WHERE 1=1"
    )
    return (
        f"SELECT n.{node_id_col} AS node_id, {name_col} AS canonical_name, {type_col} AS node_type, "
        f"{status_col} AS status, {created_col} AS created_at "
        f"FROM node n {join_ns} {active_cond} {sub_not_in} {obj_not_in}"
    )


def _edge_count_for_node(cursor: sqlite3.Cursor, has_edge: bool, node_id: str) -> int:
    if not has_edge:
        return 0
    c_res = cursor.execute("SELECT count(*) FROM edge WHERE from_node_id = ? OR to_node_id = ?", (node_id, node_id)).fetchone()
    return int(c_res[0]) if c_res else 0


def _is_recent_candidate_node(row: Any, now_dt: datetime, min_orphan_age_days: int) -> bool:
    status_val = str(row["status"] or "").strip().lower()
    created_val = row["created_at"]
    return bool(status_val == "candidate" and created_val and _is_within_days(created_val, now_dt, min_orphan_age_days))


def _scan_orphan_nodes(cursor: sqlite3.Cursor, min_orphan_age_days: int = 14) -> List[ClutterFinding]:
    if not _table_exists(cursor, "node"):
        return []
    node_cols = _col_names(cursor, "node")
    node_id_col = "node_id" if "node_id" in node_cols else ("id" if "id" in node_cols else None)
    if not node_id_col:
        return []
    cursor.execute(_build_orphan_query(cursor, node_cols, node_id_col))
    col_names = [d[0] for d in (cursor.description or [])]
    orphan_rows = [dict(r) if hasattr(r, "keys") else dict(zip(col_names, r)) for r in cursor.fetchall()]
    has_edge = _table_exists(cursor, "edge")
    now_dt = datetime.now(timezone.utc)
    findings: List[ClutterFinding] = []
    for row in orphan_rows:
        if _is_recent_candidate_node(row, now_dt, min_orphan_age_days):
            continue
        edge_cnt = _edge_count_for_node(cursor, has_edge, str(row["node_id"]))
        if edge_cnt > 0:
            continue
        status_val = str(row["status"] or "").strip().lower() or "active"
        findings.append(
            ClutterFinding(
                finding_type="orphan_node",
                target_id=row["node_id"],
                label=f"Orphan entity: '{row['canonical_name']}' ({row['node_type']})",
                details={
                    "node_id": row["node_id"],
                    "node_type": row["node_type"],
                    "canonical_name": row["canonical_name"],
                    "status": status_val,
                    "created_at": str(row["created_at"] or ""),
                    "allow_aged_candidate_orphan": bool(status_val == "candidate" and row["created_at"]),
                    "assertion_count": 0,
                    "edge_count": edge_cnt,
                    "recommended_option": "ARCHIVE_OR_LINK",
                    "confidence_tier": "HIGH (88%)",
                },
                action_recommended="graph_gc_archive_or_link",
            )
        )
    return findings


def _build_stale_candidate_finding(
    row: Dict[str, Any],
    age_days: int,
    sup_id: Optional[str],
    reason: str,
    finding_type: str = "stale_candidate",
    recommended_option: str = "ARCHIVE",
    action_recommended: str = "candidate_cleanup_reclassify",
) -> ClutterFinding:
    s_name = row["subject_name"] or row["subject_node_id"] or ""
    o_val = row["object_name"] or row["object_literal"] or row["object_node_id"] or ""
    claim_text = row["scope_text"] or (f"{s_name} {row['predicate']} {o_val}".strip() if (s_name or o_val) else "")
    prefix = "90-Day Review" if finding_type == "revalidation_review_90d" else "Candidate"
    label = f"{prefix}: '{s_name}' {row['predicate']} '{o_val}'" if (s_name or o_val) else f"Assertion {row['assertion_id']} ({row['predicate']})"
    return ClutterFinding(
        finding_type=finding_type,
        target_id=row["assertion_id"],
        label=label,
        details={
            "assertion_id": row["assertion_id"],
            "subject_node_id": row["subject_node_id"],
            "subject_name": s_name,
            "predicate": row["predicate"],
            "object_node_id": row["object_node_id"],
            "object_name": row["object_name"],
            "object_literal": row["object_literal"],
            "claim_text": claim_text,
            "evidence_quote": row["evidence_quote"] or "",
            "source_title": row["source_title"] or "",
            "age_days": age_days,
            "created_at": row["created_at"],
            "superseded_by_assertion_id": sup_id,
            "eviction_reason": reason,
            "recommended_option": recommended_option,
            "hitl_options": ["CONFIRM", "REMOVE"] if finding_type == "revalidation_review_90d" else ["ARCHIVE", "KEEP"],
            "confidence_tier": "MEDIUM (75%)" if finding_type == "revalidation_review_90d" else "HIGH (90%)",
        },
        action_recommended=action_recommended,
    )


def scan_orphan_and_clutter(
    conn: sqlite3.Connection,
    max_candidate_age_days: int = 30,
    revalidation_age_days: int = 90,
) -> List[ClutterFinding]:
    """Scan for non-intentional orphan nodes, superseded candidate clutter, and 90-day uncorroborated claims."""
    if conn.row_factory is None:
        conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    if not _table_exists(cursor, "node"):
        return []
    findings = _scan_orphan_nodes(cursor)
    asrt_cols = _col_names(cursor, "assertion") if _table_exists(cursor, "assertion") else set()
    now_dt = datetime.now(timezone.utc)
    for row in _fetch_stale_candidate_assertions(cursor):
        try:
            created_dt = _parse_utc_dt(row["created_at"])
            if not created_dt:
                continue
            age_days = (now_dt - created_dt).days
            if age_days < max_candidate_age_days:
                continue
            evictable, sup_id, reason = _is_candidate_evictable(cursor, row, asrt_cols)
            if evictable:
                findings.append(_build_stale_candidate_finding(row, age_days, sup_id, reason))
            elif age_days >= revalidation_age_days and not _has_recent_corroboration(cursor, row, now_dt, revalidation_age_days):
                findings.append(
                    _build_stale_candidate_finding(
                        row, age_days, None, "uncorroborated_after_90_days_requires_confirm_or_remove",
                        finding_type="revalidation_review_90d", recommended_option="CONFIRM_OR_REMOVE",
                        action_recommended="escalate_90d_revalidation",
                    )
                )
        except Exception:
            continue
    return findings


def _is_historical_or_consolidated_edge(
    edge: Dict[str, Any], historical_edge_ids: Set[str], node_status_map: Dict[str, str]
) -> bool:
    eid = str(edge["edge_id"])
    if edge.get("superseded_by") or eid in historical_edge_ids:
        return True
    for nid in (str(edge["from_node_id"]), str(edge["to_node_id"])):
        st = node_status_map.get(nid, "").lower()
        if st == "merged" or st.startswith("historical"):
            return True
    return False


def _classify_edge_parity(
    edge: Dict[str, Any], has_node_table: bool, active_nodes: Set[str], mapped_edge_ids: Set[str],
    assertion_pair_map: Dict[Tuple[str, str], str], historical_edge_ids: Optional[Set[str]] = None,
    node_status_map: Optional[Dict[str, str]] = None,
) -> Optional[EdgeParityFinding]:
    eid, u, v, etype = str(edge["edge_id"]), str(edge["from_node_id"]), str(edge["to_node_id"]), str(edge["edge_type"])
    if _is_historical_or_consolidated_edge(edge, historical_edge_ids or set(), node_status_map or {}):
        return None
    if has_node_table and (u not in active_nodes or v not in active_nodes):
        missing = ([f"from_node_id='{u}'"] if u not in active_nodes else []) + ([f"to_node_id='{v}'"] if v not in active_nodes else [])
        return EdgeParityFinding(
            edge_id=eid, from_node_id=u, to_node_id=v, edge_type=etype,
            issue_type="dangling_endpoint", matching_assertion_id=None,
            description=f"Dangling edge {eid} ({etype}): endpoint(s) inactive/missing: {', '.join(missing)}",
            details={"edge_id": eid, "from_node_id": u, "to_node_id": v, "edge_type": etype, "inactive_endpoints": missing},
            action_recommended="archive_or_heal_edge",
        )
    if eid in mapped_edge_ids:
        return None
    matching_asrt = assertion_pair_map.get((u, v))
    if matching_asrt:
        return EdgeParityFinding(
            edge_id=eid, from_node_id=u, to_node_id=v, edge_type=etype,
            issue_type="unmapped_auto_linkable", matching_assertion_id=matching_asrt,
            description=f"Unmapped edge {eid} ({etype}: {u} -> {v}) matches existing assertion {matching_asrt}.",
            details={"edge_id": eid, "from_node_id": u, "to_node_id": v, "edge_type": etype, "matching_assertion_id": matching_asrt},
            action_recommended="auto_link_edge_assertion",
        )
    return EdgeParityFinding(
        edge_id=eid, from_node_id=u, to_node_id=v, edge_type=etype,
        issue_type="unmapped_needs_assertion", matching_assertion_id=None,
        description=f"Unmapped edge {eid} ({etype}: {u} -> {v}) has no backing assertion in graph.",
        details={"edge_id": eid, "from_node_id": u, "to_node_id": v, "edge_type": etype},
        action_recommended="synthesize_backing_assertion",
    )


def _load_historical_edge_ids(cursor: sqlite3.Cursor, tables: Set[str]) -> Set[str]:
    if "legacy_edge_assertion_map" not in tables or "assertion" not in tables:
        return set()
    asrt_cols = _col_names(cursor, "assertion")
    hist_clauses = []
    if "currentness_state" in asrt_cols:
        hist_clauses.append("a.currentness_state IN ('historical', 'superseded')")
    if "canonical_status" in asrt_cols:
        hist_clauses.append("a.canonical_status IN ('historical', 'superseded', 'rejected')")
    if not hist_clauses:
        return set()
    hist_sql = "SELECT DISTINCT m.edge_id FROM legacy_edge_assertion_map m JOIN assertion a ON m.assertion_id = a.assertion_id WHERE m.edge_id IS NOT NULL AND (" + " OR ".join(hist_clauses) + ")"
    cursor.execute(hist_sql)
    return {str(r[0]) for r in cursor.fetchall()}


def _load_active_nodes_and_status(cursor: sqlite3.Cursor, tables: Set[str]) -> Tuple[Set[str], Dict[str, str]]:
    if "node" not in tables:
        return set(), {}
    node_cols = _col_names(cursor, "node")
    id_n = "node_id" if "node_id" in node_cols else "id"
    status_expr = "status" if "status" in node_cols else "''"
    active_expr = "active" if "active" in node_cols else "1"
    active_nodes: Set[str] = set()
    node_status_map: Dict[str, str] = {}
    select_nodes_sql = "SELECT " + id_n + ", " + active_expr + ", " + status_expr + " FROM node"
    for r in cursor.execute(select_nodes_sql).fetchall():
        nid_str = str(r[0])
        if int(r[1] if r[1] is not None else 1) == 1:
            active_nodes.add(nid_str)
        if r[2]:
            node_status_map[nid_str] = str(r[2])
    return active_nodes, node_status_map


def _load_edge_parity_context(
    cursor: sqlite3.Cursor, tables: Set[str]
) -> Tuple[Set[str], Set[str], Dict[Tuple[str, str], str], Set[str], Dict[str, str]]:
    mapped_edge_ids = (
        {str(r[0]) for r in cursor.execute("SELECT DISTINCT edge_id FROM legacy_edge_assertion_map WHERE edge_id IS NOT NULL").fetchall()}
        if "legacy_edge_assertion_map" in tables else set()
    )
    historical_edge_ids = _load_historical_edge_ids(cursor, tables)
    active_nodes, node_status_map = _load_active_nodes_and_status(cursor, tables)

    assertion_pair_map: Dict[Tuple[str, str], str] = {}
    if "assertion" in tables:
        cursor.execute("SELECT assertion_id, subject_node_id, object_node_id FROM assertion WHERE subject_node_id IS NOT NULL AND object_node_id IS NOT NULL")
        for r in cursor.fetchall():
            assertion_pair_map.setdefault((str(r["subject_node_id"]), str(r["object_node_id"])), str(r["assertion_id"]))
    return mapped_edge_ids, active_nodes, assertion_pair_map, historical_edge_ids, node_status_map


def scan_edge_assertion_parity(conn: sqlite3.Connection) -> List[EdgeParityFinding]:
    """Scan edges for missing assertion mappings and dangling node endpoints."""
    if conn.row_factory is None:
        conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    tables = {r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "edge" not in tables:
        return []
    edge_cols = _col_names(cursor, "edge")
    from_col = "from_node_id" if "from_node_id" in edge_cols else ("source" if "source" in edge_cols else None)
    to_col = "to_node_id" if "to_node_id" in edge_cols else ("target" if "target" in edge_cols else None)
    if not from_col or not to_col:
        return []
    id_col = "edge_id" if "edge_id" in edge_cols else "rowid"
    type_expr = "edge_type" if "edge_type" in edge_cols else ("predicate" if "predicate" in edge_cols else "'RELATED_TO'")
    sup_expr = "superseded_by" if "superseded_by" in edge_cols else "NULL"
    edge_query = f"SELECT {id_col} AS edge_id, {from_col} AS from_node_id, {to_col} AS to_node_id, {type_expr} AS edge_type, {sup_expr} AS superseded_by FROM edge"
    cursor.execute(edge_query)
    edges = [dict(r) for r in cursor.fetchall()]

    mapped_edge_ids, active_nodes, assertion_pair_map, historical_edge_ids, node_status_map = _load_edge_parity_context(cursor, tables)
    findings: List[EdgeParityFinding] = []
    for edge in edges:
        f = _classify_edge_parity(
            edge, "node" in tables, active_nodes, mapped_edge_ids, assertion_pair_map, historical_edge_ids, node_status_map
        )
        if f:
            findings.append(f)
    return findings


from .source_scans import scan_duplicate_and_malformed_sources  # noqa: E402, F401


