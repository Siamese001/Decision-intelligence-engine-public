"""
dual_pipeline/live_meeting_recorder_core/copilot_connect.py

SSOT engine for Wave 5: Copilot `connect` intent and contradiction-aware `gaps`.
Extracts cross-SOW chains ranked by flow state and entity salience, and detects
true semantic contradictions (opposing objects/literals on identical subject+predicate).
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

from .c19_firewall import is_c19_quarantined_assertion
from .copilot_connection_policy import is_assertion_in_scope
from .copilot_predicates import normalize_copilot_predicate
from .copilot_retriever import has_c19_marker

logger = logging.getLogger("dual_pipeline.copilot_connect")


class GapsResult(tuple):
    """Backward-compatible 2-tuple (assertions, has_prior_record) with .conflicts property."""
    assertions: List[Dict[str, Any]]
    has_prior_record: bool
    conflicts: List[Dict[str, Any]]

    def __new__(cls, assertions: List[Dict[str, Any]], has_prior_record: bool, conflicts: Optional[List[Dict[str, Any]]] = None) -> GapsResult:
        return super().__new__(cls, (assertions, has_prior_record))

    def __init__(cls, assertions: List[Dict[str, Any]], has_prior_record: bool, conflicts: Optional[List[Dict[str, Any]]] = None) -> None:
        cls.assertions, cls.has_prior_record, cls.conflicts = assertions, has_prior_record, conflicts or []


def _accumulate_token_salience(
    tokens: Any, token_idf: Dict[str, float], inv_index: Dict[str, Set[str]], allowed: Set[str], salience_map: Dict[str, float]
) -> None:
    for token in tokens:
        idf = token_idf.get(token, 1.0)
        for nid in inv_index.get(token, set()):
            salience_map[nid] = salience_map.get(nid, 0.0) + idf
        if token in allowed:
            salience_map[token] = salience_map.get(token, 0.0) + 2.0


def _accumulate_entity_salience(
    dialogue: Any, token_idf: Dict[str, float], inv_index: Dict[str, Set[str]], allowed: Set[str], salience_map: Dict[str, float]
) -> None:
    from .copilot_retriever import _tokenize_text

    if not hasattr(dialogue, "salient_entities"):
        return
    for ent, score in dialogue.salient_entities():
        if ent in allowed:
            salience_map[ent] = salience_map.get(ent, 0.0) + float(score) * 2.0
        for t in _tokenize_text(ent):
            for nid in inv_index.get(t, set()):
                salience_map[nid] = salience_map.get(nid, 0.0) + float(score) * token_idf.get(t, 1.0)


def _extract_dialogue_salience(dialogue: Any, retriever: Any) -> Tuple[Dict[str, float], str]:
    """Extracts entity salience weights and current conversational act from dialogue target."""
    salience_map: Dict[str, float] = {}
    current_act = str(getattr(dialogue, "current_act", "general")).lower()
    from .copilot_retriever import _extract_query_tokens

    query_tokens = _extract_query_tokens(dialogue)
    inv_index = getattr(retriever, "_inverted_index", {})
    allowed = getattr(retriever, "_allowed_nodes", set())
    token_idf = getattr(retriever, "_token_idf", {})
    _accumulate_token_salience(query_tokens, token_idf, inv_index, allowed, salience_map)
    _accumulate_entity_salience(dialogue, token_idf, inv_index, allowed, salience_map)
    return salience_map, current_act


def _ensure_neighborhood_store(retriever: Any, salience_map: Dict[str, float]) -> Optional[Any]:
    """Retrieves or dynamically computes the neighborhood store for active seeds."""
    store = getattr(retriever, "neighborhood_store", None)
    if store and getattr(store, "chains", None):
        return store
    topology = getattr(retriever, "_topology", None)
    if not topology:
        return None
    from .copilot_neighborhood import CopilotNeighborhoodStore, compute_neighborhood_chains

    seeds = list(salience_map.keys())[:10] or list(getattr(retriever, "_attendee_node_ids", set()))[:10]
    if not seeds:
        return None
    eng_id, permitted = getattr(retriever, "engagement_id", None), getattr(retriever, "permitted_engagements", None)
    chains, nh_status = compute_neighborhood_chains(topology, seeds, engagement_id=eng_id, permitted_engagements=permitted)
    store = CopilotNeighborhoodStore(chains=chains, status=nh_status, engagement_id=eng_id, permitted_engagements=permitted)
    retriever.neighborhood_store = store
    return store


def _score_chain_candidate(
    chain: Dict[str, Any],
    salience_map: Dict[str, float],
    current_act: str,
    attendee_nodes: Set[str],
) -> float:
    """Computes relevance score of a relationship chain based on salience and flow state."""
    score = 0.0
    path = chain.get("path", [])

    for nid in path:
        score += salience_map.get(nid, 0.0)

    if chain.get("seed_id") in attendee_nodes:
        score += 1.5

    if chain.get("is_priority"):
        score += 1.0

    preds = chain.get("predicates", [])
    if current_act in ("objection", "risk", "conflict") and any(
        p in ("HAS_RISK", "CONSTRAINS", "BLOCKED_BY", "DEPENDS_ON") for p in preds
    ) or current_act in ("action", "commitment", "next_steps") and any(
        p in ("DELIVERS", "OWNS", "ASSIGNS_TO", "COMMITS_TO") for p in preds
    ):
        score += 2.0
    elif current_act in ("question", "inquiry") and any(
        p in ("RELATES_TO", "DECIDES", "REQUIRES") for p in preds
    ):
        score += 1.5

    # Multi-hop richness boost for spanning across workstreams/domains
    hops = int(chain.get("hops", 1))
    score += hops * 0.5
    return score


def _match_salient_assertion(
    u: str, v: str, p: str, candidates: List[Dict[str, Any]], seen_ids: Set[str]
) -> Optional[Dict[str, Any]]:
    for a in candidates:
        aid = a.get("assertion_id")
        a_obj = str(a.get("object") or a.get("object_node_id") or "")
        a_subj = str(a.get("subject") or a.get("subject_node_id") or "")
        a_pred = normalize_copilot_predicate(a.get("predicate", ""))
        endpoint_match = (a_obj == v or a_obj.endswith(v)) or (a_subj == v or a_subj.endswith(v))
        if (
            endpoint_match
            and (not p or a_pred == normalize_copilot_predicate(p))
            and aid and aid not in seen_ids
            and not has_c19_marker(str(a.get("claim_text", "")))
        ):
            return a
    return None


def _endpoints_match_pair(a: dict[str, Any], u: str, v: str) -> bool:
    subj, obj = a.get("subject_node_id"), a.get("object_node_id")
    return (subj == u and obj == v) or (subj == v and obj == u)


def _is_valid_topology_assertion(
    a: Any,
    aid: str,
    u: str,
    v: str,
    seen_ids: Set[str],
    eng_id: Optional[str],
    permitted: Optional[Set[str]],
    retriever: Optional[Any],
) -> bool:
    if not isinstance(a, dict) or not _endpoints_match_pair(a, u, v):
        return False
    if aid in seen_ids or is_c19_quarantined_assertion(a, aid):
        return False
    claim = str(a.get("claim_text") or a.get("object_literal") or "")
    if not claim or has_c19_marker(claim):
        return False
    if eng_id and not is_assertion_in_scope(a, eng_id, permitted):
        return False
    ast_eng = str(a.get("engagement_id") or a.get("project_scope") or "")
    if not eng_id and ast_eng.startswith("quarantine:"):
        return False
    if retriever and hasattr(retriever, "_is_assertion_allowed") and not retriever._is_assertion_allowed(a, claim):
        return False
    return True


def _match_topology_assertion(
    u: str, v: str, p: str, top_assertions: Any, seen_ids: Set[str],
    retriever: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    if not isinstance(top_assertions, dict):
        return None
    eng_id = getattr(retriever, "engagement_id", None) if retriever else None
    permitted = getattr(retriever, "permitted_engagements", None) if retriever else None
    for aid, a in top_assertions.items():
        if _is_valid_topology_assertion(a, aid, u, v, seen_ids, eng_id, permitted, retriever):
            return {
                **a,
                "assertion_id": aid,
                "subject": u,
                "object": v,
                "predicate": a.get("predicate", p),
                "source_date": a.get("observed_at") or a.get("valid_from") or "",
            }
    return None


def _resolve_single_chain(
    c: Dict[str, Any],
    salient_dict: Dict[str, Any],
    top_assertions: Any,
    seen_ids: Set[str],
    retriever: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    path = c.get("path", [])
    preds = c.get("predicates", [])
    for i in range(len(path) - 1):
        u, v = path[i], path[i + 1]
        p = preds[i] if i < len(preds) else ""
        match = _match_salient_assertion(u, v, p, salient_dict.get(u, []), seen_ids) or _match_topology_assertion(u, v, p, top_assertions, seen_ids, retriever=retriever)
        if match:
            aid = match.get("assertion_id")
            if aid:
                seen_ids.add(aid)
            matches.append(match)
    return matches


def _resolve_chain_assertions(
    selected_chains: List[Dict[str, Any]],
    retriever: Any,
) -> List[Dict[str, Any]]:
    """Resolves and gathers clean, grounded assertion records for selected chains."""
    resolved: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()
    salient_dict = getattr(retriever, "_salient_assertions", {})
    topology = getattr(retriever, "_topology", None)
    top_assertions = getattr(topology, "assertions", {}) if topology else {}

    for c in selected_chains:
        resolved.extend(_resolve_single_chain(c, salient_dict, top_assertions, seen_ids, retriever=retriever))

    return resolved


def get_connected_chains(
    retriever: Any,
    dialogue: Any,
    limit: int = 5,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Surfaces cross-SOW multi-hop chains ranked by dialogue salience and flow state.
    Returns (ranked_chains, underlying_assertions).
    """
    salience_map, current_act = _extract_dialogue_salience(dialogue, retriever)
    if not salience_map:
        return [], []

    store = _ensure_neighborhood_store(retriever, salience_map)
    if not store or not store.chains:
        return [], []

    attendee_nodes = getattr(retriever, "_attendee_node_ids", set())

    # Score and filter candidate chains
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for c in store.chains:
        chain_str = c.get("chain_str", "")
        last_claim = c.get("last_claim", "")
        if has_c19_marker(chain_str) or has_c19_marker(last_claim):
            continue

        path = c.get("path", [])
        if salience_map and not any(nid in salience_map for nid in path):
            continue

        score = _score_chain_candidate(c, salience_map, current_act, attendee_nodes)
        scored.append((score, c))

    if not scored:
        return [], []

    # Sort deterministically
    scored.sort(key=lambda x: (x[0], -len(x[1].get("path", [])), x[1].get("chain_str", "")), reverse=True)
    top_chains = [c for _, c in scored[:limit]]

    underlying_assertions = _resolve_chain_assertions(top_chains, retriever)
    if not underlying_assertions:
        return [], []

    return top_chains, underlying_assertions


