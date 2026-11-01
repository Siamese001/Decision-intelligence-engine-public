"""
dual_pipeline/graph_consolidation_core/detect/nodes.py

Detects semantic entity duplicate clusters across active graph nodes.
Supports same-type and cross-type concept clustering, alias drift, and survivor ranking.
Strictly read-only execution.
"""
from __future__ import annotations

import difflib
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from ..constants import (
    COMPATIBLE_CONCEPT_TYPES,
    DEFAULT_NODE_SIMILARITY_THRESHOLD,
    HITL_AMBIGUITY_MARGIN_THRESHOLD,
    INCOMPATIBLE_TYPE_PAIRS,
    NODE_TYPE_PRECEDENCE,
    STOPWORDS,
)


def normalize_name(text: Optional[str]) -> str:
    """Normalize entity name: lowercase, strip punctuation, remove stopwords."""
    if not text:
        return ""
    cleaned = re.sub(r"[^a-zA-Z0-9\s]", " ", text.lower())
    tokens = [t for t in cleaned.split() if t and t not in STOPWORDS]
    return " ".join(tokens) if tokens else cleaned.strip()


def compute_name_similarity(norm_a: str, norm_b: str) -> Tuple[float, float, float]:
    """Compute combined similarity with multi-token phrase containment support."""
    if not norm_a and not norm_b:
        return 1.0, 1.0, 1.0
    if not norm_a or not norm_b:
        return 0.0, 0.0, 0.0
    if norm_a == norm_b:
        return 1.0, 1.0, 1.0

    seq_ratio = difflib.SequenceMatcher(None, norm_a, norm_b).ratio()
    tokens_a = set(norm_a.split())
    tokens_b = set(norm_b.split())
    intersection = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    jaccard = intersection / union if union > 0 else 0.0
    combined = 0.55 * seq_ratio + 0.45 * jaccard
    if (
        min(len(tokens_a), len(tokens_b)) >= 3
        and (tokens_a <= tokens_b or tokens_b <= tokens_a)
        and jaccard >= 0.50
    ):
        combined = max(combined, 0.35 * seq_ratio + 0.30 * jaccard + 0.35)
    elif (
        min(len(tokens_a), len(tokens_b)) >= 1
        and max(len(tokens_a), len(tokens_b)) >= 2
        and (tokens_a <= tokens_b or tokens_b <= tokens_a)
        and min(len(t) for t in (tokens_a & tokens_b)) >= 5
    ):
        combined = max(combined, 0.85)
    return round(combined, 4), round(seq_ratio, 4), round(jaccard, 4)


def _determine_survivor(node_a: Dict[str, Any], node_b: Dict[str, Any], asrt_counts: Dict[str, int]) -> Tuple[str, str, str]:
    """Pick recommended survivor (winner) and retired (loser) node."""
    type_a, type_b = node_a.get("node_type", ""), node_b.get("node_type", "")
    prec_a = NODE_TYPE_PRECEDENCE.get(type_a, 50)
    prec_b = NODE_TYPE_PRECEDENCE.get(type_b, 50)

    # 1. Type precedence difference >= 10
    if abs(prec_a - prec_b) >= 10:
        if prec_a > prec_b:
            return node_a["node_id"], node_b["node_id"], f"type precedence ({type_a} > {type_b})"
        return node_b["node_id"], node_a["node_id"], f"type precedence ({type_b} > {type_a})"

    # 2. Prefer confirmed canonical over candidate/row
    stat_a = str(node_a.get("status", "")).lower()
    stat_b = str(node_b.get("status", "")).lower()
    is_canon_a = "canonical" in stat_a or "active" in stat_a
    is_canon_b = "canonical" in stat_b or "active" in stat_b
    if is_canon_a and not is_canon_b:
        return node_a["node_id"], node_b["node_id"], "status canonical precedence"
    if is_canon_b and not is_canon_a:
        return node_b["node_id"], node_a["node_id"], "status canonical precedence"

    # 3. Assertion count precedence
    cnt_a = asrt_counts.get(node_a["node_id"], 0)
    cnt_b = asrt_counts.get(node_b["node_id"], 0)
    if cnt_a != cnt_b:
        if cnt_a > cnt_b:
            return node_a["node_id"], node_b["node_id"], f"higher assertion connectivity ({cnt_a} > {cnt_b})"
        return node_b["node_id"], node_a["node_id"], f"higher assertion connectivity ({cnt_b} > {cnt_a})"

    # 4. Deterministic lexical tie-break
    if node_a["node_id"] < node_b["node_id"]:
        return node_a["node_id"], node_b["node_id"], "lexical tie-break"
    return node_b["node_id"], node_a["node_id"], "lexical tie-break"


