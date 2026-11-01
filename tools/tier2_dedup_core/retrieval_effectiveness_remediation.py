"""Governed remediation handlers for October 27-28 graph retrieval effectiveness findings."""
from __future__ import annotations

from typing import Any, Dict, List, Set

_SQL_UPDATE_NODE_DESC_WITH_TS = (
    "UPDATE node SET description = ?, updated_at = ? WHERE node_id = ?"
)
_SQL_UPDATE_NODE_DESC_NO_TS = (
    "UPDATE node SET description = ? WHERE node_id = ?"
)
_SQL_SUPERSEDE_STALE_ROLE_WITH_TS = (
    "UPDATE assertion SET currentness_state = 'historical', retrieval_eligible = 0, "
    "validation_state = 'superseded', superseded_by_assertion_id = ?, updated_at = ? "
    "WHERE assertion_id = ?"
)
_SQL_SUPERSEDE_STALE_ROLE_NO_TS = (
    "UPDATE assertion SET currentness_state = 'historical', retrieval_eligible = 0, "
    "superseded_by_assertion_id = ? WHERE assertion_id = ?"
)
_SQL_DEACTIVATE_SD_WITH_TS = (
    "UPDATE search_document SET retrieval_eligible = 0, currentness_state = 'historical', "
    "updated_at = ? WHERE assertion_id = ? OR search_document_id = ?"
)
_SQL_DEACTIVATE_SD_NO_TS = (
    "UPDATE search_document SET retrieval_eligible = 0, currentness_state = 'historical' "
    "WHERE assertion_id = ? OR search_document_id = ?"
)
_SQL_REBIND_OBJECT_NODE_WITH_TS = (
    "UPDATE assertion SET object_node_id = ?, updated_at = ? "
    "WHERE assertion_id = ? AND (object_node_id IS NULL OR object_node_id = '')"
)
_SQL_REBIND_OBJECT_NODE_NO_TS = (
    "UPDATE assertion SET object_node_id = ? "
    "WHERE assertion_id = ? AND (object_node_id IS NULL OR object_node_id = '')"
)
_SQL_QUARANTINE_BLEED_WITH_TS = (
    "UPDATE assertion SET currentness_state = 'historical', retrieval_eligible = 0, "
    "is_quarantined = 1, updated_at = ? WHERE assertion_id = ?"
)
_SQL_QUARANTINE_BLEED_NO_TS = (
    "UPDATE assertion SET currentness_state = 'historical', retrieval_eligible = 0 "
    "WHERE assertion_id = ?"
)
_SQL_UPDATE_SV_SNAPSHOT_DATE_STATUS = (
    "UPDATE source_version SET source_date_status = 'estimated_from_source_snapshot' "
    "WHERE source_id = ?"
)
_SQL_UPDATE_SOURCE_SNAPSHOT_NOTES = (
    "UPDATE source SET notes = ? WHERE source_id = ?"
)
_SQL_UPDATE_ASRT_SNAPSHOT_DATE_STATUS = (
    "UPDATE assertion SET date_status = 'estimated_from_source_snapshot', "
    "qualifiers_json = json_set(coalesce(qualifiers_json, '{}'), '$.date_basis', 'file_upload_snapshot', '$.upload_date', coalesce(valid_from, ?)), "
    "updated_at = ? WHERE primary_source_id = ?"
)


def _table_cols(conn: Any, table: str) -> Set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(" + table + ")").fetchall()}


def _deactivate_search_doc_for_id(conn: Any, has_sd: bool, sd_cols: Set[str], aid: str, now_iso: str) -> None:
    if not has_sd:
        return
    if "updated_at" in sd_cols:
        conn.execute(_SQL_DEACTIVATE_SD_WITH_TS, (now_iso, aid, aid))
    else:
        conn.execute(_SQL_DEACTIVATE_SD_NO_TS, (aid, aid))


def _reconcile_stale_role_item(
    conn: Any,
    f: Dict[str, Any],
    tables: Set[str],
    has_sd: bool,
    now_iso: str,
    sample_ids: List[str],
) -> int:
    rd = f.get("raw_data") or {}
    node_id = str(rd.get("node_id") or rd.get("subject_node_id") or "")
    resolved_role = str(rd.get("resolved_role") or "")
    winner_aid = str(rd.get("winner_assertion_id") or "")
    stale_aids: List[str] = [str(x) for x in (rd.get("stale_assertion_ids") or []) if x]
    if not stale_aids and rd.get("assertion_id"):
        stale_aids.append(str(rd["assertion_id"]))

    count = 0
    if node_id and resolved_role and "node" in tables:
        ncols = _table_cols(conn, "node")
        if "description" in ncols:
            if "updated_at" in ncols:
                conn.execute(_SQL_UPDATE_NODE_DESC_WITH_TS, (resolved_role, now_iso, node_id))
            else:
                conn.execute(_SQL_UPDATE_NODE_DESC_NO_TS, (resolved_role, node_id))
            sample_ids.append(node_id)
            count += 1

    if "assertion" in tables and stale_aids:
        acols = _table_cols(conn, "assertion")
        sd_cols = _table_cols(conn, "search_document") if has_sd else set()
        for aid in stale_aids:
            if {"validation_state", "updated_at", "superseded_by_assertion_id"}.issubset(acols):
                conn.execute(_SQL_SUPERSEDE_STALE_ROLE_WITH_TS, (winner_aid or None, now_iso, aid))
            else:
                conn.execute(_SQL_SUPERSEDE_STALE_ROLE_NO_TS, (winner_aid or None, aid))
            _deactivate_search_doc_for_id(conn, has_sd, sd_cols, aid, now_iso)
            sample_ids.append(aid)
            count += 1
    return count


