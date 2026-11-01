"""Governed generation promotion, sidecar sync, and boundary exclusion helpers for hygiene remediation."""
from __future__ import annotations

import datetime as dt
import json
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from dual_pipeline.core import ROOT, sha256_file, stable_id
from dual_pipeline.runtime_root import resolve_authoritative_runtime_root
from tools.tier2_dedup_core.sidecar_vector_recovery import (
    _SUPPRESSED_TYPO_ROLE_PAIR_IDS,
    _prune_stale_quality_exceptions,
    remediate_additional_hygiene_fixes,
)
from tools.tier2_dedup_core.sow_duplicate_retirement import (
    refresh_sidecar_after_graph_cleanup,
    repair_falsely_historical_merged_assertions,
)

logger = logging.getLogger("apply_graph_hygiene_remediation")

_SQL_LINK_EDGE = "INSERT OR REPLACE INTO legacy_edge_assertion_map (edge_id, assertion_id, migration_confidence, migration_state, created_at) VALUES (?, ?, 1.0, 'structured', ?)"
_SQL_DEACTIVATE_NODE = "UPDATE node SET active = 0, status = 'archived_orphan', updated_at = ? WHERE node_id = ?"
_SQL_UPDATE_NODE_STATE = "UPDATE node_state SET lifecycle_state = 'inactive', currentness_state = 'historical', retrieval_eligibility = 'historical_only', resolution_state = 'superseded', updated_at = ? WHERE node_id = ?"
_SQL_DEACTIVATE_SEARCH_NODE = "UPDATE search_document SET canonical_status = 'historical', currentness_state = 'historical', retrieval_eligible = 0, updated_at = ? WHERE object_type = 'node' AND object_id = ?"
_SQL_INSERT_EXCEPTION = "INSERT OR REPLACE INTO graph_quality_exception (exception_id, metric_name, object_type, object_id, exception_type, reason, control_action, owner, approved_reference, active, created_at) VALUES (?, 'duplicate_entity_pair', 'node_pair', ?, 'HYGIENE_KEEP_DISTINCT', ?, 'keep_distinct', 'operator_hitl', ?, 1, ?)"
_SQL_INSERT_FINDING_EXCEPTION = "INSERT OR REPLACE INTO graph_quality_exception (exception_id, metric_name, object_type, object_id, exception_type, reason, control_action, owner, approved_reference, active, created_at) VALUES (?, ?, ?, ?, 'HYGIENE_FINDING_EXCLUDED', ?, 'exclude_finding', 'operator_hitl', ?, 1, ?)"
_SQL_INSERT_ERC = "INSERT OR REPLACE INTO entity_resolution_candidate (candidate_id, candidate_type, normalized_key, node_type_scope, status, auto_merge_allowed, rationale, created_at, resolved_at) VALUES (?, 'normalized_node_duplicate', ?, ?, 'resolved_distinct', 0, ?, ?, ?)"
_SQL_ARCHIVE_CAND_ASSERTION = "UPDATE assertion SET canonical_status = 'historical_candidate', canonical_eligible = 0, retrieval_eligible = 0, currentness_state = 'historical', updated_at = ? WHERE assertion_id = ?"
_SQL_ARCHIVE_CAND_SEARCH_DOC = "UPDATE search_document SET canonical_status = 'historical_candidate', currentness_state = 'historical', retrieval_eligible = 0, updated_at = ? WHERE assertion_id = ?"
_SQL_REPOINT_EV_ITEM = "UPDATE evidence_item SET source_id = ? WHERE source_id = ?"
_SQL_REPOINT_SV_WITH_DATE = "UPDATE source_version SET source_id = ?, version_number = ?, source_date_normalized = ? WHERE source_version_id = ?"
_SQL_REPOINT_SV_NO_DATE = "UPDATE source_version SET source_id = ?, version_number = ? WHERE source_version_id = ?"
_SQL_REPOINT_ASRT_PRIM = "UPDATE assertion SET primary_source_id = ? WHERE primary_source_id = ?"
_SQL_REPOINT_TABLE_MAP = {
    "search_document": ("PRAGMA table_info(search_document)", "UPDATE OR IGNORE search_document SET source_id = ? WHERE source_id = ?", None),
    "alias": ("PRAGMA table_info(alias)", "UPDATE OR IGNORE alias SET source_id = ? WHERE source_id = ?", "DELETE FROM alias WHERE source_id = ?"),
    "canonical_alias": ("PRAGMA table_info(canonical_alias)", "UPDATE OR IGNORE canonical_alias SET source_id = ? WHERE source_id = ?", "DELETE FROM canonical_alias WHERE source_id = ?"),
    "ingestion_source": ("PRAGMA table_info(ingestion_source)", "UPDATE OR IGNORE ingestion_source SET source_id = ? WHERE source_id = ?", "DELETE FROM ingestion_source WHERE source_id = ?"),
    "audit_event": ("PRAGMA table_info(audit_event)", "UPDATE OR IGNORE audit_event SET source_id = ? WHERE source_id = ?", None),
}
_SQL_DELETE_SOURCE_LABEL = "DELETE FROM source_label_state WHERE source_id = ?"
_SQL_DELETE_SOURCE = "DELETE FROM source WHERE source_id = ?"
_SQL_INSERT_SPLIT_SOURCE = "INSERT OR IGNORE INTO source (source_id, source_type, source_name, source_date, authority_rank, source_posture, cold_archive_ref, notes, created_at) SELECT ?, source_type, 'Governed Source ' || ? || ' (' || substr(?, -6) || ')', ?, authority_rank, source_posture, ?, notes, ? FROM source WHERE source_id = ?"
_SQL_REPOINT_SV_BY_ID = "UPDATE source_version SET source_id = ?, version_number = 1 WHERE source_version_id = ?"
_SQL_REPOINT_EV_BY_SV = "UPDATE evidence_item SET source_id = ? WHERE source_version_id = ?"
_SQL_REPOINT_ASRT_BY_SV = "UPDATE assertion SET primary_source_id = ? WHERE assertion_id IN (SELECT ae.assertion_id FROM assertion_evidence ae JOIN evidence_item ei ON ei.evidence_id = ae.evidence_id WHERE ei.source_version_id = ?)"
_SQL_REPOINT_SD_BY_SV = "UPDATE search_document SET source_id = ? WHERE assertion_id IN (SELECT ae.assertion_id FROM assertion_evidence ae JOIN evidence_item ei ON ei.evidence_id = ae.evidence_id WHERE ei.source_version_id = ?)"