def _build_exact_cluster_candidate(
    na: Dict[str, Any],
    nb: Dict[str, Any],
    asrt_counts: Dict[str, int],
) -> Optional[Dict[str, Any]]:
    same_type = na["node_type"] == nb["node_type"]
    type_pair = frozenset({na["node_type"], nb["node_type"]})
    if type_pair in INCOMPATIBLE_TYPE_PAIRS:
        return None

    survivor, retired, rank_reason = _determine_survivor(na, nb, asrt_counts)
    conf_merge = 0.95 if same_type else 0.85

    stat_a = str(na.get("status", "")).lower()
    stat_b = str(nb.get("status", "")).lower()
    is_charter = "currentness_validation_required" in stat_a or "currentness_validation_required" in stat_b
    if is_charter:
        conf_merge = 0.55

    conf_distinct = round(1.0 - conf_merge, 4)
    margin_delta = round(abs(conf_merge - conf_distinct), 4)
    hitl_required = margin_delta < HITL_AMBIGUITY_MARGIN_THRESHOLD

    return {
        "cluster_type": "same_type_exact_name" if same_type else "cross_type_exact_name",
        "node_a": na,
        "node_b": nb,
        "similarity": 1.0,
        "recommended_survivor_id": survivor,
        "recommended_retired_id": retired,
        "survivor_selection_reason": rank_reason,
        "confidence_merge": conf_merge,
        "confidence_distinct": conf_distinct,
        "margin_delta": margin_delta,
        "hitl_required": hitl_required,
        "recommended_action": "MERGE" if conf_merge >= 0.5 else "KEEP_DISTINCT",
    }


def _evaluate_group_exact_matches(
    group: List[Dict[str, Any]],
    seen_pairs: Set[frozenset[str]],
    asrt_counts: Dict[str, int],
) -> List[Dict[str, Any]]:
    group_clusters: List[Dict[str, Any]] = []
    count = len(group)
    for i in range(count):
        for j in range(i + 1, count):
            na, nb = group[i], group[j]
            pair_key = frozenset({na["node_id"], nb["node_id"]})
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            cand = _build_exact_cluster_candidate(na, nb, asrt_counts)
            if cand is not None:
                group_clusters.append(cand)
    return group_clusters


def _evaluate_exact_matches(
    by_norm: Dict[str, List[Dict[str, Any]]],
    seen_pairs: Set[frozenset[str]],
    asrt_counts: Dict[str, int],
) -> List[Dict[str, Any]]:
    clusters: List[Dict[str, Any]] = []
    for _norm, group in by_norm.items():
        if len(group) < 2:
            continue
        clusters.extend(_evaluate_group_exact_matches(group, seen_pairs, asrt_counts))
    return clusters


def _build_fuzzy_cluster_candidate(
    na: Dict[str, Any],
    nb: Dict[str, Any],
    comb: float,
    asrt_counts: Dict[str, int],
) -> Dict[str, Any]:
    survivor, retired, rank_reason = _determine_survivor(na, nb, asrt_counts)
    conf_merge = round(comb * 0.9, 4)
    conf_distinct = round(1.0 - conf_merge, 4)
    margin_delta = round(abs(conf_merge - conf_distinct), 4)
    hitl_required = margin_delta < HITL_AMBIGUITY_MARGIN_THRESHOLD

    same_type = na["node_type"] == nb["node_type"]
    return {
        "cluster_type": "same_type_fuzzy_name" if same_type else "cross_type_fuzzy_name",
        "node_a": na,
        "node_b": nb,
        "similarity": comb,
        "recommended_survivor_id": survivor,
        "recommended_retired_id": retired,
        "survivor_selection_reason": rank_reason,
        "confidence_merge": conf_merge,
        "confidence_distinct": conf_distinct,
        "margin_delta": margin_delta,
        "hitl_required": hitl_required,
        "recommended_action": "MERGE" if conf_merge >= 0.5 else "KEEP_DISTINCT",
    }


