"""Read-only scanners for October 27-28 graph retrieval effectiveness blindspots."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from storage.database import sqlite3

from .models import ClutterFinding

_UNRESOLVED_DESC_RE = re.compile(
    r"\b(?:remain[s]?\s+unresolved|unresolved|engineering\s+team\s+member|unknown\s+role|exact\s+title)\b",
    re.I,
)
_OPS_ONLY_RE = re.compile(
    r"\b(?:100%\s+operations|onboarding,\s*access,\s*laptops|pure\s+partner_advisory\s+nee\s+operations)\b",
    re.I,
)
_EXEC_BIO_RE = re.compile(
    r"\b(?:account\s+executive|executive\s+leadership|engagement\s+director|client\s+partner|managing\s+director|executive\s+sponsor)\b",
    re.I,
)
_DELEGATED_SPRINT_RE = re.compile(
    r"\b(?:should\s+align\s+to|aligned\s+to|sprint\s+development\s+task|pod\s+delivery\s+lead)\b",
    re.I,
)


def _has_table(cursor: sqlite3.Cursor, name: str) -> bool:
    return (
        cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def _cols(cursor: sqlite3.Cursor, table: str) -> Set[str]:
    pragma_sql = f"PRAGMA table_info({table})"
    return {r[1] for r in cursor.execute(pragma_sql).fetchall()}


def _is_stale_description(desc: str) -> bool:
    cleaned = (desc or "").strip()
    if not cleaned:
        return True
    return bool(_UNRESOLVED_DESC_RE.search(cleaned))


def _load_authoritative_roles(
    cursor: sqlite3.Cursor, asrt_cols: Set[str], node_cols: Set[str]
) -> Dict[str, Tuple[str, str, str]]:
    if not {"assertion_id", "subject_node_id", "predicate"}.issubset(asrt_cols):
        return {}
    obj_join = ""
    role_expr = "coalesce(a.object_literal, a.scope_text, '')"
    if "object_node_id" in asrt_cols and {"node_id", "canonical_name"}.issubset(node_cols):
        obj_join = "LEFT JOIN node ro ON a.object_node_id = ro.node_id"
        role_expr = "coalesce(ro.canonical_name, a.object_literal, a.scope_text, '')"
    desc_expr = "coalesce(ns.description, '')" if "description" in node_cols else "''"
    curr_cond = "AND a.currentness_state = 'current'" if "currentness_state" in asrt_cols else ""
    can_cond = "AND a.canonical_status = 'canonical'" if "canonical_status" in asrt_cols else ""
    sql = (
        f"SELECT a.subject_node_id, a.assertion_id, {role_expr} AS role_label, {desc_expr} AS subj_desc "
        f"FROM assertion a LEFT JOIN node ns ON a.subject_node_id = ns.node_id {obj_join} "
        f"WHERE a.predicate IN ('HAS_ROLE_IN', 'HAS_ROLE') {curr_cond} {can_cond}"
    )
    roles: Dict[str, Tuple[str, str, str]] = {}
    for subj_id, aid, role_label, subj_desc in cursor.execute(sql).fetchall():
        if subj_id and role_label and str(role_label).strip():
            roles[str(subj_id)] = (str(aid), str(role_label).strip(), str(subj_desc or ""))
    return roles


def _find_stale_role_caveat_ids(
    cursor: sqlite3.Cursor, asrt_cols: Set[str], subject_id: str, winner_aid: str
) -> List[str]:
    text_expr = (
        "coalesce(scope_text, '') || ' ' || coalesce(object_literal, '')"
        if {"scope_text", "object_literal"}.issubset(asrt_cols)
        else "''"
    )
    where = ["subject_node_id = ?", "assertion_id != ?"]
    if "currentness_state" in asrt_cols:
        where.append("currentness_state = 'current'")
    if "retrieval_eligible" in asrt_cols:
        where.append("retrieval_eligible = 1")
    if "superseded_by_assertion_id" in asrt_cols:
        where.append("(superseded_by_assertion_id IS NULL OR superseded_by_assertion_id = '')")
    sql = f"SELECT assertion_id, {text_expr} FROM assertion WHERE {' AND '.join(where)}"
    stale_ids: List[str] = []
    for aid, blob in cursor.execute(sql, (subject_id, winner_aid)).fetchall():
        if _UNRESOLVED_DESC_RE.search(str(blob or "")):
            stale_ids.append(str(aid))
    return stale_ids


def scan_stale_role_and_unresolved_descriptions(cursor: sqlite3.Cursor) -> List[ClutterFinding]:
    """Detect frozen/unresolved stakeholder descriptions and unsuperseded role caveat claims."""
    if not (_has_table(cursor, "assertion") and _has_table(cursor, "node")):
        return []
    asrt_cols = _cols(cursor, "assertion")
    node_cols = _cols(cursor, "node")
    auth_roles = _load_authoritative_roles(cursor, asrt_cols, node_cols)
    findings: List[ClutterFinding] = []
    for subj_id, (winner_aid, role_label, subj_desc) in auth_roles.items():
        stale_aids = _find_stale_role_caveat_ids(cursor, asrt_cols, subj_id, winner_aid)
        desc_stale = _is_stale_description(subj_desc)
        if not stale_aids and not desc_stale:
            continue
        target_id = stale_aids[0] if stale_aids else subj_id
        findings.append(
            ClutterFinding(
                finding_type="stale_role_or_bio_caveat",
                target_id=target_id,
                label=f"Stale role caveat or unresolved bio on {subj_id} (resolved role: {role_label})",
                details={
                    "node_id": subj_id,
                    "subject_node_id": subj_id,
                    "winner_assertion_id": winner_aid,
                    "assertion_id": stale_aids[0] if stale_aids else "",
                    "stale_assertion_ids": stale_aids,
                    "resolved_role": role_label,
                    "stale_description": subj_desc,
                },
                action_recommended="reconcile_stale_role_and_description",
            )
        )
    return findings


def _load_targetable_entities(cursor: sqlite3.Cursor, node_cols: Set[str]) -> List[Tuple[str, str, re.Pattern[str]]]:
    if not {"node_id", "canonical_name"}.issubset(node_cols):
        return []
    act_cond = "WHERE coalesce(active, 1) = 1" if "active" in node_cols else ""
    sql = f"SELECT node_id, canonical_name FROM node {act_cond}"
    targets: List[Tuple[str, str, re.Pattern[str]]] = []
    for nid, cname in cursor.execute(sql).fetchall():
        name_str = str(cname or "").strip()
        if not nid or len(name_str) < 6 or " " not in name_str:
            continue
        pat = re.compile(r"\b" + re.escape(name_str) + r"\b", re.I)
        targets.append((str(nid), name_str, pat))
    targets.sort(key=lambda item: len(item[1]), reverse=True)
    return targets


def _match_literal_target(
    subj_id: str, text_blob: str, targets: List[Tuple[str, str, re.Pattern[str]]]
) -> Optional[Tuple[str, str]]:
    if not text_blob:
        return None
    for nid, cname, pat in targets:
        if nid != subj_id and pat.search(text_blob):
            return nid, cname
    return None


def _build_literal_mention_sql(asrt_cols: Set[str]) -> str:
    text_expr = (
        "coalesce(scope_text, '') || ' ' || coalesce(object_literal, '')"
        if {"scope_text", "object_literal"}.issubset(asrt_cols)
        else ("coalesce(object_literal, '')" if "object_literal" in asrt_cols else "''")
    )
    where = ["(object_node_id IS NULL OR object_node_id = '')", "predicate != 'HAS_CLAIM_TEXT'"]
    if "currentness_state" in asrt_cols:
        where.append("currentness_state = 'current'")
    if "canonical_status" in asrt_cols:
        where.append("canonical_status = 'canonical'")
    if "retrieval_eligible" in asrt_cols:
        where.append("retrieval_eligible = 1")
    return f"SELECT assertion_id, subject_node_id, predicate, {text_expr} FROM assertion WHERE {' AND '.join(where)}"


def scan_literal_only_entity_mentions(cursor: sqlite3.Cursor) -> List[ClutterFinding]:
    """Detect canonical assertions missing object_node_id that explicitly name an active entity."""
    if not (_has_table(cursor, "assertion") and _has_table(cursor, "node")):
        return []
    asrt_cols = _cols(cursor, "assertion")
    if not {"assertion_id", "subject_node_id", "object_node_id", "predicate"}.issubset(asrt_cols):
        return []
    targets = _load_targetable_entities(cursor, _cols(cursor, "node"))
    if not targets:
        return []

    sql = _build_literal_mention_sql(asrt_cols)
    findings: List[ClutterFinding] = []
    for aid, subj_id, pred, blob in cursor.execute(sql).fetchall():
        matched = _match_literal_target(str(subj_id or ""), str(blob or ""), targets)
        if matched is None:
            continue
        target_nid, target_name = matched
        findings.append(
            ClutterFinding(
                finding_type="literal_only_entity_mention",
                target_id=str(aid),
                label=f"Literal-only {pred} assertion {aid} missing object_node_id link to {target_name} ({target_nid})",
                details={
                    "assertion_id": str(aid),
                    "subject_node_id": str(subj_id or ""),
                    "predicate": str(pred or ""),
                    "target_node_id": target_nid,
                    "target_canonical_name": target_name,
                },
                action_recommended="rebind_literal_entity_mention_edge",
            )
        )
    return findings


def _classify_disjoint_or_owner_bleed(row: Tuple[Any, ...]) -> Optional[str]:
    _, _, pred, obj_id, qual_json, text_blob, subj_desc = row
    qual_low = str(qual_json or "").lower()
    combined_text = f"{text_blob or ''} {subj_desc or ''}"
    obj_low = str(obj_id or "").lower()
    if "disjoint_workstream_exclusion" in qual_low:
        return "disjoint_workstream_exclusion_active"
    if _OPS_ONLY_RE.search(combined_text) and ("workstream" in obj_low or "project" in obj_low or "sherpa" in obj_low):
        return "operations_only_resource_on_delivery_workstream"
    if (
        str(pred or "").upper() in ("OWNS_OR_LEADS", "HAS_ACTION_OWNER", "OWNS")
        and _DELEGATED_SPRINT_RE.search(str(text_blob or ""))
        and (_EXEC_BIO_RE.search(str(subj_desc or "")) or "should align to" in str(text_blob or "").lower())
    ):
        return "executive_misattributed_as_pod_delivery_owner"
    return None


def _build_disjoint_bleed_sql(cursor: sqlite3.Cursor, asrt_cols: Set[str]) -> str:
    node_cols = _cols(cursor, "node") if _has_table(cursor, "node") else set()
    node_join = "LEFT JOIN node ns ON a.subject_node_id = ns.node_id" if "node_id" in node_cols else ""
    desc_col = "coalesce(ns.description, '')" if "description" in node_cols else "''"
    obj_col = "coalesce(a.object_node_id, '')" if "object_node_id" in asrt_cols else "''"
    qual_col = "coalesce(a.qualifiers_json, '')" if "qualifiers_json" in asrt_cols else "''"
    text_col = (
        "coalesce(a.scope_text, '') || ' ' || coalesce(a.object_literal, '')"
        if {"scope_text", "object_literal"}.issubset(asrt_cols)
        else "''"
    )
    where = ["1=1"]
    if "currentness_state" in asrt_cols:
        where.append("a.currentness_state = 'current'")
    if "retrieval_eligible" in asrt_cols:
        where.append("a.retrieval_eligible = 1")
    return (
        f"SELECT a.assertion_id, a.subject_node_id, a.predicate, {obj_col}, {qual_col}, {text_col}, {desc_col} "
        f"FROM assertion a {node_join} WHERE {' AND '.join(where)}"
    )


def scan_disjoint_role_and_owner_bleed(cursor: sqlite3.Cursor) -> List[ClutterFinding]:
    """Detect disjoint operations roster bleed and executive owner misattribution."""
    if not _has_table(cursor, "assertion"):
        return []
    asrt_cols = _cols(cursor, "assertion")
    if not {"assertion_id", "subject_node_id", "predicate"}.issubset(asrt_cols):
        return []
    sql = _build_disjoint_bleed_sql(cursor, asrt_cols)
    findings: List[ClutterFinding] = []
    for row in cursor.execute(sql).fetchall():
        issue = _classify_disjoint_or_owner_bleed(row)
        if issue is None:
            continue
        aid, subj_id, pred, obj_id = str(row[0]), str(row[1] or ""), str(row[2] or ""), str(row[3] or "")
        findings.append(
            ClutterFinding(
                finding_type="disjoint_role_or_owner_bleed",
                target_id=aid,
                label=f"Disjoint role or owner bleed ({issue}): {subj_id} --[{pred}]--> {obj_id}",
                details={
                    "assertion_id": aid,
                    "subject_node_id": subj_id,
                    "predicate": pred,
                    "object_node_id": obj_id,
                    "issue": issue,
                },
                action_recommended="quarantine_disjoint_role_or_owner_bleed",
            )
        )
    return findings


def scan_october_retrieval_effectiveness_gaps(cursor: sqlite3.Cursor) -> List[ClutterFinding]:
    """Run all October 27-28 retrieval effectiveness gap scanners."""
    findings: List[ClutterFinding] = []
    findings.extend(scan_stale_role_and_unresolved_descriptions(cursor))
    findings.extend(scan_literal_only_entity_mentions(cursor))
    findings.extend(scan_disjoint_role_and_owner_bleed(cursor))
    return findings
