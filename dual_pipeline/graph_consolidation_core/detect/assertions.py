"""
dual_pipeline/graph_consolidation_core/detect/assertions.py

Detects redundant and overlapping assertion clusters in the active graph.
Cardinality-aware: respects multi-valued predicates and distinguishes corroboration from collision.
Strictly read-only execution.
"""
from __future__ import annotations

import difflib
from typing import Any, Dict, List, Optional, Set, Tuple

from ..constants import (
    DEFAULT_LITERAL_SIMILARITY_THRESHOLD,
    HITL_AMBIGUITY_MARGIN_THRESHOLD,
)


def calculate_literal_similarity(lit_a: Optional[str], lit_b: Optional[str]) -> float:
    """Compute normalized sequence matcher ratio between two assertion object literals."""
    norm_a = (lit_a or "").strip().lower()
    norm_b = (lit_b or "").strip().lower()
    if not norm_a and not norm_b:
        return 1.0
    if not norm_a or not norm_b:
        return 0.0
    return round(difflib.SequenceMatcher(None, norm_a, norm_b).ratio(), 4)


def _pick_assertion_winner(
    a: Dict[str, Any],
    b: Dict[str, Any],
    ev_counts: Dict[str, int],
) -> Tuple[str, str, float, float, float, str]:
    """Pick winner and loser assertion, returning (winner_id, loser_id, winner_conf, loser_conf, delta, reason)."""
    ev_a = ev_counts.get(a["assertion_id"], 0)
    ev_b = ev_counts.get(b["assertion_id"], 0)
    conf_a = float(a.get("confidence_score") or 0.8)
    conf_b = float(b.get("confidence_score") or 0.8)
    delta = round(abs(conf_a - conf_b), 4)

    # 1. Higher evidence corroboration count wins if difference >= 1
    if abs(ev_a - ev_b) >= 1:
        if ev_a > ev_b:
            return a["assertion_id"], b["assertion_id"], conf_a, conf_b, delta, f"higher evidence count ({ev_a} > {ev_b})"
        return b["assertion_id"], a["assertion_id"], conf_b, conf_a, delta, f"higher evidence count ({ev_b} > {ev_a})"

    # 2. Higher confidence score wins if difference >= 0.05
    if delta >= 0.05:
        if conf_a > conf_b:
            return a["assertion_id"], b["assertion_id"], conf_a, conf_b, delta, f"higher confidence ({conf_a:.2f} > {conf_b:.2f})"
        return b["assertion_id"], a["assertion_id"], conf_b, conf_a, delta, f"higher confidence ({conf_b:.2f} > {conf_a:.2f})"

    # 3. Newer assertion timestamp wins
    ts_a = str(a.get("recorded_at") or a.get("created_at") or "")
    ts_b = str(b.get("recorded_at") or b.get("created_at") or "")
    if ts_a != ts_b:
        if ts_a > ts_b:
            return a["assertion_id"], b["assertion_id"], conf_a, conf_b, delta, f"newer timestamp ({ts_a} > {ts_b})"
        return b["assertion_id"], a["assertion_id"], conf_b, conf_a, delta, f"newer timestamp ({ts_b} > {ts_a})"

    # 4. Deterministic lexical tie-break
    if a["assertion_id"] < b["assertion_id"]:
        return a["assertion_id"], b["assertion_id"], conf_a, conf_b, delta, "lexical tie-break"
    return b["assertion_id"], a["assertion_id"], conf_b, conf_a, delta, "lexical tie-break"


def _load_assertion_overlap_data(conn: Any) -> Tuple[Dict[str, str], Dict[str, int], List[Dict[str, Any]]]:
    cur = conn.cursor()
    cur.execute("PRAGMA query_only = ON;")

    cardinality_map: Dict[str, str] = {}
    has_registry = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='assertion_predicate_registry'"
    ).fetchone()
    if has_registry:
        for r in cur.execute("SELECT predicate, cardinality FROM assertion_predicate_registry").fetchall():
            cardinality_map[r[0]] = r[1]

    cur.execute("SELECT assertion_id, count(*) as cnt FROM assertion_evidence GROUP BY assertion_id")
    ev_counts: Dict[str, int] = {r[0]: r[1] for r in cur.fetchall()}

    cur.execute("""
        SELECT a.assertion_id, a.subject_node_id, a.predicate, a.object_node_id,
               a.object_literal, a.assertion_type, a.confidence_label, a.confidence_score,
               a.primary_source_id, a.extraction_run_id, a.recorded_at, a.created_at,
               n.canonical_name as subject_name
        FROM assertion a
        JOIN node n ON a.subject_node_id = n.node_id
        WHERE a.canonical_status = 'canonical'
          AND a.currentness_state = 'current'
          AND a.superseded_by_assertion_id IS NULL
        ORDER BY a.subject_node_id, a.predicate, a.object_node_id
    """)
    rows = [dict(r) for r in cur.fetchall()]
    return cardinality_map, ev_counts, rows


