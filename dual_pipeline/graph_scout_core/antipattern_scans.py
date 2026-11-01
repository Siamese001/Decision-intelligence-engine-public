"""Read-only retrieval antipattern and cross-project contamination scanners for Graph Scout."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from storage.database import sqlite3

from .models import ClutterFinding, DuplicateEntityFinding
from .retrieval_effectiveness_scans import scan_october_retrieval_effectiveness_gaps
from .speaker_and_qualifier_scans import (
    scan_qualifier_metadata_drift,
    scan_speaker_and_diarization_defects,
)

# Mutually exclusive project/engagement boundary signatures
_PROJECT_SIGNATURES: Dict[str, Tuple[Set[str], re.Pattern[str]]] = {
    "lens": ({"workstream_lens", "project_lens", "lens"}, re.compile(r"\b(?:lens\s+platform|workstream_lens|project_lens|system[:_]lens)\b", re.I)),
    "jarvis": ({"project_jarvis", "workstream_jarvis", "jarvis"}, re.compile(r"\b(?:jarvis|project_jarvis|workstream_jarvis|system[:_]jarvis)\b", re.I)),
    "sherpa": ({"project_beacon", "workstream_project_beacon", "project_sherpa"}, re.compile(r"\b(?:digital\s+sherpa|project_beacon|workstream_project_beacon)\b", re.I)),
    "nexus": ({"project_nexus", "workstream_nexus", "nexus"}, re.compile(r"\b(?:project\s+nexus|project_nexus|workstream_nexus|system[:_]nexus)\b", re.I)),
    "dociq": ({"dociq", "project_dociq", "workstream_dociq"}, re.compile(r"\b(?:dociq|doc\s+iq|project_dociq|workstream_dociq)\b", re.I)),
}

_CROSS_TYPE_GROUPS: Tuple[Tuple[str, ...], ...] = (
    ("person", "stakeholder", "person_profile", "internal_person"),
    ("workstream", "program", "system", "technical_anchor"),
    ("org", "organization"),
    ("role", "role_group"),
)

_PREFIX_STRIP_RE = re.compile(
    r"^(?:person|stakeholder|workstream|project|program|system|org|organization|role|role_group|node|entity)[:_-]+",
    re.I,
)


def _table_exists(cursor: sqlite3.Cursor, name: str) -> bool:
    return (
        cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def _col_names(cursor: sqlite3.Cursor, table: str) -> Set[str]:
    pragma_sql = f"PRAGMA table_info({table})"
    return {r[1] for r in cursor.execute(pragma_sql).fetchall()}


def _matches_project_token(nid_low: str, node_tokens: Set[str]) -> bool:
    if nid_low in node_tokens:
        return True
    stripped = _PREFIX_STRIP_RE.sub("", nid_low).replace("-", "_")
    return stripped in node_tokens


def _detect_node_project(node_id: str, node_name: str = "") -> Optional[str]:
    nid_low = (node_id or "").strip().lower()
    name_low = (node_name or "").strip().lower()
    for proj, (node_tokens, pattern) in _PROJECT_SIGNATURES.items():
        if _matches_project_token(nid_low, node_tokens):
            return proj
        if name_low and pattern.search(name_low):
            return proj
    return None


def _detect_text_projects(text: str) -> Set[str]:
    if not text:
        return set()
    return {proj for proj, (_, pattern) in _PROJECT_SIGNATURES.items() if pattern.search(text)}


def _evaluate_assertion_contamination(row: Dict[str, Any]) -> Optional[ClutterFinding]:
    subj_id = str(row.get("subject_node_id") or "")
    obj_id = str(row.get("object_node_id") or "")
    node_proj = _detect_node_project(subj_id, str(row.get("subject_name") or "")) or _detect_node_project(
        obj_id, str(row.get("object_name") or "")
    )
    if not node_proj:
        return None

    claim_blob = " ".join(
        str(row.get(k) or "") for k in ("scope_text", "object_literal", "evidence_quote", "source_title")
    )
    text_projs = _detect_text_projects(claim_blob)
    foreign_projs = sorted(text_projs - {node_proj})
    if not foreign_projs or node_proj in text_projs:
        return None

    aid = str(row["assertion_id"])
    foreign_label = ", ".join(foreign_projs)
    return ClutterFinding(
        finding_type="cross_project_contamination",
        target_id=aid,
        label=f"Cross-project bleed: {subj_id or obj_id} ({node_proj}) attached to {foreign_label} claim",
        details={
            "assertion_id": aid,
            "subject_node_id": subj_id,
            "object_node_id": obj_id,
            "node_project": node_proj,
            "foreign_projects": foreign_projs,
            "scope_text": str(row.get("scope_text") or row.get("object_literal") or "")[:160],
            "source_title": str(row.get("source_title") or ""),
        },
        action_recommended="quarantine_cross_project_assertion",
    )


def _build_node_joins(cursor: sqlite3.Cursor, asrt_cols: Set[str]) -> Tuple[List[str], List[str]]:
    if not _table_exists(cursor, "node"):
        return [], ["NULL AS subject_name", "NULL AS object_name"]
    node_cols = _col_names(cursor, "node")
    nid_col = "node_id" if "node_id" in node_cols else "id"
    nname_col = "canonical_name" if "canonical_name" in node_cols else nid_col
    joins = [f"LEFT JOIN node ns ON a.subject_node_id = ns.{nid_col}"]
    extra = [f"ns.{nname_col} AS subject_name"]
    if "object_node_id" in asrt_cols:
        joins.append(f"LEFT JOIN node no ON a.object_node_id = no.{nid_col}")
        extra.append(f"no.{nname_col} AS object_name")
    else:
        extra.append("NULL AS object_name")
    return joins, extra


def _build_evidence_and_source_joins(cursor: sqlite3.Cursor, asrt_cols: Set[str]) -> Tuple[List[str], List[str]]:
    joins: List[str] = []
    extra: List[str] = []
    if _table_exists(cursor, "assertion_evidence") and _table_exists(cursor, "evidence_item"):
        ei_cols = _col_names(cursor, "evidence_item")
        q_col = "ei.direct_quote" if "direct_quote" in ei_cols else "NULL"
        joins.extend([
            "LEFT JOIN assertion_evidence ae ON a.assertion_id = ae.assertion_id",
            "LEFT JOIN evidence_item ei ON ae.evidence_id = ei.evidence_id",
        ])
        extra.append(f"{q_col} AS evidence_quote")
    else:
        extra.append("NULL AS evidence_quote")

    if _table_exists(cursor, "source") and "primary_source_id" in asrt_cols:
        src_cols = _col_names(cursor, "source")
        sname = "s.source_name" if "source_name" in src_cols else "s.source_id"
        joins.append("LEFT JOIN source s ON a.primary_source_id = s.source_id")
        extra.append(f"{sname} AS source_title")
    else:
        extra.append("NULL AS source_title")
    return joins, extra


def _build_contamination_query(cursor: sqlite3.Cursor, asrt_cols: Set[str]) -> str:
    obj_col = "a.object_node_id" if "object_node_id" in asrt_cols else "NULL AS object_node_id"
    lit_col = "a.object_literal" if "object_literal" in asrt_cols else "NULL AS object_literal"
    scope_col = "a.scope_text" if "scope_text" in asrt_cols else "NULL AS scope_text"
    src_id_col = "a.primary_source_id" if "primary_source_id" in asrt_cols else "NULL AS primary_source_id"

    n_joins, n_extra = _build_node_joins(cursor, asrt_cols)
    e_joins, e_extra = _build_evidence_and_source_joins(cursor, asrt_cols)
    joins = n_joins + e_joins
    extra_cols = n_extra + e_extra

    where_parts = ["1=1"]
    if "currentness_state" in asrt_cols:
        where_parts.append("a.currentness_state = 'current'")
    if "retrieval_eligible" in asrt_cols:
        where_parts.append("a.retrieval_eligible = 1")

    return (
        f"SELECT a.assertion_id, a.subject_node_id, {obj_col}, {lit_col}, {scope_col}, {src_id_col}, "
        f"{', '.join(extra_cols)} FROM assertion a {' '.join(joins)} "
        f"WHERE {' AND '.join(where_parts)} GROUP BY a.assertion_id"
    )


def scan_cross_project_contamination(cursor: sqlite3.Cursor) -> List[ClutterFinding]:
    """Scan active assertions for cross-project entity vs claim/source contamination."""
    if not _table_exists(cursor, "assertion"):
        return []
    asrt_cols = _col_names(cursor, "assertion")
    if not {"assertion_id", "subject_node_id"}.issubset(asrt_cols):
        return []

    sql = _build_contamination_query(cursor, asrt_cols)
    cursor.execute(sql)
    col_list = [d[0] for d in (cursor.description or [])]
    findings: List[ClutterFinding] = []
    for raw_row in cursor.fetchall():
        row = dict(raw_row) if hasattr(raw_row, "keys") else dict(zip(col_list, raw_row))
        finding = _evaluate_assertion_contamination(row)
        if finding is not None:
            findings.append(finding)
    return findings


def scan_leaked_candidate_assertions(cursor: sqlite3.Cursor) -> List[ClutterFinding]:
    """Scan for candidate or unapproved assertions that leaked into retrieval_eligible = 1."""
    if not _table_exists(cursor, "assertion"):
        return []
    asrt_cols = _col_names(cursor, "assertion")
    if not {"assertion_id", "canonical_status", "retrieval_eligible"}.issubset(asrt_cols):
        return []

    elig_cond = "OR coalesce(a.canonical_eligible, 1) = 0" if "canonical_eligible" in asrt_cols else ""
    subj_col = "a.subject_node_id" if "subject_node_id" in asrt_cols else "'' AS subject_node_id"
    pred_col = "a.predicate" if "predicate" in asrt_cols else "'' AS predicate"
    scope_col = (
        "a.scope_text"
        if "scope_text" in asrt_cols
        else ("a.object_literal" if "object_literal" in asrt_cols else "''")
    )

    sql = (
        f"SELECT a.assertion_id, {subj_col}, {pred_col}, a.canonical_status, {scope_col} AS claim_preview "
        f"FROM assertion a "
        f"WHERE a.retrieval_eligible = 1 AND (a.canonical_status = 'candidate' {elig_cond})"
    )
    cursor.execute(sql)
    col_list = [d[0] for d in (cursor.description or [])]
    findings: List[ClutterFinding] = []
    for raw_row in cursor.fetchall():
        r = dict(raw_row) if hasattr(raw_row, "keys") else dict(zip(col_list, raw_row))
        aid = str(r["assertion_id"])
        findings.append(
            ClutterFinding(
                finding_type="leaked_candidate_assertion",
                target_id=aid,
                label=f"Leaked candidate assertion in retrieval lane: {aid} ({r.get('subject_node_id')})",
                details={
                    "assertion_id": aid,
                    "subject_node_id": str(r.get("subject_node_id") or ""),
                    "predicate": str(r.get("predicate") or ""),
                    "canonical_status": str(r.get("canonical_status") or "candidate"),
                    "claim_preview": str(r.get("claim_preview") or "")[:120],
                },
                action_recommended="demote_leaked_candidate_assertion",
            )
        )
    return findings


def _scan_unquarantined_advisory_assertions(
    cursor: sqlite3.Cursor, asrt_cols: Set[str]
) -> List[ClutterFinding]:
    if not {"assertion_id", "retrieval_eligible"}.issubset(asrt_cols):
        return []
    adv_conds: List[str] = []
    if "is_quarantined" in asrt_cols:
        adv_conds.append("coalesce(is_quarantined, 0) = 1")
    if "namespace" in asrt_cols:
        adv_conds.append("upper(coalesce(namespace, '')) = 'INTERNAL_ADVISORY'")
    if "qualifiers_json" in asrt_cols:
        adv_conds.append("qualifiers_json LIKE '%INTERNAL_ADVISORY%'")
    if not adv_conds:
        return []
    sql_adv = f"SELECT assertion_id FROM assertion WHERE retrieval_eligible = 1 AND ({' OR '.join(adv_conds)})"
    return [
        ClutterFinding(
            finding_type="unquarantined_advisory_assertion",
            target_id=str(aid),
            label=f"Quarantined or internal advisory assertion exposed to retrieval: {aid}",
            details={"assertion_id": str(aid), "issue": "internal_advisory_retrieval_eligible"},
            action_recommended="quarantine_advisory_or_orphan_sidecar",
        )
        for (aid,) in cursor.execute(sql_adv).fetchall()
    ]


def _scan_orphan_search_documents(
    cursor: sqlite3.Cursor, asrt_cols: Set[str]
) -> List[ClutterFinding]:
    if not _table_exists(cursor, "search_document") or "assertion_id" not in asrt_cols:
        return []
    sd_cols = _col_names(cursor, "search_document")
    if not {"search_document_id", "assertion_id", "retrieval_eligible"}.issubset(sd_cols):
        return []
    sql_orphan_sd = (
        "SELECT search_document_id, assertion_id FROM search_document "
        "WHERE retrieval_eligible = 1 AND assertion_id IS NOT NULL AND assertion_id != '' "
        "AND assertion_id NOT IN (SELECT assertion_id FROM assertion WHERE assertion_id IS NOT NULL)"
    )
    return [
        ClutterFinding(
            finding_type="orphan_sidecar_document",
            target_id=str(sd_id),
            label=f"Orphan search_document {sd_id} references missing assertion {aid}",
            details={"search_document_id": str(sd_id), "assertion_id": str(aid)},
            action_recommended="quarantine_advisory_or_orphan_sidecar",
        )
        for sd_id, aid in cursor.execute(sql_orphan_sd).fetchall()
    ]


def scan_advisory_and_orphan_sidecar_bleed(cursor: sqlite3.Cursor) -> List[ClutterFinding]:
    """Scan for orphan search_document rows missing backing assertions or unquarantined INTERNAL_ADVISORY rows."""
    if not _table_exists(cursor, "assertion"):
        return []
    asrt_cols = _col_names(cursor, "assertion")
    findings = _scan_unquarantined_advisory_assertions(cursor, asrt_cols)
    findings.extend(_scan_orphan_search_documents(cursor, asrt_cols))
    return findings


def _compatible_group(node_type: str) -> int:
    nt = (node_type or "").strip().lower()
    for idx, grp in enumerate(_CROSS_TYPE_GROUPS):
        if nt in grp:
            return idx
    return -1


def _is_distinct_mece_tier_pair(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    nt_a = str(a.get("node_type") or a.get("type") or "").strip().lower()
    nt_b = str(b.get("node_type") or b.get("type") or "").strip().lower()
    mece_tiers = {"program", "product", "workstream", "mission"}
    if nt_a in mece_tiers and nt_b in mece_tiers and nt_a != nt_b:
        return True
    if {nt_a, nt_b} & {"workstream", "program", "product"} and {nt_a, nt_b} & {"technical_anchor", "system"}:
        combined_text = f"{a.get('canonical_name') or a.get('name') or ''} {b.get('canonical_name') or b.get('name') or ''} {a.get('description', '')} {b.get('description', '')}".lower()
        if any(tok in combined_text for tok in ("2026q", "2027q", "department:", "vertical:", "program:", "product:", "mission ")):
            return True
    return False


def _check_cross_type_pair(
    a: Dict[str, Any],
    b: Dict[str, Any],
    existing_pairs: Set[Tuple[str, str]],
    compare_fn: Any,
) -> Optional[DuplicateEntityFinding]:
    if a.get("node_type") == b.get("node_type") or _is_distinct_mece_tier_pair(a, b):
        return None
    nid_a = str(a.get("node_id") or "")
    nid_b = str(b.get("node_id") or "")
    pair_key = (min(nid_a, nid_b), max(nid_a, nid_b))
    if pair_key in existing_pairs:
        return None
    finding = compare_fn(a, b, str(a.get("node_type") or "entity"))
    if finding is not None:
        finding.node_type_b = str(b.get("node_type") or finding.node_type_a)
        existing_pairs.add(pair_key)
    return finding


def _compare_cross_type_group(
    grp_nodes: List[Dict[str, Any]],
    existing_pairs: Set[Tuple[str, str]],
    compare_fn: Any,
) -> List[DuplicateEntityFinding]:
    twins: List[DuplicateEntityFinding] = []
    cnt = len(grp_nodes)
    for i in range(cnt):
        for j in range(i + 1, cnt):
            finding = _check_cross_type_pair(grp_nodes[i], grp_nodes[j], existing_pairs, compare_fn)
            if finding is not None:
                twins.append(finding)
    return twins


def scan_split_prefix_identity_twins(
    nodes: List[Dict[str, Any]],
    existing_pairs: Set[Tuple[str, str]],
    compare_fn: Any,
) -> List[DuplicateEntityFinding]:
    """Detect cross-type and colon-vs-underscore prefix twin entities (e.g. person_amit vs person:amit-ayer)."""
    by_group: Dict[int, List[Dict[str, Any]]] = {}
    for n in nodes:
        grp = _compatible_group(str(n.get("node_type") or ""))
        if grp >= 0:
            by_group.setdefault(grp, []).append(n)

    twins: List[DuplicateEntityFinding] = []
    for grp_nodes in by_group.values():
        twins.extend(_compare_cross_type_group(grp_nodes, existing_pairs, compare_fn))
    return twins


def scan_retrieval_antipatterns(conn: sqlite3.Connection) -> List[ClutterFinding]:
    """Run all read-only retrieval antipattern, speaker/diarization, and qualifier scans."""
    if conn.row_factory is None:
        conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    findings: List[ClutterFinding] = []
    findings.extend(scan_cross_project_contamination(cursor))
    findings.extend(scan_leaked_candidate_assertions(cursor))
    findings.extend(scan_advisory_and_orphan_sidecar_bleed(cursor))
    findings.extend(scan_october_retrieval_effectiveness_gaps(cursor))
    quarantined_ids = {str(f.target_id) for f in findings}
    for f in scan_speaker_and_diarization_defects(cursor):
        if str(f.target_id) not in quarantined_ids:
            findings.append(f)
    for f in scan_qualifier_metadata_drift(cursor):
        if str(f.target_id) not in quarantined_ids:
            findings.append(f)
    return findings