def _build_candidate_comparison_key(assertion: Dict[str, Any]) -> Tuple[str, str]:
    """Generates normalized (subject, predicate) tuple for contradiction clustering."""
    subj = str(assertion.get("subject") or assertion.get("subject_node_id") or "").strip().lower()
    pred = normalize_copilot_predicate(assertion.get("predicate") or "")
    return subj, pred


def _clean_object_representation(assertion: Dict[str, Any]) -> str:
    """Extracts clean comparable object or literal representation."""
    val = (
        assertion.get("object_literal")
        or assertion.get("object")
        or assertion.get("object_node_id")
        or assertion.get("claim_text")
        or ""
    )
    return str(val).strip().lower()


def _evaluate_group_conflict(
    subj: str, pred: str, a1: Dict[str, Any], a2: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    obj1 = _clean_object_representation(a1)
    obj2 = _clean_object_representation(a2)
    if not obj1 or not obj2 or obj1 == obj2:
        return None
    d1 = a1.get("source_date") or a1.get("observed_at") or a1.get("source_id") or "Prior Date"
    d2 = a2.get("source_date") or a2.get("observed_at") or a2.get("source_id") or "Current Date"
    return {
        "assertion_a": a1,
        "assertion_b": a2,
        "subject": subj,
        "predicate": pred,
        "date_a": d1,
        "date_b": d2,
        "claim_a": a1.get("claim_text") or obj1,
        "claim_b": a2.get("claim_text") or obj2,
        "reason": f"Opposing object values for {pred}: '{a1.get('claim_text') or obj1}' ({d1}) vs '{a2.get('claim_text') or obj2}' ({d2})",
    }


def _collect_group_conflicts(subj: str, pred: str, group: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    conflicts: List[Dict[str, Any]] = []
    for i in range(len(group)):
        for j in range(i + 1, len(group)):
            c = _evaluate_group_conflict(subj, pred, group[i], group[j])
            if c:
                conflicts.append(c)
    return conflicts


def _cluster_by_comparison_key(candidates: List[Dict[str, Any]]) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    clusters: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for c in candidates:
        claim_str = str(c.get("claim_text", "") or c.get("object_literal", ""))
        if has_c19_marker(claim_str) or re.search(r"\bis an identified\b", claim_str, re.IGNORECASE):
            continue
        key = _build_candidate_comparison_key(c)
        if key[0] and key[1]:
            clusters[key].append(c)
    return clusters


def _extract_distinct_dated_assertions(
    candidates: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Set[str]]:
    distinct_dated: List[Dict[str, Any]] = []
    sources_seen: Set[str] = set()
    for c in candidates:
        dt = c.get("source_date") or c.get("observed_at") or c.get("source_id") or c.get("primary_source_id") or ""
        if dt and dt not in sources_seen:
            sources_seen.add(dt)
            distinct_dated.append(c)
    return distinct_dated, sources_seen


def _try_build_temporal_evolution(
    subj: str, a1: Dict[str, Any], a2: Dict[str, Any], seen_pairs: Set[Tuple[Any, Any]]
) -> Optional[Dict[str, Any]]:
    pair_id = (a1.get("assertion_id"), a2.get("assertion_id"))
    if pair_id in seen_pairs or (pair_id[1], pair_id[0]) in seen_pairs:
        return None
    d1 = str(a1.get("source_date") or a1.get("observed_at") or a1.get("source_id") or "")
    d2 = str(a2.get("source_date") or a2.get("observed_at") or a2.get("source_id") or "")
    if not (d1 and d2 and d1 != d2):
        return None
    c = _evaluate_group_conflict(subj, normalize_copilot_predicate(a2.get("predicate") or "EVOLVED_TO"), a1, a2)
    if c:
        seen_pairs.add(pair_id)
    return c


def _collect_temporal_workstream_evolutions(
    candidates: List[Dict[str, Any]], existing_conflicts: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    seen_pairs: Set[Tuple[Any, Any]] = {
        (c.get("assertion_a", {}).get("assertion_id"), c.get("assertion_b", {}).get("assertion_id"))
        for c in existing_conflicts
    }
    by_subj: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for c in candidates:
        subj = str(c.get("subject") or c.get("subject_node_id") or "").strip().lower()
        if subj:
            by_subj[subj].append(c)
    evolutions: List[Dict[str, Any]] = []
    for subj, group in by_subj.items():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                evo = _try_build_temporal_evolution(subj, group[i], group[j], seen_pairs)
                if evo:
                    evolutions.append(evo)
    return evolutions


def find_contradiction_gaps(
    retriever: Any,
    dialogue: Any,
    max_candidates: int = 30,
) -> GapsResult:
    """Compares assertions with identical (subject, predicate) pairs or temporal workstream shifts across dates/sources."""
    candidates = retriever.retrieve_structured_assertions(dialogue, max_results=max_candidates)
    if not candidates:
        return GapsResult([], False, [])

    clusters = _cluster_by_comparison_key(candidates)
    conflicts: List[Dict[str, Any]] = []
    for (subj, pred), group in clusters.items():
        conflicts.extend(_collect_group_conflicts(subj, pred, group))

    distinct_dated, sources_seen = _extract_distinct_dated_assertions(candidates)
    if not conflicts and len(sources_seen) >= 2:
        conflicts.extend(_collect_temporal_workstream_evolutions(distinct_dated, conflicts))
    has_prior = (len(sources_seen) >= 2) or (len(conflicts) > 0)
    result_assertions = distinct_dated[:8] if distinct_dated else candidates[:8]

    retriever._last_gap_conflicts = conflicts
    return GapsResult(result_assertions, has_prior, conflicts)