def _rebind_literal_mention_item(
    conn: Any,
    f: Dict[str, Any],
    tables: Set[str],
    now_iso: str,
    sample_ids: List[str],
) -> int:
    if "assertion" not in tables:
        return 0
    rd = f.get("raw_data") or {}
    aid = str(rd.get("assertion_id") or (f.get("entity_references") or [""])[0] or "")
    target_nid = str(rd.get("target_node_id") or "")
    if not aid or not target_nid:
        return 0
    acols = _table_cols(conn, "assertion")
    if "updated_at" in acols:
        conn.execute(_SQL_REBIND_OBJECT_NODE_WITH_TS, (target_nid, now_iso, aid))
    else:
        conn.execute(_SQL_REBIND_OBJECT_NODE_NO_TS, (target_nid, aid))
    sample_ids.append(aid)
    return 1


def _quarantine_disjoint_or_owner_item(
    conn: Any,
    f: Dict[str, Any],
    tables: Set[str],
    has_sd: bool,
    now_iso: str,
    sample_ids: List[str],
) -> int:
    if "assertion" not in tables:
        return 0
    rd = f.get("raw_data") or {}
    aids = [str(rd["assertion_id"])] if rd.get("assertion_id") else [str(x) for x in (f.get("entity_references") or []) if x]
    acols = _table_cols(conn, "assertion")
    sd_cols = _table_cols(conn, "search_document") if has_sd else set()
    for aid in aids:
        if {"is_quarantined", "updated_at"}.issubset(acols):
            conn.execute(_SQL_QUARANTINE_BLEED_WITH_TS, (now_iso, aid))
        else:
            conn.execute(_SQL_QUARANTINE_BLEED_NO_TS, (aid,))
        _deactivate_search_doc_for_id(conn, has_sd, sd_cols, aid, now_iso)
        sample_ids.append(aid)
    return len(aids)


def _reconcile_upload_snapshot_dates_item(
    conn: Any,
    f: Dict[str, Any],
    tables: Set[str],
    now_iso: str,
    sample_ids: List[str],
) -> int:
    rd = f.get("raw_data") or {}
    sid = str(rd.get("source_id") or (f.get("entity_references") or [""])[0] or "").strip()
    if not sid:
        return 0
    upload_date = str(rd.get("upload_date") or "2026-10-28").strip()
    note_text = str(
        rd.get("notes")
        or f"Governed NEE dual-pipeline source package (file upload/export date: {upload_date}; content reflects undated snapshot notes)."
    )
    count = 0
    if "source" in tables:
        conn.execute(_SQL_UPDATE_SOURCE_SNAPSHOT_NOTES, (note_text, sid))
        sample_ids.append(sid)
        count += 1
    if "source_version" in tables:
        conn.execute(_SQL_UPDATE_SV_SNAPSHOT_DATE_STATUS, (sid,))
        count += 1
    if "assertion" in tables:
        cur = conn.execute(_SQL_UPDATE_ASRT_SNAPSHOT_DATE_STATUS, (upload_date, now_iso, sid))
        count += max(0, cur.rowcount)
    return count


def remediate_october_retrieval_effectiveness_findings(
    conn: Any,
    findings: List[Dict[str, Any]],
    tables: Set[str],
    has_sd: bool,
    now_iso: str,
    sample_ids: List[str],
) -> int:
    """Apply governed fixes for October 27-28 retrieval effectiveness findings."""
    reconciled = 0
    for f in findings:
        fix = f.get("proposed_fix")
        if fix == "reconcile_stale_role_and_description":
            reconciled += _reconcile_stale_role_item(conn, f, tables, has_sd, now_iso, sample_ids)
        elif fix == "rebind_literal_entity_mention_edge":
            reconciled += _rebind_literal_mention_item(conn, f, tables, now_iso, sample_ids)
        elif fix == "quarantine_disjoint_role_or_owner_bleed":
            reconciled += _quarantine_disjoint_or_owner_item(conn, f, tables, has_sd, now_iso, sample_ids)
        elif fix == "reconcile_upload_snapshot_dates":
            reconciled += _reconcile_upload_snapshot_dates_item(conn, f, tables, now_iso, sample_ids)
    return reconciled