def _is_archivable_stale_item(item: Any, seen: Set[str]) -> Optional[str]:
    if item.finding_type != "stale_candidate" or (item.details or {}).get("recommended_option", "ARCHIVE") != "ARCHIVE":
        return None
    aid = str(item.target_id or "")
    return aid if aid and aid not in seen else None


def archive_extra_stale_candidates(conn: Any, pkg: Dict[str, Any], stale_ids: List[str], has_sd: Any, now_iso: str) -> None:
    """Archive any newly wall-clock-aged evictable stale candidates when strict_package_gating is False."""
    if not stale_ids or pkg.get("strict_package_gating", False):
        return
    asrt_cols = {r[1] for r in conn.execute("PRAGMA table_info(assertion)").fetchall()}
    if not {"created_at", "canonical_status"}.issubset(asrt_cols):
        return
    from dual_pipeline.graph_scout_core.scans import scan_orphan_and_clutter
    seen = set(stale_ids)
    for item in scan_orphan_and_clutter(conn):
        aid = _is_archivable_stale_item(item, seen)
        if aid:
            seen.add(aid)
            conn.execute(_SQL_ARCHIVE_CAND_ASSERTION, (now_iso, aid))
            if has_sd:
                conn.execute(_SQL_ARCHIVE_CAND_SEARCH_DOC, (now_iso, aid))


def remediate_auto_link_edges(conn: Any, pkg: Dict[str, Any], now_iso: str, sample_ids: List[str]) -> int:
    """Cluster 3B: Link unmapped auto-linkable edges in legacy_edge_assertion_map."""
    linked_count = 0
    for f in (f for f in pkg.get("findings", []) if f.get("proposed_fix") == "auto_link_edge"):
        rd = f.get("raw_data") or {}
        edge_id = rd.get("edge_id") or (f.get("entity_references") or [None])[0]
        asrt_id = rd.get("matching_assertion_id")
        if edge_id and asrt_id:
            conn.execute(_SQL_LINK_EDGE, (edge_id, asrt_id, now_iso))
            sample_ids.append(str(edge_id))
            linked_count += 1
    return linked_count


def remediate_orphan_nodes(conn: Any, pkg: Dict[str, Any], now_iso: str, sample_ids: List[str]) -> Tuple[int, List[str]]:
    """Cluster 8: Archive approved orphan clutter nodes with zero edges and zero assertions."""
    archived_ids: List[str] = []
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    has_ns, has_sd = "node_state" in tables, "search_document" in tables
    for f in (f for f in pkg.get("findings", []) if f.get("proposed_fix") == "archive_node"):
        rd = f.get("raw_data") or {}
        nid = rd.get("node_id") or (f.get("entity_references") or [None])[0]
        if not nid:
            continue
        conn.execute(_SQL_DEACTIVATE_NODE, (now_iso, nid))
        if has_ns:
            conn.execute(_SQL_UPDATE_NODE_STATE, (now_iso, nid))
        if has_sd:
            conn.execute(_SQL_DEACTIVATE_SEARCH_NODE, (now_iso, nid))
        archived_ids.append(str(nid))
        sample_ids.append(str(nid))
    return len(archived_ids), archived_ids


def _find_winner_edge_id(conn: Any, winner_id: str, eid: str, has_key_cols: bool) -> Optional[str]:
    if has_key_cols:
        erow = conn.execute("SELECT from_node_id, edge_type, to_node_id FROM edge WHERE edge_id = ?", (eid,)).fetchone()
        if erow:
            wrow = conn.execute("SELECT edge_id FROM edge WHERE source_id = ? AND from_node_id = ? AND edge_type = ? AND to_node_id = ? LIMIT 1", (winner_id, erow[0], erow[1], erow[2])).fetchone()
            if wrow:
                return wrow[0]
    wrow = conn.execute("SELECT edge_id FROM edge WHERE source_id = ? LIMIT 1", (winner_id,)).fetchone()
    return wrow[0] if wrow else None


def _repoint_single_loser_edge(conn: Any, tables: Set[str], has_vq_edge: bool, eid: str, win_eid: Optional[str]) -> None:
    if has_vq_edge:
        if win_eid:
            conn.execute("UPDATE OR IGNORE validation_queue SET edge_id = ? WHERE edge_id = ?", (win_eid, eid))
        conn.execute("DELETE FROM validation_queue WHERE edge_id = ?", (eid,))
    if "legacy_edge_assertion_map" in tables:
        if win_eid:
            conn.execute("UPDATE OR IGNORE legacy_edge_assertion_map SET edge_id = ? WHERE edge_id = ?", (win_eid, eid))
        conn.execute("DELETE FROM legacy_edge_assertion_map WHERE edge_id = ?", (eid,))
    conn.execute("DELETE FROM edge WHERE edge_id = ?", (eid,))


