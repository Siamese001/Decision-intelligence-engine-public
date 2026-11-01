"""Read-only Graph Scout scanners for speaker attribution defects, synthetic markers, and qualifier drift."""
from __future__ import annotations

from typing import Any, Dict, List, Set

from storage.database import sqlite3

from ..l0.speaker_and_qualifier_policy import (
    classify_speaker_attribution_defect,
    clean_non_placeholder_speaker,
    extract_inline_turn_speaker,
    has_non_promotable_qualifiers,
    has_stale_candidate_qualifiers,
    has_synthetic_or_cross_project_pollution,
    is_placeholder_speaker,
)
from .models import ClutterFinding


def _table_exists(cursor: sqlite3.Cursor, name: str) -> bool:
    return cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _col_names(cursor: sqlite3.Cursor, table: str) -> Set[str]:
    return {r[0] for r in cursor.execute("SELECT name FROM pragma_table_info(?)", (table,)).fetchall()}


def _source_and_node_join_clauses(cursor: sqlite3.Cursor, asrt_cols: Set[str]) -> tuple[str, str, str, str, str, str, str]:
    has_src = _table_exists(cursor, "source") and "primary_source_id" in asrt_cols
    has_node = _table_exists(cursor, "node") and "subject_node_id" in asrt_cols
    src_cols = _col_names(cursor, "source") if has_src else set()
    ncols = _col_names(cursor, "node") if has_node else set()
    stype = "s.source_type" if "source_type" in src_cols else "NULL AS source_type"
    sname = "s.source_name" if "source_name" in src_cols else "NULL AS source_name"
    scold = "s.cold_archive_ref" if "cold_archive_ref" in src_cols else "NULL AS cold_archive_ref"
    subj_name = "n.canonical_name AS subject_canonical_name" if "canonical_name" in ncols else "NULL AS subject_canonical_name"
    subj_type = "n.node_type AS subject_node_type" if "node_type" in ncols else "NULL AS subject_node_type"
    sjoin = "LEFT JOIN source s ON a.primary_source_id = s.source_id" if has_src else ""
    njoin = "LEFT JOIN node n ON a.subject_node_id = n.node_id" if has_node else ""
    return stype, sname, scold, subj_name, subj_type, sjoin, njoin


def _evidence_select_exprs(cursor: sqlite3.Cursor) -> tuple[str, str, str]:
    has_ei = _table_exists(cursor, "assertion_evidence") and _table_exists(cursor, "evidence_item")
    if not has_ei:
        return "NULL AS evidence_id", "NULL AS transcript_timestamp", "NULL AS direct_quote"
    ei_cols = _col_names(cursor, "evidence_item")
    ts_col = "coalesce(ei.transcript_timestamp, ei.locator_value)" if "transcript_timestamp" in ei_cols else "ei.locator_value"
    ev_sub = "(SELECT ae.evidence_id FROM assertion_evidence ae WHERE ae.assertion_id = a.assertion_id LIMIT 1) AS evidence_id"
    ts_sub = f"(SELECT {ts_col} FROM assertion_evidence ae JOIN evidence_item ei ON ei.evidence_id = ae.evidence_id WHERE ae.assertion_id = a.assertion_id LIMIT 1) AS transcript_timestamp"
    dq_sub = (
        "(SELECT ei.direct_quote FROM assertion_evidence ae JOIN evidence_item ei ON ei.evidence_id = ae.evidence_id WHERE ae.assertion_id = a.assertion_id LIMIT 1) AS direct_quote"
        if "direct_quote" in ei_cols
        else "NULL AS direct_quote"
    )
    return ev_sub, ts_sub, dq_sub