def _evaluate_type_fuzzy_matches(
    type_nodes: List[Dict[str, Any]],
    norm_map: Dict[str, str],
    seen_pairs: Set[frozenset[str]],
    asrt_counts: Dict[str, int],
    similarity_threshold: float,
) -> List[Dict[str, Any]]:
    type_clusters: List[Dict[str, Any]] = []
    count = len(type_nodes)
    for i in range(count):
        for j in range(i + 1, count):
            na, nb = type_nodes[i], type_nodes[j]
            pair_key = frozenset({na["node_id"], nb["node_id"]})
            if pair_key in seen_pairs:
                continue

            norm_a = norm_map[na["node_id"]]
            norm_b = norm_map[nb["node_id"]]
            comb, _seq, _jac = compute_name_similarity(norm_a, norm_b)
            if comb >= similarity_threshold:
                seen_pairs.add(pair_key)
                type_clusters.append(_build_fuzzy_cluster_candidate(na, nb, comb, asrt_counts))
    return type_clusters


def _is_eligible_cross_type_pair(
    na: Dict[str, Any], nb: Dict[str, Any], ta: Set[str], tb: Set[str], seen_pairs: Set[frozenset[str]]
) -> bool:
    if na["node_type"] == nb["node_type"] or max(len(ta), len(tb)) < 2:
        return False
    if frozenset({na["node_id"], nb["node_id"]}) in seen_pairs:
        return False
    if frozenset({na["node_type"], nb["node_type"]}) in INCOMPATIBLE_TYPE_PAIRS:
        return False
    return ta <= tb or tb <= ta


def _evaluate_cross_type_containment_matches(
    nodes: List[Dict[str, Any]],
    norm_map: Dict[str, str],
    seen_pairs: Set[frozenset[str]],
    asrt_counts: Dict[str, int],
    similarity_threshold: float,
) -> List[Dict[str, Any]]:
    """Evaluate cross-type pairs among compatible concept types when multi-token containment holds."""
    eligible = [(n, set(norm_map[n["node_id"]].split())) for n in nodes if n["node_type"] in COMPATIBLE_CONCEPT_TYPES]
    eligible = [(n, toks) for n, toks in eligible if len(toks) >= 1]
    cross_clusters: List[Dict[str, Any]] = []
    for i, (na, ta) in enumerate(eligible):
        for nb, tb in eligible[i + 1 :]:
            if not _is_eligible_cross_type_pair(na, nb, ta, tb, seen_pairs):
                continue
            comb, _seq, _jac = compute_name_similarity(norm_map[na["node_id"]], norm_map[nb["node_id"]])
            if comb >= similarity_threshold:
                seen_pairs.add(frozenset({na["node_id"], nb["node_id"]}))
                cross_clusters.append(_build_fuzzy_cluster_candidate(na, nb, comb, asrt_counts))
    return cross_clusters


def _evaluate_fuzzy_matches(
    nodes: List[Dict[str, Any]],
    norm_map: Dict[str, str],
    seen_pairs: Set[frozenset[str]],
    asrt_counts: Dict[str, int],
    similarity_threshold: float,
) -> List[Dict[str, Any]]:
    clusters: List[Dict[str, Any]] = []
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for n in nodes:
        by_type.setdefault(n["node_type"], []).append(n)

    for _node_type, type_nodes in by_type.items():
        clusters.extend(
            _evaluate_type_fuzzy_matches(
                type_nodes, norm_map, seen_pairs, asrt_counts, similarity_threshold
            )
        )
    clusters.extend(
        _evaluate_cross_type_containment_matches(
            nodes, norm_map, seen_pairs, asrt_counts, similarity_threshold
        )
    )
    return clusters