def _repoint_edges_for_source(conn: Any, tables: Set[str], loser_id: str, winner_id: str) -> None:
    edge_cols = {r[1] for r in conn.execute("PRAGMA table_info(edge)").fetchall()} if "edge" in tables else set()
    if "source_id" not in edge_cols:
        return
    conn.execute("UPDATE OR IGNORE edge SET source_id = ? WHERE source_id = ?", (winner_id, loser_id))
    has_key_cols = {"from_node_id", "edge_type", "to_node_id"} <= edge_cols
    has_vq_edge = _has_col(conn, tables, "validation_queue", "edge_id")
    for (eid,) in conn.execute("SELECT edge_id FROM edge WHERE source_id = ?", (loser_id,)).fetchall():
        win_eid = _find_winner_edge_id(conn, winner_id, eid, has_key_cols)
        _repoint_single_loser_edge(conn, tables, has_vq_edge, eid, win_eid)


def _find_winner_claim_id(conn: Any, winner_id: str, cid: str, text_col: Optional[str]) -> Optional[str]:
    if text_col:
        sel_c = "SELECT " + text_col + " FROM claim WHERE claim_id = ?"
        crow = conn.execute(sel_c, (cid,)).fetchone()
        if crow and crow[0] is not None:
            sel_w = "SELECT claim_id FROM claim WHERE source_id = ? AND " + text_col + " = ? LIMIT 1"
            wrow = conn.execute(sel_w, (winner_id, crow[0])).fetchone()
            if wrow:
                return wrow[0]
    wrow = conn.execute("SELECT claim_id FROM claim WHERE source_id = ? LIMIT 1", (winner_id,)).fetchone()
    return wrow[0] if wrow else None


def _repoint_single_loser_claim(conn: Any, tables: Set[str], cid: str, win_cid: Optional[str]) -> None:
    for child_tbl in ("claim_confidence_hitl_matrix", "claim_hitl_validation_queue", "legacy_claim_assertion_map"):
        if child_tbl in tables:
            if win_cid:
                upd_c = "UPDATE OR IGNORE " + child_tbl + " SET claim_id = ? WHERE claim_id = ?"
                conn.execute(upd_c, (win_cid, cid))
            del_c = "DELETE FROM " + child_tbl + " WHERE claim_id = ?"
            conn.execute(del_c, (cid,))
    conn.execute("DELETE FROM claim WHERE claim_id = ?", (cid,))


def _repoint_claims_for_source(conn: Any, tables: Set[str], loser_id: str, winner_id: str) -> None:
    claim_cols = {r[1] for r in conn.execute("PRAGMA table_info(claim)").fetchall()} if "claim" in tables else set()
    if "source_id" not in claim_cols:
        return
    conn.execute("UPDATE OR IGNORE claim SET source_id = ? WHERE source_id = ?", (winner_id, loser_id))
    text_col = "normalized_claim_text" if "normalized_claim_text" in claim_cols else ("claim_text" if "claim_text" in claim_cols else None)
    for (cid,) in conn.execute("SELECT claim_id FROM claim WHERE source_id = ?", (loser_id,)).fetchall():
        win_cid = _find_winner_claim_id(conn, winner_id, cid, text_col)
        _repoint_single_loser_claim(conn, tables, cid, win_cid)


def _repoint_source_versions(conn: Any, loser_id: str, winner_id: str) -> None:
    src_cols = {r[1] for r in conn.execute("PRAGMA table_info(source)").fetchall()}
    win_row = conn.execute("SELECT source_date FROM source WHERE source_id = ?", (winner_id,)).fetchone() if "source_date" in src_cols else None
    win_date = win_row[0] if win_row and win_row[0] else None
    max_ver = conn.execute("SELECT coalesce(max(version_number), 0) FROM source_version WHERE source_id = ?", (winner_id,)).fetchone()[0]
    for idx, (sv_id,) in enumerate(conn.execute("SELECT source_version_id FROM source_version WHERE source_id = ? ORDER BY version_number", (loser_id,)).fetchall(), start=1):
        conn.execute(_SQL_REPOINT_SV_WITH_DATE if win_date else _SQL_REPOINT_SV_NO_DATE, (winner_id, max_ver + idx, win_date, sv_id) if win_date else (winner_id, max_ver + idx, sv_id))


def _repoint_auxiliary_source_tables(conn: Any, tables: Set[str], loser_id: str, winner_id: str) -> None:
    for tbl, (pragma_sql, update_sql, cleanup_sql) in _SQL_REPOINT_TABLE_MAP.items():
        if tbl in tables and "source_id" in {r[1] for r in conn.execute(pragma_sql).fetchall()}:
            conn.execute(update_sql, (winner_id, loser_id))
            if cleanup_sql:
                conn.execute(cleanup_sql, (loser_id,))


