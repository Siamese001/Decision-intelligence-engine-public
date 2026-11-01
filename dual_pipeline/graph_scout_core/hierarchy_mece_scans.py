"""Read-only MECE portfolio hierarchy, compound-node, ownership, and retrieval-breadcrumb scanners."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from storage.database import sqlite3

from .models import ClutterFinding

_CORP_SUFFIX_RE = re.compile(
    r",\s*(?:\d+\s+of\s+\d+|outside\s+|inc\.?|llc\.?|ltd\.?|corp\.?|co\.?|jr\.?|sr\.?|ii|iii|iv)\b", re.I
)
_INVERTED_PERSON_RE = re.compile(r"^([A-Z][a-zA-Z'’-]+),\s+([A-Z][a-zA-Z'’-]+(?:\s+[A-Z][a-zA-Z'’-]+)?)$")
_LEADING_NUM_RE = re.compile(r"^\d+\.\s*")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_ROLE_INLINE_RE = re.compile(
    r"(?:Product\s+Manager|Program\s+Owner|Engineering\s+Lead|Scrum\s+Master|\bPM|\bSM)\s*:\s*([A-Z][a-zA-Z'’.-]+(?:\s+[A-Z][a-zA-Z'’.-]+){1,3})",
)


def _table_exists(cursor: sqlite3.Cursor, name: str) -> bool:
    return cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _col_names(cursor: sqlite3.Cursor, table: str) -> Set[str]:
    clean_table = re.sub(r"[^a-zA-Z0-9_]", "", table)
    query = f"PRAGMA table_info({clean_table})"
    return {r[1] for r in cursor.execute(query).fetchall()}


def _norm_slug(text: str) -> str:
    clean = re.sub(r"\([^)]*\)", " ", (text or "").lower())
    clean = re.sub(r"^(?:org|workstream|program|product|person|stakeholder|entity)[:_-]+", "", clean)
    return _NON_ALNUM_RE.sub("_", clean).strip("_")


def _detect_comma_merged_node(nid: str, cname: str, ntype: str) -> Optional[ClutterFinding]:
    no_paren = re.sub(r"\([^)]*\)", "", cname)
    if ntype not in ("product", "workstream", "program") or "," not in no_paren or _CORP_SUFFIX_RE.search(cname):
        return None
    raw_parts = [re.sub(r"^(?:&\s*|and\s+)", "", p.strip(), flags=re.I).strip() for p in cname.split(",")]
    raw_parts = [p for p in raw_parts if len(p) >= 3]
    if len(raw_parts) < 2:
        return None
    prefix = f"{raw_parts[0].split(' - ', 1)[0]} - " if " - " in raw_parts[0] else ""
    parts = [raw_parts[0]] + [f"{prefix}{p}" if (prefix and len(p.split()) == 1 and " - " not in p) else p for p in raw_parts[1:]]
    return ClutterFinding(
        finding_type="compound_multi_entity_node",
        target_id=nid,
        label=f"Comma-merged {ntype} node '{cname}' combines {len(parts)} distinct entities",
        details={
            "node_id": nid, "canonical_name": cname, "node_type": ntype,
            "expected_canonical_name": parts[0], "expected_node_type": ntype,
            "issue_subtype": "comma_merged_entities", "split_names": parts,
        },
        action_recommended="split_or_normalize_compound_node",
    )


def _detect_malformed_or_inverted_name(nid: str, cname: str, ntype: str) -> Optional[ClutterFinding]:
    if ntype in ("stakeholder", "person"):
        m_inv = _INVERTED_PERSON_RE.match(cname.strip())
        if m_inv:
            fixed = f"{m_inv.group(2).strip()} {m_inv.group(1).strip()}"
            return ClutterFinding(
                finding_type="inverted_stakeholder_name",
                target_id=nid,
                label=f"Comma-inverted stakeholder name '{cname}' -> '{fixed}'",
                details={
                    "node_id": nid, "canonical_name": cname, "node_type": ntype,
                    "expected_canonical_name": fixed, "expected_node_type": ntype,
                    "issue_subtype": "inverted_person_name", "normalized_name": fixed,
                },
                action_recommended="split_or_normalize_compound_node",
            )
    if ntype not in ("product", "workstream", "program") or "..." in cname:
        return None
    has_unclosed_paren = cname.count("(") > cname.count(")")
    has_leading_num = bool(_LEADING_NUM_RE.match(cname))
    if not (has_unclosed_paren or has_leading_num):
        return None
    fixed_name = _LEADING_NUM_RE.sub("", cname).strip()
    if fixed_name.count("(") > fixed_name.count(")"):
        fixed_name += ")"
    return ClutterFinding(
        finding_type="malformed_truncated_node_name",
        target_id=nid,
        label=f"Malformed or truncated {ntype} name '{cname}' -> '{fixed_name}'",
        details={
            "node_id": nid, "canonical_name": cname, "node_type": ntype,
            "expected_canonical_name": fixed_name, "expected_node_type": ntype,
            "issue_subtype": "truncated_or_numbered_name", "normalized_name": fixed_name,
        },
        action_recommended="split_or_normalize_compound_node",
    )


def _scan_collapsed_hierarchy_merges(cursor: sqlite3.Cursor, node_cols: Set[str]) -> List[ClutterFinding]:
    findings: List[ClutterFinding] = []
    if {"node_id", "canonical_name", "node_type", "active", "status"}.issubset(node_cols) and _table_exists(cursor, "entity_redirect"):
        rows = cursor.execute(
            """SELECT r.from_node_id, n_from.canonical_name, n_from.node_type,
                      r.to_node_id, n_to.canonical_name, n_to.node_type
                 FROM entity_redirect r
                 JOIN node n_from ON n_from.node_id = r.from_node_id
                 JOIN node n_to ON n_to.node_id = r.to_node_id
                WHERE r.active = 1
                  AND n_from.node_type IN ('workstream', 'program', 'product')
                  AND n_to.node_type IN ('technical_anchor', 'system')
                  AND (LOWER(n_from.canonical_name) LIKE '%2026%' OR LOWER(r.from_node_id) LIKE '%2026%')"""
        ).fetchall()
        for from_id, from_name, from_type, to_id, to_name, to_type in rows:
            findings.append(
                ClutterFinding(
                    finding_type="mistyped_or_collapsed_hierarchy_node",
                    target_id=str(from_id),
                    label=f"Hierarchy node '{from_name}' ({from_type}) destructively merged into '{to_name}' ({to_type})",
                    details={
                        "node_id": str(from_id), "canonical_name": str(from_name), "node_type": str(from_type),
                        "expected_canonical_name": str(from_name), "expected_node_type": str(from_type),
                        "merged_into_node_id": str(to_id), "merged_into_type": str(to_type),
                        "issue_subtype": "cross_tier_destructive_merge",
                    },
                    action_recommended="split_or_normalize_compound_node",
                )
            )
    rows_part = cursor.execute(
        """SELECT node_id, canonical_name, node_type FROM node
            WHERE active = 1 AND node_type = 'program'
              AND (LOWER(canonical_name) LIKE '%part 1%' OR LOWER(canonical_name) LIKE '%part 2%' OR LOWER(canonical_name) LIKE '%mission %')"""
    ).fetchall()
    for nid, cname, ntype in rows_part:
        findings.append(
            ClutterFinding(
                finding_type="mistyped_or_collapsed_hierarchy_node",
                target_id=str(nid),
                label=f"Mission/workstream node '{cname}' mis-typed as '{ntype}' instead of 'workstream'",
                details={
                    "node_id": str(nid), "canonical_name": str(cname), "node_type": str(ntype),
                    "target_node_type": "workstream", "expected_canonical_name": str(cname),
                    "expected_node_type": "workstream", "issue_subtype": "mistyped_hierarchy_tier",
                },
                action_recommended="split_or_normalize_compound_node",
            )
        )
    return findings


def scan_compound_and_malformed_nodes(conn: sqlite3.Connection) -> List[ClutterFinding]:
    """Detect comma-merged products/workstreams, unclosed parens, inverted names, and cross-tier merge collisions."""
    cursor = conn.cursor()
    if not _table_exists(cursor, "node"):
        return []
    node_cols = _col_names(cursor, "node")
    if not {"node_id", "canonical_name", "node_type"}.issubset(node_cols):
        return []
    query = (
        "SELECT node_id, canonical_name, coalesce(node_type, 'entity') FROM node WHERE active = 1"
        if "active" in node_cols
        else "SELECT node_id, canonical_name, coalesce(node_type, 'entity') FROM node"
    )
    rows = cursor.execute(query).fetchall()
    findings: List[ClutterFinding] = []
    for nid, cname, ntype in rows:
        nid_s, cname_s, ntype_s = str(nid or ""), str(cname or "").strip(), str(ntype or "").lower()
        f_node = (_detect_comma_merged_node(nid_s, cname_s, ntype_s) or _detect_malformed_or_inverted_name(nid_s, cname_s, ntype_s)) if (nid_s and cname_s) else None
        if f_node is not None:
            findings.append(f_node)
    findings.extend(_scan_collapsed_hierarchy_merges(cursor, node_cols))
    return findings


def _evaluate_slug_twin_group(group: List[Tuple[str, str, str, int]]) -> List[ClutterFinding]:
    if len(group) < 2:
        return []
    active_members = [m for m in group if m[3] > 0]
    orphan_members = [m for m in group if m[3] == 0 and ":" in m[0]]
    if not active_members or not orphan_members:
        return []
    survivor = sorted(active_members, key=lambda x: (-x[3], x[0]))[0]
    return [
        ClutterFinding(
            finding_type="orphan_prefix_twin_node",
            target_id=orph[0],
            label=f"Orphan prefix twin '{orph[0]}' ({orph[1]}) duplicates active '{survivor[0]}' ({survivor[1]})",
            details={
                "node_id": orph[0], "retired_node_id": orph[0],
                "survivor_node_id": survivor[0], "canonical_name": survivor[1],
                "issue_subtype": "orphan_prefix_twin",
            },
            action_recommended="reconcile_hierarchy_and_ownership_edges",
        )
        for orph in orphan_members
        if orph[0] != survivor[0]
    ]


def _scan_orphan_prefix_twins(cursor: sqlite3.Cursor) -> List[ClutterFinding]:
    rows = cursor.execute(
        """SELECT n.node_id, n.canonical_name, coalesce(n.node_type, 'entity'),
                  (SELECT count(*) FROM assertion a WHERE a.currentness_state='current' AND (a.subject_node_id=n.node_id OR a.object_node_id=n.node_id)) AS acnt
             FROM node n WHERE n.active = 1"""
    ).fetchall()
    by_slug: Dict[str, List[Tuple[str, str, str, int]]] = {}
    for nid, cname, ntype, acnt in rows:
        slug = _norm_slug(str(cname or nid))
        if slug:
            by_slug.setdefault(slug, []).append((str(nid), str(cname or ""), str(ntype or ""), int(acnt or 0)))
    return [f for group in by_slug.values() for f in _evaluate_slug_twin_group(group)]


def _scan_role_hub_detours(cursor: sqlite3.Cursor) -> List[ClutterFinding]:
    detour_rows = cursor.execute(
        """SELECT DISTINCT a_role.subject_node_id, n_p.canonical_name, a_role.object_node_id, a_role.primary_source_id,
                  n_t.node_id, n_t.canonical_name, n_t.node_type
             FROM assertion a_role
             JOIN node n_p ON n_p.node_id = a_role.subject_node_id AND n_p.active = 1
             JOIN assertion a_tgt ON a_tgt.primary_source_id = a_role.primary_source_id
             JOIN node n_t ON n_t.node_id = a_tgt.subject_node_id AND n_t.active = 1
            WHERE a_role.predicate IN ('HAS_ROLE_IN', 'HAS_ROLE')
              AND (LOWER(a_role.object_node_id) LIKE '%product-manager%' OR LOWER(a_role.object_node_id) LIKE '%engineering-lead%' OR LOWER(a_role.object_node_id) LIKE '%program-manager%')
              AND n_t.node_type IN ('product', 'program', 'workstream')
              AND NOT EXISTS (
                  SELECT 1 FROM assertion a_own
                   WHERE a_own.subject_node_id = a_role.subject_node_id
                     AND a_own.object_node_id = n_t.node_id
                     AND a_own.predicate = 'OWNS_OR_LEADS'
                     AND a_own.currentness_state = 'current'
              )"""
    ).fetchall()
    return [
        ClutterFinding(
            finding_type="missing_hierarchy_or_ownership_edge",
            target_id=str(t_id),
            label=f"Role-hub detour: '{p_name}' ({role_id}) lacks direct OWNS_OR_LEADS edge to {t_type} '{t_name}'",
            details={
                "node_id": str(t_id), "subject_node_id": str(p_id), "object_node_id": str(t_id),
                "predicate": "OWNS_OR_LEADS", "role_node_id": str(role_id),
                "source_id": str(src_id or ""), "issue_subtype": "role_hub_ownership_detour",
            },
            action_recommended="reconcile_hierarchy_and_ownership_edges",
        )
        for p_id, p_name, role_id, src_id, t_id, t_name, t_type in detour_rows
    ]


def _scan_unwired_inline_owners(cursor: sqlite3.Cursor, seen_targets: Set[str]) -> List[ClutterFinding]:
    claim_rows = cursor.execute(
        """SELECT n.node_id, n.canonical_name, n.node_type, coalesce(a.scope_text, a.object_literal, ''), a.primary_source_id
             FROM node n
             JOIN assertion a ON a.subject_node_id = n.node_id
            WHERE n.active = 1 AND n.node_type IN ('product', 'program', 'workstream')
              AND a.currentness_state = 'current'
              AND (a.scope_text LIKE '%PM:%' OR a.scope_text LIKE '%Product Manager:%' OR a.scope_text LIKE '%Program Owner:%' OR a.scope_text LIKE '%Engineering Lead:%')
              AND NOT EXISTS (
                  SELECT 1 FROM assertion a2
                   WHERE a2.object_node_id = n.node_id AND a2.predicate = 'OWNS_OR_LEADS' AND a2.currentness_state = 'current'
              )"""
    ).fetchall()
    findings: List[ClutterFinding] = []
    for nid, cname, ntype, text_blob, src_id in claim_rows:
        nid_s = str(nid)
        names = [m.strip() for m in _ROLE_INLINE_RE.findall(str(text_blob)) if len(m.strip()) >= 4]
        if nid_s not in seen_targets and names:
            seen_targets.add(nid_s)
            findings.append(
                ClutterFinding(
                    finding_type="missing_hierarchy_or_ownership_edge",
                    target_id=nid_s,
                    label=f"{ntype.title()} '{cname}' names owner(s) {names} in text but lacks canonical OWNS_OR_LEADS edge",
                    details={
                        "node_id": nid_s, "object_node_id": nid_s, "owner_names": names,
                        "source_id": str(src_id or ""), "issue_subtype": "unwired_inline_owner",
                    },
                    action_recommended="reconcile_hierarchy_and_ownership_edges",
                )
            )
    return findings


def _scan_misrouted_portfolio_candidates(cursor: sqlite3.Cursor) -> List[ClutterFinding]:
    if not (_table_exists(cursor, "source") and _table_exists(cursor, "assertion_evidence")):
        return []
    asrt_cols = _col_names(cursor, "assertion")
    src_cols = _col_names(cursor, "source")
    if not {"assertion_id", "canonical_status", "primary_source_id", "confidence_score"}.issubset(asrt_cols):
        return []
    sname_col = "s.source_name" if "source_name" in src_cols else "s.source_id"
    slabel_expr = f"coalesce({sname_col}, '') || ' ' || coalesce(s.title, '')" if "title" in src_cols else f"coalesce({sname_col}, '')"
    rows = cursor.execute(
        f"""SELECT a.primary_source_id, {sname_col}, COUNT(DISTINCT a.assertion_id), MIN(a.assertion_id)
              FROM assertion a
              JOIN source s ON s.source_id = a.primary_source_id
              JOIN assertion_evidence ae ON ae.assertion_id = a.assertion_id
             WHERE a.canonical_status = 'candidate' AND a.currentness_state = 'current'
               AND coalesce(a.confidence_score, 0.0) >= 0.80
               AND (LOWER({slabel_expr}) LIKE '%program%planning%' OR LOWER({slabel_expr}) LIKE '%charter%'
                    OR LOWER({slabel_expr}) LIKE '%portfolio%' OR LOWER({slabel_expr}) LIKE '%workstreams%headcount%')
             GROUP BY a.primary_source_id, {sname_col}"""
    ).fetchall()
    findings: List[ClutterFinding] = []
    for src_id, src_name, cnt, sample_aid in rows:
        aids = [
            str(r[0])
            for r in cursor.execute(
                """SELECT DISTINCT a.assertion_id FROM assertion a
                     JOIN assertion_evidence ae ON ae.assertion_id = a.assertion_id
                    WHERE a.primary_source_id = ? AND a.canonical_status = 'candidate'
                      AND a.currentness_state = 'current' AND coalesce(a.confidence_score, 0.0) >= 0.80""",
                (src_id,),
            ).fetchall()
        ]
        if aids:
            findings.append(
                ClutterFinding(
                    finding_type="misrouted_portfolio_candidate_batch",
                    target_id=str(sample_aid),
                    label=f"Source '{src_name}' ({src_id}) has {cnt} grounded high-confidence portfolio assertions quarantined as candidate",
                    details={
                        "assertion_id": str(sample_aid), "source_id": str(src_id),
                        "source_name": str(src_name or ""), "promotable_ids": aids,
                        "issue_subtype": "misrouted_portfolio_candidates",
                    },
                    action_recommended="reconcile_hierarchy_and_ownership_edges",
                )
            )
    return findings


def scan_hierarchy_connectivity_and_ownership_gaps(conn: sqlite3.Connection) -> List[ClutterFinding]:
    """Detect orphan prefix twins, role-hub ownership detours, misrouted portfolio candidates, and broken 6-tier MECE hierarchy links."""
    cursor = conn.cursor()
    if not (_table_exists(cursor, "node") and _table_exists(cursor, "assertion")):
        return []
    findings = _scan_orphan_prefix_twins(cursor)
    detours = _scan_role_hub_detours(cursor)
    findings.extend(detours)
    findings.extend(_scan_unwired_inline_owners(cursor, {f.target_id for f in detours}))
    findings.extend(_scan_misrouted_portfolio_candidates(cursor))
    return findings


def scan_retrieval_synthesis_breadcrumb_drift(conn: sqlite3.Connection) -> List[ClutterFinding]:
    """Detect active canonical portfolio assertions/nodes whose search_document lacks synthesized MECE breadcrumbs."""
    cursor = conn.cursor()
    if not (_table_exists(cursor, "node") and _table_exists(cursor, "assertion") and _table_exists(cursor, "search_document")):
        return []
    if not {"search_document_id", "body", "retrieval_eligible"}.issubset(_col_names(cursor, "search_document")):
        return []
    if not cursor.execute("SELECT 1 FROM node WHERE active = 1 AND node_type IN ('program', 'product') LIMIT 1").fetchone():
        return []
    rows = cursor.execute(
        """SELECT a.assertion_id, n.node_id
             FROM assertion a
             JOIN node n ON n.node_id = a.subject_node_id
             LEFT JOIN search_document sd ON sd.assertion_id = a.assertion_id
            WHERE n.active = 1 AND n.node_type IN ('program', 'product', 'workstream', 'mission')
              AND a.canonical_status = 'canonical' AND a.currentness_state = 'current' AND a.retrieval_eligible = 1
              AND (sd.search_document_id IS NULL OR sd.retrieval_eligible = 0 OR coalesce(sd.body, '') NOT LIKE '%[MECE Hierarchy:%')"""
    ).fetchall()
    if not rows:
        return []
    aids, nids = [str(r[0]) for r in rows], sorted({str(r[1]) for r in rows})
    return [
        ClutterFinding(
            finding_type="retrieval_breadcrumb_drift",
            target_id=aids[0],
            label=f"{len(aids)} active portfolio assertion(s) across {len(nids)} node(s) lack synthesized MECE hierarchy retrieval breadcrumbs",
            details={
                "assertion_id": aids[0], "unsynced_assertion_ids": aids[:200],
                "unsynced_node_ids": nids[:200], "total_unsynced_assertions": len(aids), "total_unsynced_nodes": len(nids),
            },
            action_recommended="synthesize_hierarchical_retrieval_docs",
        )
    ]


def audit_mece_hierarchy_findings(conn: sqlite3.Connection, finding_cls: Any) -> List[Any]:
    """Run compound-node and MECE hierarchy/ownership scanners and wrap into hygiene Finding objects."""
    raw = scan_compound_and_malformed_nodes(conn) + scan_hierarchy_connectivity_and_ownership_gaps(conn)
    return [
        finding_cls(
            detective="StructuralValidator", severity="HIGH", description=item.label,
            entity_references=[item.target_id], proposed_fix=item.action_recommended, raw_data=item.details,
        )
        for item in raw
    ]


def audit_retrieval_breadcrumb_findings(conn: sqlite3.Connection, finding_cls: Any) -> List[Any]:
    """Run retrieval-synthesis breadcrumb drift scanner and wrap into hygiene Finding objects."""
    return [
        finding_cls(
            detective="IndexSynchronizer", severity="MEDIUM", description=item.label,
            entity_references=[item.target_id], proposed_fix=item.action_recommended, raw_data=item.details,
        )
        for item in scan_retrieval_synthesis_breadcrumb_drift(conn)
    ]