def _fetch_node_and_assertion_counts(conn: Any) -> Tuple[Dict[str, int], List[Dict[str, Any]]]:
    cur = conn.cursor()
    cur.execute("PRAGMA query_only = ON;")

    cur.execute("""
        SELECT subject_node_id as nid, count(*) as cnt FROM assertion WHERE currentness_state='current' GROUP BY subject_node_id
        UNION ALL
        SELECT object_node_id as nid, count(*) as cnt FROM assertion WHERE currentness_state='current' AND object_node_id IS NOT NULL GROUP BY object_node_id
    """)
    asrt_counts: Dict[str, int] = {}
    for r in cur.fetchall():
        asrt_counts[r[0]] = asrt_counts.get(r[0], 0) + r[1]

    cur.execute("""
        SELECT node_id, node_type, canonical_name, display_name, description, status
        FROM node
        WHERE active = 1
        ORDER BY node_type, canonical_name
    """)
    nodes = [dict(r) for r in cur.fetchall()]
    return asrt_counts, nodes


def _incorporate_hygiene_findings(
    hygiene_findings: List[Dict[str, Any]],
    nodes: List[Dict[str, Any]],
    seen_pairs: Set[frozenset[str]],
    asrt_counts: Dict[str, int],
) -> List[Dict[str, Any]]:
    extra_clusters: List[Dict[str, Any]] = []
    node_lookup = {n["node_id"]: n for n in nodes}
    for hf in hygiene_findings:
        refs = hf.get("entity_references", [])
        if len(refs) != 2 or hf.get("proposed_fix") != "merge_entities":
            continue
        e1, e2 = refs[0], refs[1]
        pair_key = frozenset({e1, e2})
        if pair_key in seen_pairs or e1 not in node_lookup or e2 not in node_lookup:
            continue
        seen_pairs.add(pair_key)
        na, nb = node_lookup[e1], node_lookup[e2]
        survivor, retired, rank_reason = _determine_survivor(na, nb, asrt_counts)
        extra_clusters.append({
            "cluster_type": "hygiene_panel_finding",
            "node_a": na,
            "node_b": nb,
            "similarity": 0.80,
            "recommended_survivor_id": survivor,
            "recommended_retired_id": retired,
            "survivor_selection_reason": f"hygiene panel ({rank_reason})",
            "confidence_merge": 0.80,
            "confidence_distinct": 0.20,
            "margin_delta": 0.60,
            "hitl_required": False,
            "recommended_action": "MERGE",
        })
    return extra_clusters


def detect_node_clusters(
    conn: Any,
    similarity_threshold: float = DEFAULT_NODE_SIMILARITY_THRESHOLD,
    hygiene_findings: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Scans all active graph nodes for duplicate and near-duplicate entity clusters."""
    asrt_counts, nodes = _fetch_node_and_assertion_counts(conn)
    clusters: List[Dict[str, Any]] = []
    seen_pairs: Set[frozenset[str]] = set()

    norm_map: Dict[str, str] = {n["node_id"]: normalize_name(n["canonical_name"]) for n in nodes}
    by_norm: Dict[str, List[Dict[str, Any]]] = {}
    for n in nodes:
        norm = norm_map[n["node_id"]]
        if norm:
            by_norm.setdefault(norm, []).append(n)

    clusters.extend(_evaluate_exact_matches(by_norm, seen_pairs, asrt_counts))
    clusters.extend(
        _evaluate_fuzzy_matches(nodes, norm_map, seen_pairs, asrt_counts, similarity_threshold)
    )

    if hygiene_findings:
        clusters.extend(
            _incorporate_hygiene_findings(hygiene_findings, nodes, seen_pairs, asrt_counts)
        )

    same_type_count = sum(1 for c in clusters if "same_type" in c["cluster_type"])
    cross_type_count = sum(1 for c in clusters if "cross_type" in c["cluster_type"])
    hitl_count = sum(1 for c in clusters if c["hitl_required"])

    return {
        "clusters_total": len(clusters),
        "same_type_count": same_type_count,
        "cross_type_count": cross_type_count,
        "hitl_required_count": hitl_count,
        "clusters": clusters,
    }
