"""
dual_pipeline/graph_scout_core/scout_helpers.py

Helper functions and constants for GraphScout node deduplication and scoping.
Decomposed to enforce cognitive complexity <= 15 and module size <= 400 lines.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from ..parameters import get_hitl_ambiguity_margin_threshold
from .matching import _string_similarity
from .models import DuplicateEntityFinding

# Wave 1: Pre-mutation deduplication constants (P8)
_ID_SEP_COLON = ":"
_ID_SEP_UNDERSCORE = "_"

# P6: Compatible types for cross-type duplicate detection
_TYPE_COMPAT_MAP: Dict[str, Set[str]] = {
    "role": {"stakeholder", "role_group"},
    "org": {"org"},
    "person": {"person", "person_profile", "stakeholder", "internal_person"},
    "stakeholder": {"person", "person_profile", "stakeholder", "internal_person"},
    "internal_person": {"person", "person_profile", "stakeholder", "internal_person"},
    "system": {"system", "tool", "platform"},
}


def _evaluate_proposed_pair(
    eid: str,
    ename: str,
    etype: str,
    edesc: str,
    db_node: Dict[str, Any],
    db_type: str,
    threshold: float,
    known_aliases: Set[Tuple[str, str]],
) -> Optional[DuplicateEntityFinding]:
    """Compare a single proposed entity against an existing database node."""
    db_name = db_node.get("canonical_name") or ""
    if not db_name or (ename.lower(), db_name.lower()) in known_aliases:
        return None

    sim = _string_similarity(ename, db_name)
    if sim < threshold:
        return None

    db_desc = db_node.get("description") or ""
    desc_sim = (
        _string_similarity(edesc, db_desc)
        if edesc.strip() and db_desc.strip()
        else 0.0
    )

    c_merge = min(0.98, max(0.50, round(0.70 * sim + 0.30 * desc_sim, 2)))
    c_distinct = round(1.0 - c_merge, 2)
    delta = round(abs(c_merge - c_distinct), 2)
    hitl_required = delta < get_hitl_ambiguity_margin_threshold()

    db_nid = db_node.get("node_id") or ""
    if _ID_SEP_COLON in eid and _ID_SEP_UNDERSCORE in db_nid and _ID_SEP_COLON not in db_nid:
        hitl_required = True

    rationale = (
        f"Pre-mutation: '{ename}' (id={eid}) matches existing '{db_name}' "
        f"(id={db_nid}) sim={sim:.2f} desc_sim={desc_sim:.2f} "
        f"delta={delta:.2f} {'[HITL]' if hitl_required else '[Decisive]'}."
    )
    return DuplicateEntityFinding(
        node_id_a=eid,
        canonical_name_a=ename,
        node_type_a=etype,
        node_id_b=db_nid,
        canonical_name_b=db_name,
        node_type_b=db_type,
        similarity_score=sim,
        confidence_merge=c_merge,
        confidence_distinct=c_distinct,
        margin_delta=delta,
        hitl_required=hitl_required,
        rationale=rationale,
    )


def _scan_single_proposed(
    entity: Dict[str, Any],
    db_nodes: Dict[str, List[Dict[str, Any]]],
    threshold: float,
    known_aliases: Set[Tuple[str, str]],
) -> List[DuplicateEntityFinding]:
    """Scan candidate types for a single proposed entity."""
    eid = entity.get("id") or entity.get("node_id") or ""
    ename = (
        entity.get("canonical_name")
        or entity.get("name")
        or entity.get("display_name")
        or eid
    )
    if not ename:
        return []

    etype = entity.get("type") or entity.get("node_type") or ""
    if not etype and _ID_SEP_COLON in eid:
        etype = eid.split(_ID_SEP_COLON, 1)[0]
    if not etype:
        return []

    candidate_types = {etype} | _TYPE_COMPAT_MAP.get(etype, set())
    findings: List[DuplicateEntityFinding] = []
    edesc = entity.get("description") or ""

    for db_type in candidate_types:
        for db_node in db_nodes.get(db_type, []):
            finding = _evaluate_proposed_pair(
                eid, ename, etype, edesc, db_node, db_type, threshold, known_aliases
            )
            if finding is not None:
                findings.append(finding)
    return findings


def _synthesize_uninserted_proposals(uninserted_ids: Set[str]) -> List[Dict[str, Any]]:
    """Synthesize pseudo proposed entity dicts for uninserted target IDs."""
    synthesized = []
    for uid in sorted(uninserted_ids):
        etype, ename = "", uid
        if _ID_SEP_COLON in uid:
            parts = uid.split(_ID_SEP_COLON, 1)
            etype, ename = parts[0], parts[1].replace("_", " ").replace("-", " ")
        elif _ID_SEP_UNDERSCORE in uid:
            parts = uid.split(_ID_SEP_UNDERSCORE, 1)
            etype, ename = parts[0], parts[1].replace("_", " ").replace("-", " ")
        synthesized.append({
            "id": uid,
            "canonical_name": ename,
            "type": etype,
            "description": "",
        })
    return synthesized


def _compare_type_nodes(
    type_nodes: List[Dict[str, Any]],
    target_ids: Set[str],
    node_type: str,
    compare_fn: Any,
) -> List[DuplicateEntityFinding]:
    """Compare nodes of a single type where at least one node is in target_ids."""
    findings: List[DuplicateEntityFinding] = []
    count = len(type_nodes)
    for i in range(count):
        a = type_nodes[i]
        a_is_target = a["node_id"] in target_ids
        for j in range(i + 1, count):
            b = type_nodes[j]
            if not (a_is_target or (b["node_id"] in target_ids)):
                continue
            finding = compare_fn(a, b, node_type)
            if finding is not None:
                findings.append(finding)
    return findings


def _find_scoped_pairs(
    by_type: Dict[str, List[Dict[str, Any]]],
    type_has_target: Dict[str, bool],
    target_ids: Set[str],
    compare_fn: Any,
) -> List[DuplicateEntityFinding]:
    """Perform pairwise comparison across types having target nodes."""
    findings: List[DuplicateEntityFinding] = []
    for node_type, type_nodes in by_type.items():
        if type_has_target.get(node_type, False):
            findings.extend(_compare_type_nodes(type_nodes, target_ids, node_type, compare_fn))
    return findings