def _repoint_source_references(conn: Any, loser_id: str, winner_id: str) -> None:
    """Repoint all foreign-key and provenance references from loser_id to winner_id and delete loser_id."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "evidence_item" in tables:
        conn.execute(_SQL_REPOINT_EV_ITEM, (winner_id, loser_id))
    if "source_version" in tables:
        _repoint_source_versions(conn, loser_id, winner_id)
    if _has_col(conn, tables, "assertion", "primary_source_id"):
        conn.execute(_SQL_REPOINT_ASRT_PRIM, (winner_id, loser_id))
    _repoint_edges_for_source(conn, tables, loser_id, winner_id)
    _repoint_claims_for_source(conn, tables, loser_id, winner_id)
    _repoint_auxiliary_source_tables(conn, tables, loser_id, winner_id)
    if "source_label_state" in tables:
        conn.execute(_SQL_DELETE_SOURCE_LABEL, (loser_id,))
    conn.execute(_SQL_DELETE_SOURCE, (loser_id,))


def _split_single_version_collision(conn: Any, sid: str, sv_id: str, sv_date: str, has_asrt: bool, has_sd: bool, now_iso: str, sample_ids: List[str]) -> int:
    new_sid = stable_id("source", sid, sv_id, sv_date, length=24)
    conn.execute(_SQL_INSERT_SPLIT_SOURCE, (new_sid, sv_date, sv_id, sv_date, f"remediated_split://{sv_id}/{sv_date}", now_iso, sid))
    conn.execute(_SQL_REPOINT_SV_BY_ID, (new_sid, sv_id))
    conn.execute(_SQL_REPOINT_EV_BY_SV, (new_sid, sv_id))
    if has_asrt:
        conn.execute(_SQL_REPOINT_ASRT_BY_SV, (new_sid, sv_id))
    if has_sd:
        conn.execute(_SQL_REPOINT_SD_BY_SV, (new_sid, sv_id))
    sample_ids.append(new_sid)
    return 1


def _has_col(conn: Any, tables: Set[str], tbl: str, col: str) -> bool:
    return tbl in tables and col in {r[1] for r in conn.execute("PRAGMA table_info(" + tbl + ")").fetchall()}


def _split_multi_date_sources(conn: Any, raw_findings: List[Any], now_iso: str, sample_ids: List[str]) -> int:
    remediated, tables = 0, {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    has_asrt = "assertion_evidence" in tables and _has_col(conn, tables, "assertion", "primary_source_id")
    has_sd = "assertion_evidence" in tables and _has_col(conn, tables, "search_document", "source_id")
    for f in (x for x in raw_findings if x.finding_type == "multi_date_source_collision"):
        sv_rows = conn.execute("SELECT source_version_id, source_date_normalized FROM source_version WHERE source_id = ? ORDER BY version_number", (f.target_id,)).fetchall()
        base_date = sv_rows[0][1] if sv_rows else None
        for sv_id, sv_date in sv_rows[1:]:
            if sv_date and sv_date != base_date:
                remediated += _split_single_version_collision(conn, f.target_id, sv_id, sv_date, has_asrt, has_sd, now_iso, sample_ids)
    return remediated


def _resolve_source_col_names(conn: Any) -> Tuple[Optional[str], str, str]:
    src_cols = {r[1] for r in conn.execute("PRAGMA table_info(source)").fetchall()}
    name_col = "source_name" if "source_name" in src_cols else ("title" if "title" in src_cols else None)
    ref_col = "cold_archive_ref" if "cold_archive_ref" in src_cols else ("source_path_or_uri" if "source_path_or_uri" in src_cols else "NULL")
    date_col = "source_date" if "source_date" in src_cols else ("created_at" if "created_at" in src_cols else "NULL")
    return name_col, ref_col, date_col


def _compute_retitle_label(sid: str, row: Any) -> str:
    from dual_pipeline.graph_writer_core.sources import normalize_source_archive_stem
    stem = normalize_source_archive_stem(row[1])
    return stem.replace("_", " ").title() if stem and stem not in {"unknown", "some_doc"} else f"Governed Source {row[2] or sid[-8:]}"


def _retitle_degenerate_sources(conn: Any, raw_findings: List[Any], sample_ids: List[str]) -> int:
    name_col, ref_col, date_col = _resolve_source_col_names(conn)
    if not name_col:
        return 0
    sel_sql, upd_sql, remediated = f"SELECT {name_col}, {ref_col}, {date_col} FROM source WHERE source_id = ?", f"UPDATE source SET {name_col} = ? WHERE source_id = ?", 0
    for f in (x for x in raw_findings if x.finding_type == "degenerate_source_metadata"):
        row = conn.execute(sel_sql, (f.target_id,)).fetchone()
        if row is not None:
            conn.execute(upd_sql, (_compute_retitle_label(f.target_id, row), f.target_id))
            sample_ids.append(f.target_id)
            remediated += 1
    return remediated


def _rank_source_member(m: Dict[str, Any]) -> Tuple[int, int, str]:
    import re
    nm = str(m.get("source_name") or "").strip()
    not_unknown = 0 if nm.lower() in {"unknown", "unspecified", ""} else 1
    not_doc = 0 if nm.lower().startswith("document 202") or re.fullmatch(r"document\s+[0-9a-f]{12}", nm, re.I) else 1
    return (not_unknown, not_doc, m["source_id"])


def _consolidate_source_clusters(conn: Any, raw_findings: List[Any], sample_ids: List[str]) -> int:
    remediated = 0
    for f in (x for x in raw_findings if x.finding_type == "duplicate_source_cluster"):
        members = f.details.get("members", [])
        if len(members) >= 2:
            sorted_m = sorted(members, key=_rank_source_member, reverse=True)
            winner_id = sorted_m[0]["source_id"]
            sample_ids.append(winner_id)
            for loser in sorted_m[1:]:
                _repoint_source_references(conn, loser["source_id"], winner_id)
                remediated += 1
    return remediated


def _evict_fts_for_source_search_docs(conn: Any, sid: str, tables: Set[str]) -> None:
    if "search_document" not in tables or not _has_col(conn, tables, "search_document", "source_id"):
        return
    sd_cols = {r[1] for r in conn.execute("PRAGMA table_info(search_document)").fetchall()}
    obj_expr = "object_id" if "object_id" in sd_cols else ("assertion_id" if "assertion_id" in sd_cols else "NULL")
    sel_sd = "SELECT rowid, " + obj_expr + " FROM search_document WHERE source_id = ?"
    for r_id, obj_id in conn.execute(sel_sd, (sid,)).fetchall():
        for fts_tbl in ("search_fts", "search_document_fts"):
            if fts_tbl in tables:
                del_row_sql = "DELETE FROM " + fts_tbl + " WHERE rowid = ?"
                conn.execute(del_row_sql, (r_id,))
                if obj_id and _has_col(conn, tables, fts_tbl, "object_id"):
                    del_obj_sql = "DELETE FROM " + fts_tbl + " WHERE object_id = ?"
                    conn.execute(del_obj_sql, (obj_id,))


def _delete_unreferenced_source_row(conn: Any, sid: str, tables: Set[str]) -> None:
    _evict_fts_for_source_search_docs(conn, sid, tables)
    for tbl in ("alias", "canonical_alias", "ingestion_source", "source_label_state", "source_version", "search_document"):
        if _has_col(conn, tables, tbl, "source_id"):
            conn.execute("DELETE FROM " + tbl + " WHERE source_id = ?", (sid,))
    if _has_col(conn, tables, "audit_event", "source_id"):
        conn.execute("UPDATE audit_event SET source_id = NULL WHERE source_id = ?", (sid,))
    conn.execute(_SQL_DELETE_SOURCE, (sid,))


def _is_source_referenced(conn: Any, sid: str, tables: Set[str], has_asrt: bool, has_edge: bool, has_claim: bool) -> bool:
    if "evidence_item" in tables and conn.execute("SELECT 1 FROM evidence_item WHERE source_id = ? LIMIT 1", (sid,)).fetchone():
        return True
    if has_asrt and conn.execute("SELECT 1 FROM assertion WHERE primary_source_id = ? LIMIT 1", (sid,)).fetchone():
        return True
    if has_edge and conn.execute("SELECT 1 FROM edge WHERE source_id = ? LIMIT 1", (sid,)).fetchone():
        return True
    return bool(has_claim and conn.execute("SELECT 1 FROM claim WHERE source_id = ? LIMIT 1", (sid,)).fetchone())


def _retire_unreferenced_retired_sources(conn: Any, sample_ids: List[str]) -> int:
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    name_col, _, _ = _resolve_source_col_names(conn) if "source" in tables else (None, "", "")
    if not name_col:
        return 0
    has_a, has_e, has_c = _has_col(conn, tables, "assertion", "primary_source_id"), _has_col(conn, tables, "edge", "source_id"), _has_col(conn, tables, "claim", "source_id")
    retired = 0
    for (sid,) in conn.execute("SELECT source_id FROM source WHERE " + name_col + " LIKE 'Retired - %'").fetchall():
        if not _is_source_referenced(conn, sid, tables, has_a, has_e, has_c):
            _delete_unreferenced_source_row(conn, sid, tables)
            sample_ids.append(str(sid))
            retired += 1
    return retired


def remediate_duplicate_and_malformed_sources(
    conn: Any, pkg: Dict[str, Any], now_iso: str, sample_ids: List[str]
) -> int:
    """Cluster 9 (Gap 4): Consolidate duplicate source_id clusters and resolve 'Unknown' / hex-hash titles."""
    from dual_pipeline.graph_scout_core.scans import scan_duplicate_and_malformed_sources

    strict = bool(pkg.get("strict_package_gating", False))
    fixes = {f.get("proposed_fix") for f in pkg.get("findings", [])}
    allow_dup = (not strict) or ("consolidate_duplicate_sources" in fixes)
    allow_degen = (not strict) or ("unmerge_or_retitle_degenerate_source" in fixes)
    if not allow_dup and not allow_degen:
        return 0
    remediated = 0
    if allow_degen:
        remediated += _split_multi_date_sources(conn, scan_duplicate_and_malformed_sources(conn), now_iso, sample_ids)
        remediated += _retire_unreferenced_retired_sources(conn, sample_ids)
    if allow_dup:
        remediated += _consolidate_source_clusters(conn, scan_duplicate_and_malformed_sources(conn), sample_ids)
    if allow_degen:
        remediated += _retitle_degenerate_sources(conn, scan_duplicate_and_malformed_sources(conn), sample_ids)
    return remediated


def _record_single_finding_exclusion(conn: Any, item: Dict[str, Any], has_gqe: Any, seen_keys: Set[str], approval_ref: str, now_iso: str, sample_ids: List[str]) -> int:
    fix = str(item.get("proposed_fix") or "").strip()
    refs = [str(r).strip() for r in (item.get("entity_references") or []) if str(r).strip()]
    sig = "::".join(sorted(refs)) if refs else fix
    dedup_key = f"{fix}::{sig}"
    if not fix or dedup_key in seen_keys:
        return 0
    seen_keys.add(dedup_key)
    exc_id = stable_id("exc-finding", fix, sig, length=28)
    reason = str(item.get("exclusion_reason") or "Operator HITL decision: exclude finding")
    if has_gqe:
        conn.execute(_SQL_INSERT_FINDING_EXCEPTION, (exc_id, fix, fix, sig, reason, approval_ref, now_iso))
    sample_ids.append(exc_id)
    return 1


def _extract_pair_from_exclusion(item: Dict[str, Any]) -> Tuple[str, str, Dict[str, Any]]:
    rd = item.get("raw_data") if isinstance(item.get("raw_data"), dict) else item
    refs = item.get("entity_references") or []
    return str(rd.get("node_id_a") or (refs[0] if len(refs) >= 2 else "")).strip(), str(rd.get("node_id_b") or (refs[1] if len(refs) >= 2 else "")).strip(), rd


def _record_pair_exclusion(conn: Any, item: Dict[str, Any], has_gqe: Any, has_erc: Any, seen_pairs: Set[str], approval_ref: str, now_iso: str, sample_ids: List[str]) -> int:
    nid_a, nid_b, rd = _extract_pair_from_exclusion(item)
    if not nid_a or not nid_b or nid_a == nid_b:
        return 0
    n1, n2 = sorted((nid_a, nid_b))
    pair_key, exc_id = f"{n1}::{n2}", stable_id("exc-keep-distinct", n1, n2, length=28)
    if pair_key in seen_pairs or exc_id in _SUPPRESSED_TYPO_ROLE_PAIR_IDS:
        seen_pairs.add(pair_key)
        return 0
    seen_pairs.add(pair_key)
    if conn.execute("SELECT count(*) FROM node WHERE node_id IN (?, ?) AND active = 1", (n1, n2)).fetchone()[0] < 2:
        return 0
    reason = str(item.get("exclusion_reason") or rd.get("rationale") or "Operator HITL boundary decision: keep entities distinct")
    node_type = str(rd.get("node_type_a") or rd.get("node_type") or "entity")
    if has_gqe:
        conn.execute(_SQL_INSERT_EXCEPTION, (exc_id, pair_key, reason, approval_ref, now_iso))
    if has_erc:
        conn.execute(_SQL_INSERT_ERC, (stable_id("erc-distinct", n1, n2, length=28), pair_key, node_type, reason, now_iso, now_iso))
    sample_ids.append(exc_id)
    return 1


def record_boundary_exclusions(conn: Any, pkg: Dict[str, Any], now_iso: str, sample_ids: List[str], approval_ref: str = "HITL-HYGIENE") -> int:
    """Wave 2: Persist operator 'keep distinct' entity pairs and excluded findings into graph_quality_exception."""
    excluded = [x for x in (pkg.get("excluded_findings") or []) if isinstance(x, dict)]
    if not excluded:
        return 0
    has_gqe = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='graph_quality_exception'").fetchone()
    has_erc = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='entity_resolution_candidate'").fetchone()
    recorded, seen_pairs = 0, set()
    for item in excluded:
        fix = str(item.get("proposed_fix") or "").strip()
        if fix and fix not in ("merge_entities", "keep_entities_distinct"):
            recorded += _record_single_finding_exclusion(conn, item, has_gqe, seen_pairs, approval_ref, now_iso, sample_ids)
        else:
            recorded += _record_pair_exclusion(conn, item, has_gqe, has_erc, seen_pairs, approval_ref, now_iso, sample_ids)
    _prune_stale_quality_exceptions(conn, {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()})
    return recorded


_CLUSTER_DECISION_TOKENS = frozenset({
    "exclude", "excluded", "keep_distinct", "reject", "rejected", "approve", "approved", "apply"
})


def _sync_stripped_search_doc(conn: Any, aid: str, has_sd: bool, has_fts: bool, strip_fn: Any, now_iso: str) -> None:
    if not has_sd or not (sd_row := conn.execute("SELECT rowid, title, body FROM search_document WHERE assertion_id = ? OR object_id = ? LIMIT 1", (aid, aid)).fetchone()):
        return
    new_t, new_b = (strip_fn(sd_row[1]) if sd_row[1] else sd_row[1]), (strip_fn(sd_row[2]) if sd_row[2] else sd_row[2])
    conn.execute("UPDATE search_document SET title = ?, body = ?, updated_at = ? WHERE rowid = ?", (new_t, new_b, now_iso, sd_row[0]))
    if has_fts:
        conn.execute("UPDATE search_fts SET title = ?, body = ? WHERE rowid = ? OR object_id = ?", (new_t, new_b, sd_row[0], aid))


def _strip_hex_until_stable(val: Optional[str], strip_fn: Any) -> Optional[str]:
    if not val:
        return val
    cur = val
    for _ in range(5):
        if (nxt := strip_fn(cur)) == cur:
            break
        cur = nxt
    return cur


def strip_hex_suffixes_on_survivors(conn: Any, now_iso: str) -> int:
    """Strip trailing [6-hex] disambiguation tags from surviving current assertions idempotently and sync search_document/FTS."""
    from dual_pipeline.graph_maintenance_core.dedup import _strip_synthetic_suffix
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    has_sd, has_fts = "search_document" in tables, "search_fts" in tables
    rows = conn.execute("SELECT assertion_id, scope_text, object_literal FROM assertion WHERE currentness_state = 'current' AND (scope_text GLOB '*[[][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][]]*' OR object_literal GLOB '*[[][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][]]*')").fetchall()
    stripped_count = 0
    for aid, stext, olit in rows:
        clean_s, clean_o = _strip_hex_until_stable(stext, _strip_synthetic_suffix), _strip_hex_until_stable(olit, _strip_synthetic_suffix)
        if (clean_s, clean_o) == (stext, olit):
            continue
        conn.execute("SAVEPOINT sp_clean_hex")
        try:
            conn.execute("UPDATE assertion SET scope_text = ?, object_literal = ?, updated_at = ? WHERE assertion_id = ?", (clean_s, clean_o, now_iso, aid))
            _sync_stripped_search_doc(conn, aid, has_sd, has_fts, lambda s: _strip_hex_until_stable(s, _strip_synthetic_suffix), now_iso)
            conn.execute("RELEASE SAVEPOINT sp_clean_hex")
            stripped_count += 1
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT sp_clean_hex")
            conn.execute("RELEASE SAVEPOINT sp_clean_hex")
    return stripped_count


def _classify_cluster_action(c: Dict[str, Any], app_set: Set[str], exc_set: Set[str]) -> str:
    cid = str(c.get("cluster_id") or "")
    c_decision = str(c.get("decision") or c.get("status") or "").strip().lower()
    if cid in exc_set or c_decision in ("exclude", "excluded", "keep_distinct", "reject", "rejected"):
        return "exclude"
    if "ALL" not in app_set and cid not in app_set and c_decision not in ("approve", "approved", "apply"):
        return "skip"
    rec_action = (c.get("ready_to_ask_question") or {}).get("recommended_action", "apply")
    if rec_action == "exclude" and f"{cid}:MERGE" not in app_set and c_decision not in ("merge", "force_merge"):
        return "exclude"
    return "approve"


def _finding_sig(f: Dict[str, Any]) -> str:
    refs = sorted(str(x) for x in (f.get("entity_references") or []) if x)
    return f"{f.get('detective')}|{f.get('proposed_fix')}|{','.join(refs)}|{str(f.get('description') or '')[:80]}"


def _collect_omitted_cluster_findings(clusters: Sequence[Dict[str, Any]], kept: List[Dict[str, Any]], excl: List[Dict[str, Any]]) -> None:
    if not clusters or not kept:
        return
    seen_sigs = {_finding_sig(f) for f in kept + excl if isinstance(f, dict)}
    for cf in (cf for c in clusters for cf in (c.get("findings") or []) if isinstance(cf, dict)):
        sig = _finding_sig(cf)
        if sig not in seen_sigs:
            excl.append(cf)
            seen_sigs.add(sig)


def _normalize_cluster_set(primary: Optional[Sequence[str]], fallback: Any) -> Set[str]:
    src = primary if primary is not None else (fallback or [])
    return {str(c).strip() for c in src if str(c).strip()}


def _has_explicit_cluster_decisions(clusters: Sequence[Any]) -> bool:
    return any(isinstance(c, dict) and str(c.get("decision") or c.get("status") or "").strip().lower() in _CLUSTER_DECISION_TOKENS for c in clusters)


def filter_package_by_clusters(
    pkg: Dict[str, Any], approved_clusters: Optional[Sequence[str]] = None, excluded_clusters: Optional[Sequence[str]] = None
) -> Dict[str, Any]:
    """Filter package findings by approved/excluded cluster IDs and route keep-distinct clusters to excluded_findings."""
    app_set, exc_set = _normalize_cluster_set(approved_clusters, pkg.get("approved_clusters")), _normalize_cluster_set(excluded_clusters, pkg.get("excluded_clusters"))
    clusters = pkg.get("hitl_clusters") or []
    if not app_set and not exc_set and not _has_explicit_cluster_decisions(clusters):
        excl = list(pkg.get("excluded_findings") or []) + [f for f in pkg.get("findings", []) if f.get("proposed_fix") == "keep_entities_distinct"]
        kept = [f for f in pkg.get("findings", []) if f.get("proposed_fix") != "keep_entities_distinct"]
        _collect_omitted_cluster_findings(clusters, kept, excl)
        pkg["findings"], pkg["excluded_findings"] = kept, excl
        return pkg
    pkg["strict_package_gating"] = True
    approved_findings, excluded_findings = [], list(pkg.get("excluded_findings") or [])
    for c in clusters:
        action = _classify_cluster_action(c, app_set, exc_set)
        if action == "exclude":
            excluded_findings.extend(c.get("findings") or [])
        elif action == "approve":
            approved_findings.extend(c.get("findings") or [])
    pkg["findings"], pkg["excluded_findings"] = approved_findings, excluded_findings
    return pkg


def _write_release_and_activation_manifests(
    rt_root: Path, new_gen_id: str, prev_gen_id: Optional[str], next_pair: Dict[str, Any], now_iso: str
) -> None:
    from dual_pipeline.release_core.helpers import _seal_receipt
    rel_dir = rt_root / "releases"
    rel_dir.mkdir(parents=True, exist_ok=True)
    rel_payload = _seal_receipt({"schema": "urn:nee:hygiene-release:1.0.0", "release_id": f"release-{new_gen_id}", "generation_id": new_gen_id, "previous_generation_id": prev_gen_id, "graph_sha256": next_pair["graph_sha256"], "sidecar_sha256": next_pair["sidecar_sha256"], "created_at": now_iso})
    act_payload = _seal_receipt({"schema": "urn:nee:hygiene-activation:1.0.0", "activation_id": f"activation-{new_gen_id}", "generation_id": new_gen_id, "previous_generation_id": prev_gen_id, "activated_at": now_iso, "status": "ACTIVE"})
    (rel_dir / f"release-{new_gen_id}.json").write_text(json.dumps(rel_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (rel_dir / f"activation-{new_gen_id}.json").write_text(json.dumps(act_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _preserve_sidecar_encoding_fingerprint(sidecar_path: Path, active_pair: Dict[str, Any]) -> None:
    from dual_pipeline.storage.governed_context import GovernedStorageContext
    if not sidecar_path.is_file():
        return
    conn = GovernedStorageContext.connect(sidecar_path, readonly=False, timeout=30.0)
    try:
        rows = dict(conn.execute("SELECT key, value FROM metadata").fetchall())
        fp_dig = rows.get("encoding_fingerprint_digest") or active_pair.get("encoding_fingerprint_digest")
        if fp_dig and fp_dig != "not-applicable":
            conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('encoding_fingerprint_digest', ?)", (str(fp_dig),))
            conn.commit()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    finally:
        conn.close()


def _safe_sqlite_clone(src_db: Path, dst_db: Path) -> None:
    from dual_pipeline.storage.governed_context import GovernedStorageContext
    try:
        with src_db.open("rb") as fh:
            header = fh.read(16)
        if header.startswith(b"SQLite format 3"):
            src_conn = GovernedStorageContext.connect(src_db, readonly=True, timeout=30.0)
            dst_conn = GovernedStorageContext.connect(dst_db, readonly=False, timeout=30.0)
            try:
                src_conn.backup(dst_conn)
                dst_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                dst_conn.close()
                src_conn.close()
            return
    except Exception:
        pass
    shutil.copy2(src_db, dst_db)


def prepare_promoted_generation_paths(
    runtime_root: Optional[Path | str], run_id: str
) -> Tuple[Path, Path, Dict[str, Any], str, Path, Path, Path]:
    """Clone active generation graph and sidecar into a new generation directory."""
    rt_root = Path(runtime_root).resolve() if runtime_root else resolve_authoritative_runtime_root(ROOT)
    active_pair_path = rt_root / "active_pair.json"
    if not active_pair_path.is_file():
        raise FileNotFoundError(f"active_pair.json not found in runtime root: {rt_root}")
    active_pair = json.loads(active_pair_path.read_text(encoding="utf-8"))
    old_graph, old_sidecar = (rt_root / active_pair["graph_path"]).resolve(), (rt_root / active_pair["sidecar_path"]).resolve()
    if not old_graph.is_file() or not old_sidecar.is_file():
        raise FileNotFoundError(f"Active generation artifacts not found under: {rt_root}")
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_gen_id = stable_id("generation", active_pair.get("generation_id", "gen"), f"hygiene-{run_id}", now_iso, uuid.uuid4().hex[:8], length=28)
    new_gen_dir = rt_root / "generations" / new_gen_id
    new_gen_dir.mkdir(parents=True, exist_ok=True)
    new_graph_path, new_sidecar_path, pair_manifest_path = new_gen_dir / "graph.sqlite", new_gen_dir / "sidecar.sqlite", new_gen_dir / "pair_manifest.json"
    _safe_sqlite_clone(old_graph, new_graph_path)
    _safe_sqlite_clone(old_sidecar, new_sidecar_path)
    return rt_root, active_pair_path, active_pair, new_gen_id, new_graph_path, new_sidecar_path, pair_manifest_path


def finalize_promoted_generation(
    active_pair_path: Path, active_pair: Dict[str, Any], new_gen_id: str, new_graph_path: Path,
    new_sidecar_path: Path, pair_manifest_path: Path, new_graph_sha: str, now_iso: str,
) -> Dict[str, Any]:
    """Refresh sidecar digests, write pair_manifest.json and release manifests, and atomically update active_pair.json under active_pair.lock."""
    import fcntl
    new_graph_snap = stable_id("graph-snapshot", new_graph_sha, length=24)
    refresh_sidecar_after_graph_cleanup(new_sidecar_path, new_graph_path, new_graph_sha, new_graph_snap, new_gen_id)
    _preserve_sidecar_encoding_fingerprint(new_sidecar_path, active_pair)
    new_sidecar_sha = sha256_file(new_sidecar_path)
    lock_path = active_pair_path.parent / "active_pair.lock"
    with open(lock_path, "w", encoding="utf-8") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            latest_active = json.loads(active_pair_path.read_text(encoding="utf-8")) if active_pair_path.is_file() else active_pair
            next_pair = {
                **latest_active,
                "previous_generation_id": latest_active.get("generation_id"),
                "generation_id": new_gen_id,
                "graph_path": f"generations/{new_gen_id}/graph.sqlite",
                "sidecar_path": f"generations/{new_gen_id}/sidecar.sqlite",
                "graph_sha256": new_graph_sha,
                "graph_snapshot": new_graph_snap,
                "sidecar_sha256": new_sidecar_sha,
                "activated_at": now_iso,
            }
            pair_manifest_path.write_text(json.dumps({**next_pair, "activated_at": None}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            _write_release_and_activation_manifests(active_pair_path.parent, new_gen_id, next_pair["previous_generation_id"], next_pair, now_iso)
            tmp_active = active_pair_path.with_suffix(".json.tmp")
            tmp_active.write_text(json.dumps(next_pair, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            tmp_active.replace(active_pair_path)
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
    logger.info("Promoted hygiene generation %s (graph_sha256=%s, sidecar_sha256=%s)", new_gen_id, new_graph_sha[:12], new_sidecar_sha[:12])
    return next_pair