def _build_active_assertion_query(cursor: sqlite3.Cursor, asrt_cols: Set[str]) -> str:
    opt = lambda col, alias=None: f"a.{col}" if col in asrt_cols else f"NULL AS {alias or col}"
    stype, sname, scold, subj_name, subj_type, sjoin, njoin = _source_and_node_join_clauses(cursor, asrt_cols)
    ev_sub, ts_sub, dq_sub = _evidence_select_exprs(cursor)
    conds = ["1=1"]
    if "currentness_state" in asrt_cols:
        conds.append("coalesce(a.currentness_state, 'current') = 'current'")
    if "superseded_by_assertion_id" in asrt_cols:
        conds.append("a.superseded_by_assertion_id IS NULL")
    if "is_quarantined" in asrt_cols:
        conds.append("coalesce(a.is_quarantined, 0) = 0")
    lane_parts: List[str] = []
    if "canonical_status" in asrt_cols:
        lane_parts.append("a.canonical_status = 'canonical'")
    if "retrieval_eligible" in asrt_cols:
        lane_parts.append("coalesce(a.retrieval_eligible, 0) = 1")
    if lane_parts:
        conds.append(f"({' OR '.join(lane_parts)})")
    return (
        f"SELECT a.assertion_id, {opt('subject_node_id')}, {opt('predicate')}, {opt('scope_text')}, "
        f"{opt('object_literal')}, {opt('speaker_or_author')}, {opt('primary_source_id')}, "
        f"{opt('canonical_status')}, {opt('validation_state')}, {opt('qualifiers_json')}, "
        f"{stype}, {sname}, {scold}, {subj_name}, {subj_type}, {ev_sub}, {ts_sub}, {dq_sub} "
        f"FROM assertion a {sjoin} {njoin} WHERE {' AND '.join(conds)}"
    )


def _fetch_active_assertion_rows(cursor: sqlite3.Cursor) -> List[Dict[str, Any]]:
    if not _table_exists(cursor, "assertion"):
        return []
    asrt_cols = _col_names(cursor, "assertion")
    if "assertion_id" not in asrt_cols:
        return []
    cursor.execute(_build_active_assertion_query(cursor, asrt_cols))
    col_list = [d[0] for d in (cursor.description or [])]
    return [dict(r) if hasattr(r, "keys") else dict(zip(col_list, r)) for r in cursor.fetchall()]


def _scan_synthetic_active_nodes(cursor: sqlite3.Cursor) -> List[ClutterFinding]:
    if not _table_exists(cursor, "node"):
        return []
    ncols = _col_names(cursor, "node")
    if "node_id" not in ncols:
        return []
    cname = "canonical_name" if "canonical_name" in ncols else "NULL AS canonical_name"
    where = "WHERE coalesce(active, 1) = 1" if "active" in ncols else ""
    node_sql = "SELECT node_id, " + cname + " FROM node " + where
    findings: List[ClutterFinding] = []
    for nid, name_val in cursor.execute(node_sql).fetchall():
        syn = has_synthetic_or_cross_project_pollution(record_id=str(nid), scope_text=str(name_val or ""))
        if syn:
            findings.append(
                ClutterFinding(
                    finding_type=f"synthetic_node_pollution:{syn}",
                    target_id=str(nid),
                    label=f"Synthetic fixture node active in production graph: {nid}",
                    details={"node_id": str(nid), "defect_code": syn, "canonical_name": str(name_val or "")},
                    action_recommended="archive_clutter",
                )
            )
    return findings


def _row_speaker_defect(row: Dict[str, Any]) -> str | None:
    return classify_speaker_attribution_defect(
        predicate=row.get("predicate"),
        scope_text=row.get("scope_text") or row.get("object_literal"),
        speaker_or_author=row.get("speaker_or_author"),
        subject_node_id=row.get("subject_node_id"),
        source_type=row.get("source_type"),
        source_name=row.get("source_name"),
        assertion_id=str(row.get("assertion_id") or ""),
        qualifiers_json=row.get("qualifiers_json"),
        transcript_timestamp=row.get("transcript_timestamp"),
        validation_state=row.get("validation_state"),
        cold_archive_ref=row.get("cold_archive_ref"),
    )