def _build_exact_triple_duplicate(
    a1: Dict[str, Any],
    a2: Dict[str, Any],
    subj_id: str,
    pred: str,
    obj_1: str,
    cardinality_map: Dict[str, str],
    ev_counts: Dict[str, int],
) -> Dict[str, Any]:
    winner_id, loser_id, win_conf, lose_conf, delta, reason = _pick_assertion_winner(a1, a2, ev_counts)
    is_multi_source = a1.get("primary_source_id") != a2.get("primary_source_id")
    hitl_required = delta < HITL_AMBIGUITY_MARGIN_THRESHOLD and not is_multi_source

    return {
        "type": "exact_triple",
        "subject_node_id": subj_id,
        "subject_name": a1.get("subject_name"),
        "predicate": pred,
        "object_node_id": obj_1,
        "cardinality": cardinality_map.get(pred, "many_to_many"),
        "assertion_id_1": a1["assertion_id"],
        "assertion_id_2": a2["assertion_id"],
        "winner_id": winner_id,
        "loser_id": loser_id,
        "winner_confidence": win_conf,
        "loser_confidence": lose_conf,
        "confidence_delta": delta,
        "selection_reason": reason,
        "is_multi_source_corroboration": is_multi_source,
        "hitl_required": hitl_required,
    }


def _build_literal_overlap_duplicate(
    a1: Dict[str, Any],
    a2: Dict[str, Any],
    subj_id: str,
    pred: str,
    lit_1: str,
    lit_2: str,
    sim: float,
    ev_counts: Dict[str, int],
) -> Dict[str, Any]:
    winner_id, loser_id, win_conf, lose_conf, delta, reason = _pick_assertion_winner(a1, a2, ev_counts)
    hitl_required = delta < HITL_AMBIGUITY_MARGIN_THRESHOLD

    return {
        "type": "literal_overlap",
        "subject_node_id": subj_id,
        "subject_name": a1.get("subject_name"),
        "predicate": pred,
        "similarity": sim,
        "literal_1": lit_1,
        "literal_2": lit_2,
        "assertion_id_1": a1["assertion_id"],
        "assertion_id_2": a2["assertion_id"],
        "winner_id": winner_id,
        "loser_id": loser_id,
        "winner_confidence": win_conf,
        "loser_confidence": lose_conf,
        "confidence_delta": delta,
        "selection_reason": reason,
        "hitl_required": hitl_required,
    }


def _evaluate_assertion_pair(
    a1: Dict[str, Any],
    a2: Dict[str, Any],
    subj_id: str,
    pred: str,
    cardinality_map: Dict[str, str],
    ev_counts: Dict[str, int],
    literal_similarity_threshold: float,
    seen_pairs: Set[frozenset[str]],
    exact_triples: List[Dict[str, Any]],
    literal_overlaps: List[Dict[str, Any]],
) -> None:
    pair_key = frozenset({a1["assertion_id"], a2["assertion_id"]})
    if pair_key in seen_pairs:
        return

    obj_1 = a1.get("object_node_id")
    obj_2 = a2.get("object_node_id")
    lit_1 = a1.get("object_literal")
    lit_2 = a2.get("object_literal")

    if obj_1 and obj_2 and obj_1 == obj_2:
        seen_pairs.add(pair_key)
        exact_triples.append(
            _build_exact_triple_duplicate(
                a1, a2, subj_id, pred, obj_1, cardinality_map, ev_counts
            )
        )
    elif lit_1 is not None and lit_2 is not None:
        sim = calculate_literal_similarity(lit_1, lit_2)
        if sim >= literal_similarity_threshold:
            seen_pairs.add(pair_key)
            literal_overlaps.append(
                _build_literal_overlap_duplicate(
                    a1, a2, subj_id, pred, lit_1, lit_2, sim, ev_counts
                )
            )


def _process_assertion_group(
    group: List[Dict[str, Any]],
    subj_id: str,
    pred: str,
    cardinality_map: Dict[str, str],
    ev_counts: Dict[str, int],
    literal_similarity_threshold: float,
    seen_pairs: Set[frozenset[str]],
    exact_triples: List[Dict[str, Any]],
    literal_overlaps: List[Dict[str, Any]],
) -> None:
    n = len(group)
    for i in range(n):
        for j in range(i + 1, n):
            _evaluate_assertion_pair(
                group[i],
                group[j],
                subj_id,
                pred,
                cardinality_map,
                ev_counts,
                literal_similarity_threshold,
                seen_pairs,
                exact_triples,
                literal_overlaps,
            )


def detect_assertion_overlaps(
    conn: Any,
    literal_similarity_threshold: float = DEFAULT_LITERAL_SIMILARITY_THRESHOLD,
) -> Dict[str, Any]:
    """Scans active canonical current assertions for exact-triple and fuzzy-literal duplicates."""
    cardinality_map, ev_counts, rows = _load_assertion_overlap_data(conn)

    exact_triple_groups: List[Dict[str, Any]] = []
    literal_overlap_groups: List[Dict[str, Any]] = []
    seen_pairs: Set[frozenset[str]] = set()

    by_subj_pred: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in rows:
        by_subj_pred.setdefault((r["subject_node_id"], r["predicate"]), []).append(r)

    for (subj_id, pred), group in by_subj_pred.items():
        if len(group) >= 2:
            _process_assertion_group(
                group,
                subj_id,
                pred,
                cardinality_map,
                ev_counts,
                literal_similarity_threshold,
                seen_pairs,
                exact_triple_groups,
                literal_overlap_groups,
            )

    return {
        "exact_triple_duplicates_count": len(exact_triple_groups),
        "literal_overlaps_count": len(literal_overlap_groups),
        "total_assertion_groups_count": len(exact_triple_groups) + len(literal_overlap_groups),
        "exact_triple_groups": exact_triple_groups,
        "literal_overlap_groups": literal_overlap_groups,
    }
