"""
dual_pipeline/graph_consolidation_core/resolver.py

Unified, policy-backed supersession resolver for knowledge graph assertions.
Replaces conflicting winner rules across Tier 2, maintenance dedup, freshness, and mutex.
Enforces multi-valued predicate safety, evidence consolidation, and the 15% HITL ambiguity margin.
Strictly provider-free and deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from .policy import DEFAULT_SUPERSEDION_POLICY, SupersessionPolicy


@dataclass(frozen=True)
class SupersessionDecision:
    """Immutable resolution of competing or duplicate assertions."""

    winner_id: str
    loser_id: str
    subject_node_id: str
    predicate: str
    object_node_id: Optional[str]
    object_literal: Optional[str]
    winner_confidence: float
    loser_confidence: float
    confidence_delta: float
    hitl_required: bool
    selection_reason: str
    is_corroboration: bool
    evidence_ids_to_transfer: List[str]

    def to_dict(self) -> Dict[str, Any]:
        """Convert decision to JSON-serializable dictionary."""
        return {
            "winner_id": self.winner_id,
            "loser_id": self.loser_id,
            "subject_node_id": self.subject_node_id,
            "predicate": self.predicate,
            "object_node_id": self.object_node_id,
            "object_literal": self.object_literal,
            "winner_confidence": self.winner_confidence,
            "loser_confidence": self.loser_confidence,
            "confidence_delta": self.confidence_delta,
            "hitl_required": self.hitl_required,
            "selection_reason": self.selection_reason,
            "is_corroboration": self.is_corroboration,
            "evidence_ids_to_transfer": list(self.evidence_ids_to_transfer),
        }


def _check_evidence_precedence(
    a1: Dict[str, Any], a2: Dict[str, Any], policy: SupersessionPolicy, ev_counts: Dict[str, int]
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any], str]]:
    if not policy.evidence_count_precedence:
        return None
    cnt_1 = ev_counts.get(a1["assertion_id"], 0)
    cnt_2 = ev_counts.get(a2["assertion_id"], 0)
    if abs(cnt_1 - cnt_2) >= 1:
        if cnt_1 > cnt_2:
            return a1, a2, f"higher evidence count ({cnt_1} > {cnt_2})"
        return a2, a1, f"higher evidence count ({cnt_2} > {cnt_1})"
    return None


def _check_confidence_precedence(
    a1: Dict[str, Any], a2: Dict[str, Any], policy: SupersessionPolicy, delta: float, conf_1: float, conf_2: float
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any], str]]:
    if delta >= policy.confidence_margin_threshold:
        if conf_1 > conf_2:
            return a1, a2, f"higher confidence ({conf_1:.2f} > {conf_2:.2f})"
        return a2, a1, f"higher confidence ({conf_2:.2f} > {conf_1:.2f})"
    return None


def _check_authority_precedence(
    a1: Dict[str, Any], a2: Dict[str, Any], policy: SupersessionPolicy
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any], str]]:
    if not policy.authority_rank_precedence:
        return None
    rank_1 = int(a1.get("source_authority_rank") or 1)
    rank_2 = int(a2.get("source_authority_rank") or 1)
    if abs(rank_1 - rank_2) >= 1:
        if rank_1 > rank_2:
            return a1, a2, f"higher source authority ({rank_1} > {rank_2})"
        return a2, a1, f"higher source authority ({rank_2} > {rank_1})"
    return None


def _check_recency_precedence(
    a1: Dict[str, Any], a2: Dict[str, Any], policy: SupersessionPolicy
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any], str]]:
    if not policy.recency_precedence:
        return None
    ts_1 = str(a1.get("ts") or a1.get("recorded_at") or a1.get("created_at") or "")
    ts_2 = str(a2.get("ts") or a2.get("recorded_at") or a2.get("created_at") or "")
    if ts_1 != ts_2:
        if ts_1 > ts_2:
            return a1, a2, f"newer timestamp ({ts_1} > {ts_2})"
        return a2, a1, f"newer timestamp ({ts_2} > {ts_1})"
    return None


def _lexical_tie_break(
    a1: Dict[str, Any], a2: Dict[str, Any]
) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
    if a1["assertion_id"] < a2["assertion_id"]:
        return a1, a2, "lexical tie-break"
    return a2, a1, "lexical tie-break"


def _pick_winner_and_loser(
    a1: Dict[str, Any],
    a2: Dict[str, Any],
    policy: SupersessionPolicy,
    ev_counts: Dict[str, int],
) -> Tuple[Dict[str, Any], Dict[str, Any], float, bool, str]:
    """
    Evaluates hierarchy: Evidence Count -> Confidence Score -> Recency -> Lexical Tie-break.
    Returns (winner, loser, confidence_delta, hitl_required, reason).
    """
    conf_1 = float(a1.get("confidence_score") or 0.8)
    conf_2 = float(a2.get("confidence_score") or 0.8)
    delta = round(abs(conf_1 - conf_2), 4)
    hitl_required = policy.require_hitl_below_margin and (delta < policy.confidence_margin_threshold)

    res = (
        _check_evidence_precedence(a1, a2, policy, ev_counts)
        or _check_confidence_precedence(a1, a2, policy, delta, conf_1, conf_2)
        or _check_authority_precedence(a1, a2, policy)
        or _check_recency_precedence(a1, a2, policy)
        or _lexical_tie_break(a1, a2)
    )
    winner, loser, reason = res
    if "higher confidence" in reason:
        hitl_required = False
    return winner, loser, delta, hitl_required, reason


def resolve_pairwise_supersession(
    a1: Dict[str, Any],
    a2: Dict[str, Any],
    *,
    policy: Optional[SupersessionPolicy] = None,
    predicate_cardinality: Optional[str] = None,
    ev_links_map: Optional[Dict[str, List[str]]] = None,
) -> Optional[SupersessionDecision]:
    """
    Resolves pairwise supersession between two assertions under identical (subject, predicate).
    Enforces multi-valued predicate protection: distinct objects are NOT superseded unless exclusive.
    """
    p = policy or DEFAULT_SUPERSEDION_POLICY
    pred = a1["predicate"]
    obj_1 = a1.get("object_node_id") or a1.get("object_literal")
    obj_2 = a2.get("object_node_id") or a2.get("object_literal")

    is_exact_triple = (obj_1 == obj_2)

    # Multi-valued predicate check
    if not is_exact_triple and p.preserve_multi_valued_predicates:
        cardinality = predicate_cardinality or "many_to_many"
        if cardinality not in p.exclusive_cardinalities:
            # Multi-valued predicate with different objects: valid co-existing facts
            return None

    # Determine evidence counts and links
    ev_map = ev_links_map or {}
    ev_counts = {
        a1["assertion_id"]: len(ev_map.get(a1["assertion_id"], [])),
        a2["assertion_id"]: len(ev_map.get(a2["assertion_id"], [])),
    }

    winner, loser, delta, hitl_required, reason = _pick_winner_and_loser(a1, a2, p, ev_counts)

    # Consolidate evidence: identify evidence IDs from loser that are not in winner
    winner_ev = set(ev_map.get(winner["assertion_id"], []))
    loser_ev = set(ev_map.get(loser["assertion_id"], []))
    to_transfer = sorted(loser_ev - winner_ev) if p.consolidate_evidence_on_supersede else []

    is_corroboration = (
        bool(winner.get("primary_source_id"))
        and bool(loser.get("primary_source_id"))
        and winner.get("primary_source_id") != loser.get("primary_source_id")
    )

    return SupersessionDecision(
        winner_id=winner["assertion_id"],
        loser_id=loser["assertion_id"],
        subject_node_id=winner["subject_node_id"],
        predicate=pred,
        object_node_id=winner.get("object_node_id"),
        object_literal=winner.get("object_literal"),
        winner_confidence=float(winner.get("confidence_score") or 0.8),
        loser_confidence=float(loser.get("confidence_score") or 0.8),
        confidence_delta=delta,
        hitl_required=hitl_required,
        selection_reason=reason,
        is_corroboration=is_corroboration,
        evidence_ids_to_transfer=to_transfer,
    )


def _load_graph_supersession_context(
    conn: Any,
) -> Tuple[Dict[str, str], Dict[str, List[str]], List[Dict[str, Any]]]:
    cur = conn.cursor()
    cur.execute("PRAGMA query_only = ON;")

    cardinality_map: Dict[str, str] = {}
    has_registry = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='assertion_predicate_registry'"
    ).fetchone()
    if has_registry:
        for r in cur.execute("SELECT predicate, cardinality FROM assertion_predicate_registry").fetchall():
            cardinality_map[r[0]] = r[1]

    ev_links_map: Dict[str, List[str]] = {}
    has_ae = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='assertion_evidence'"
    ).fetchone()
    if has_ae:
        for r in cur.execute("SELECT assertion_id, evidence_id FROM assertion_evidence").fetchall():
            ev_links_map.setdefault(r[0], []).append(r[1])

    cur.execute("""
        SELECT assertion_id, subject_node_id, predicate, object_node_id, object_literal,
               confidence_score, primary_source_id, recorded_at, created_at
        FROM assertion
        WHERE currentness_state = 'current'
          AND canonical_status = 'canonical'
          AND superseded_by_assertion_id IS NULL
        ORDER BY subject_node_id, predicate, object_node_id, object_literal
    """)
    rows = [dict(r) for r in cur.fetchall()]
    return cardinality_map, ev_links_map, rows


def _resolve_group_supersessions(
    group: List[Dict[str, Any]],
    card: str,
    policy: SupersessionPolicy,
    ev_links_map: Dict[str, List[str]],
    seen_pairs: Set[frozenset[str]],
) -> List[SupersessionDecision]:
    group_decisions: List[SupersessionDecision] = []
    count = len(group)
    for i in range(count):
        for j in range(i + 1, count):
            a1, a2 = group[i], group[j]
            pair_key = frozenset({a1["assertion_id"], a2["assertion_id"]})
            if pair_key in seen_pairs:
                continue

            decision = resolve_pairwise_supersession(
                a1,
                a2,
                policy=policy,
                predicate_cardinality=card,
                ev_links_map=ev_links_map,
            )
            if decision is not None:
                seen_pairs.add(pair_key)
                group_decisions.append(decision)
    return group_decisions


def resolve_graph_supersessions(
    conn: Any,
    policy: Optional[SupersessionPolicy] = None,
) -> List[SupersessionDecision]:
    """
    Scans active canonical current assertions in SQLite database and executes governed supersession resolution.
    Returns list of SupersessionDecision instances.
    """
    p = policy or DEFAULT_SUPERSEDION_POLICY
    cardinality_map, ev_links_map, rows = _load_graph_supersession_context(conn)

    by_subj_pred: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in rows:
        key = (r["subject_node_id"], r["predicate"])
        by_subj_pred.setdefault(key, []).append(r)

    decisions: List[SupersessionDecision] = []
    seen_pairs: Set[frozenset[str]] = set()

    for (_subj, pred), group in by_subj_pred.items():
        if len(group) >= 2:
            card = cardinality_map.get(pred, "many_to_many")
            decisions.extend(
                _resolve_group_supersessions(group, card, p, ev_links_map, seen_pairs)
            )

    return decisions


def find_governed_supersessions(
    conn: Any,
    policy: Optional[SupersessionPolicy] = None,
) -> List[Dict[str, Any]]:
    """Compatibility adapter returning dictionaries matching legacy find_semantic_duplicates signatures."""
    decisions = resolve_graph_supersessions(conn, policy)
    return [d.to_dict() for d in decisions]