def _suggested_rebound_speaker(row: Dict[str, Any]) -> str | None:
    if not is_placeholder_speaker(row.get("speaker_or_author")):
        return None
    txt = str(row.get("scope_text") or row.get("object_literal") or "")
    inline_spk = extract_inline_turn_speaker(txt)
    if inline_spk:
        return inline_spk
    ntype = str(row.get("subject_node_type") or "").strip().lower()
    if ntype in {"stakeholder", "person", "internal_person", "person_profile"}:
        return clean_non_placeholder_speaker(row.get("subject_canonical_name"))
    return None


def scan_speaker_and_diarization_defects(cursor: sqlite3.Cursor) -> List[ClutterFinding]:
    """Scan active canonical/retrieval assertions for speaker, diarization, and synthetic purity defects."""
    findings = _scan_synthetic_active_nodes(cursor)
    for row in _fetch_active_assertion_rows(cursor):
        defect = _row_speaker_defect(row)
        if not defect:
            continue
        aid = str(row["assertion_id"])
        preview = str(row.get("scope_text") or row.get("object_literal") or "")[:140]
        rebound = _suggested_rebound_speaker(row)
        findings.append(
            ClutterFinding(
                finding_type=f"speaker_attribution_defect:{defect}",
                target_id=aid,
                label=f"Speaker/purity defect ({defect}) on active assertion {aid}",
                details={
                    "assertion_id": aid,
                    "defect_code": defect,
                    "speaker_or_author": row.get("speaker_or_author"),
                    "suggested_rebound_speaker": rebound,
                    "subject_node_id": str(row.get("subject_node_id") or ""),
                    "subject_canonical_name": row.get("subject_canonical_name"),
                    "subject_node_type": row.get("subject_node_type"),
                    "primary_source_id": str(row.get("primary_source_id") or ""),
                    "source_name": row.get("source_name"),
                    "source_type": row.get("source_type"),
                    "cold_archive_ref": row.get("cold_archive_ref"),
                    "evidence_id": row.get("evidence_id"),
                    "transcript_timestamp": row.get("transcript_timestamp"),
                    "direct_quote": row.get("direct_quote"),
                    "qualifiers_json": row.get("qualifiers_json"),
                    "validation_state": row.get("validation_state"),
                    "scope_text": row.get("scope_text"),
                    "object_literal": row.get("object_literal"),
                    "claim_preview": preview,
                },
                action_recommended="quarantine_undiarized_or_misattributed_claim",
            )
        )
    return findings


def _classify_qualifier_drift(row: Dict[str, Any]) -> tuple[str, str] | None:
    if str(row.get("canonical_status") or "") != "canonical":
        return None
    if _row_speaker_defect(row) is not None:
        return None
    qraw = row.get("qualifiers_json")
    vstate = str(row.get("validation_state") or "").strip().lower()
    if has_non_promotable_qualifiers(qraw):
        return ("leaked_non_promotable_qualifier", "demote_leaked_candidate_assertion")
    if vstate == "needs_review":
        return ("canonical_marked_needs_review", "demote_leaked_candidate_assertion")
    if has_stale_candidate_qualifiers(qraw):
        return ("stale_candidate_qualifier_metadata", "sync_promoted_qualifier_metadata")
    return None


def scan_qualifier_metadata_drift(cursor: sqlite3.Cursor) -> List[ClutterFinding]:
    """Scan canonical assertions for non-promotable qualifier leaks or stale candidate qualifier metadata."""
    findings: List[ClutterFinding] = []
    for row in _fetch_active_assertion_rows(cursor):
        verdict = _classify_qualifier_drift(row)
        if verdict is None:
            continue
        ftype, action = verdict
        aid = str(row["assertion_id"])
        findings.append(
            ClutterFinding(
                finding_type=ftype,
                target_id=aid,
                label=f"Qualifier governance drift ({ftype}) on canonical assertion {aid}",
                details={
                    "assertion_id": aid,
                    "subject_node_id": str(row.get("subject_node_id") or ""),
                    "validation_state": str(row.get("validation_state") or ""),
                    "qualifiers_preview": str(row.get("qualifiers_json") or "")[:160],
                },
                action_recommended=action,
            )
        )
    return findings
