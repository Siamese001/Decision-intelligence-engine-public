"""
copilot_retrieval_fusion.py

High-salience retrieval enhancement algorithms for Live Copilot:
1. Reciprocal Rank Fusion (RRF): Combines multi-query lexical and dense vector rankings.
2. Multi-Query Expansion: Extracts query variants from primary dialogue, recent asks, and active topics.
3. Maximal Marginal Relevance (MMR): Diversifies retrieved assertions to penalize near-duplicates.
4. 1-Hop Graph Expansion: Expands entity neighbors under strict scope and Rule C-19 filtering.
5. Cross-Encoder Reranking: Optional high-precision re-scoring under feature flag.
"""
from __future__ import annotations

from collections import defaultdict
import logging
import math
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .c19_firewall import (
    has_c19_marker,
    is_c19_quarantined_assertion,
)
from .copilot_connection_policy import (
    is_assertion_in_scope,
    is_enterprise_policy,
)

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.copilot_retrieval_fusion")

_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_\-]{3,}\b|\b[A-Z0-9_\-]{2}\b")


def tokenize_text_set(text: str) -> Set[str]:
    """Extracts lowercase tokens (>=3 chars or 2-char uppercase acronyms)."""
    if not text:
        return set()
    raw = _TOKEN_RE.findall(text)
    return {w.lower() for w in raw if not w.startswith("-")}


def reciprocal_rank_fusion(
    rankings: List[List[Tuple[str, float]]],
    k: int = 60,
    weights: Optional[List[float]] = None,
) -> List[Tuple[str, float]]:
    """Merges multiple ranked lists of (item_id, score) using Reciprocal Rank Fusion (RRF).

    Score formula: RRF(d) = sum_m ( w_m / (k + rank_m(d)) )
    where rank_m is 1-indexed.
    """
    if not rankings:
        return []

    eff_weights = weights if weights and len(weights) == len(rankings) else [1.0] * len(rankings)
    rrf_scores: Dict[str, float] = defaultdict(float)

    for rank_list, weight in zip(rankings, eff_weights):
        for rank_idx, (item_id, _) in enumerate(rank_list, start=1):
            rrf_scores[item_id] += weight / float(k + rank_idx)

    merged = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
    return merged


def compute_copilot_rrf_weights(num_lexical_lists: int, has_dense_list: bool) -> List[float]:
    """Compute calibrated RRF list weights so N lexical query variants do not dilute BGE-M3 dense ranking."""
    if num_lexical_lists <= 0:
        return [1.0] if has_dense_list else []
    if not has_dense_list:
        raw = [1.0 if i == 0 else 0.5 for i in range(num_lexical_lists)]
        s = sum(raw)
        return [round(w / s, 6) for w in raw]
    lex_budget = 0.60
    dense_budget = 0.40
    raw_lex = [1.0 if i == 0 else 0.5 for i in range(num_lexical_lists)]
    s_lex = sum(raw_lex)
    return [*(round(lex_budget * w / s_lex, 6) for w in raw_lex), dense_budget]


def compute_token_jaccard(tokens1: Set[str], tokens2: Set[str]) -> float:
    """Computes Jaccard similarity between two token sets."""
    if not tokens1 or not tokens2:
        return 0.0
    inter = len(tokens1 & tokens2)
    union = len(tokens1 | tokens2)
    return float(inter) / float(union) if union > 0 else 0.0


def compute_assertion_similarity(
    a1: Dict[str, Any],
    a2: Dict[str, Any],
    dense_matrix: Optional[Any] = None,
    record_id_to_idx: Optional[Dict[str, int]] = None,
) -> float:
    """Computes similarity between two assertions using token overlap for near-duplicate detection."""
    t1 = a1.get("_tokens")
    if t1 is None:
        c1 = str(a1.get("claim_text") or a1.get("object_literal") or "")
        t1 = tokenize_text_set(c1)
        a1["_tokens"] = t1

    t2 = a2.get("_tokens")
    if t2 is None:
        c2 = str(a2.get("claim_text") or a2.get("object_literal") or "")
        t2 = tokenize_text_set(c2)
        a2["_tokens"] = t2

    lex_sim = compute_token_jaccard(t1, t2)
    if dense_matrix is not None and record_id_to_idx:
        id1 = str(a1.get("assertion_id") or a1.get("id") or "")
        id2 = str(a2.get("assertion_id") or a2.get("id") or "")
        idx1 = record_id_to_idx.get(id1)
        idx2 = record_id_to_idx.get(id2)
        if idx1 is not None and idx2 is not None:
            dense_cos = max(0.0, min(1.0, float(dense_matrix[idx1] @ dense_matrix[idx2])))
            return (0.5 * dense_cos) + (0.5 * lex_sim)
    return lex_sim



def _calculate_mmr_score(
    item: Dict[str, Any],
    selected: List[Dict[str, Any]],
    raw_score: float,
    max_score: float,
    lambda_param: float,
    dense_matrix: Optional[Any],
    record_id_to_idx: Optional[Dict[str, int]],
) -> float:
    norm_score = raw_score / max_score
    if not selected:
        return norm_score
    max_sim = max(
        compute_assertion_similarity(item, s, dense_matrix, record_id_to_idx)
        for s in selected
    )
    return (lambda_param * norm_score) - ((1.0 - lambda_param) * max_sim)


def _find_next_best_mmr_candidate(
    candidate_items: List[Dict[str, Any]],
    candidate_scores: Dict[str, float],
    selected: List[Dict[str, Any]],
    selected_indices: Set[int],
    max_score: float,
    lambda_param: float,
    dense_matrix: Optional[Any],
    record_id_to_idx: Optional[Dict[str, int]],
) -> Tuple[int, Optional[Dict[str, Any]]]:
    best_item: Optional[Dict[str, Any]] = None
    best_mmr = -1e9
    best_idx = -1

    for idx, item in enumerate(candidate_items):
        if idx in selected_indices:
            continue
        aid = item.get("assertion_id", "")
        raw_score = candidate_scores.get(aid, 0.0)
        mmr_val = _calculate_mmr_score(
            item, selected, raw_score, max_score, lambda_param, dense_matrix, record_id_to_idx
        )
        if mmr_val > best_mmr:
            best_mmr = mmr_val
            best_item = item
            best_idx = idx

    return best_idx, best_item


def maximal_marginal_relevance(
    candidate_items: List[Dict[str, Any]],
    candidate_scores: Dict[str, float],
    max_results: int = 10,
    lambda_param: float = 0.7,
    dense_matrix: Optional[Any] = None,
    record_id_to_idx: Optional[Dict[str, int]] = None,
) -> List[Dict[str, Any]]:
    """Selects top assertions using Maximal Marginal Relevance (MMR) for diversity.

    Formula: MMR(d) = lambda * S_norm(d) - (1 - lambda) * max_{s in S} Sim(d, s)
    """
    if not candidate_items or max_results <= 0:
        return []

    if len(candidate_items) <= max_results:
        return list(candidate_items)

    max_score = max(candidate_scores.values()) if candidate_scores else 1.0
    if max_score <= 0.0:
        max_score = 1.0

    selected: List[Dict[str, Any]] = []
    selected_indices: Set[int] = set()
    num_to_select = min(max_results, len(candidate_items))

    for _ in range(num_to_select):
        best_idx, best_item = _find_next_best_mmr_candidate(
            candidate_items, candidate_scores, selected, selected_indices,
            max_score, lambda_param, dense_matrix, record_id_to_idx,
        )
        if best_item is not None and best_idx >= 0:
            selected.append(best_item)
            selected_indices.add(best_idx)
        else:
            break

    return selected


def _extract_primary_query(query: Any) -> str:
    """Extracts normalized text of primary query."""
    if hasattr(query, "retrieval_seeds"):
        seeds = query.retrieval_seeds()
        return " ".join(seeds).strip() if seeds else ""
    if hasattr(query, "text"):
        return str(query.text).strip()
    if isinstance(query, dict):
        return str(query.get("text") or query.get("prompt") or "").strip()
    return str(query).strip()


def _extract_ask_query(query: Any, primary_text: str) -> str:
    """Extracts latest open question from query context if distinct from primary text."""
    ask_text = ""
    if hasattr(query, "ask_ledger"):
        ledger = getattr(query, "ask_ledger", None)
        if callable(ledger):
            ledger = ledger()
        if hasattr(ledger, "get_open_asks"):
            open_asks = ledger.get_open_asks()
            if open_asks:
                ask_text = open_asks[-1].text
    elif hasattr(query, "last_ask"):
        ask_text = str(query.last_ask)
    elif isinstance(query, dict) and "last_ask" in query:
        ask_text = str(query["last_ask"])

    cand = ask_text.strip()
    return cand if (cand and cand.lower() != primary_text.lower()) else ""


def _extract_topic_query(query: Any, primary_text: str, ask_text: str) -> str:
    """Extracts active topic segment text if distinct from primary and ask queries."""
    topic_text = ""
    if hasattr(query, "flow_state") and getattr(query.flow_state, "topic_segments", None):
        seg = query.flow_state.topic_segments[-1]
        topic_text = str(seg.get("text") or " ".join(seg.get("key_entities", [])))
    elif hasattr(query, "active_topic"):
        topic_text = str(query.active_topic)
    elif isinstance(query, dict) and "topic_segment" in query:
        topic_text = str(query["topic_segment"])

    cand = topic_text.strip()
    forbidden = {primary_text.lower(), ask_text.lower()}
    return cand if (cand and cand.lower() not in forbidden) else ""


def expand_multi_queries(query: Any) -> Dict[str, str]:
    """Extracts multi-query expansion variants from query context: primary, ask, and topic."""
    queries: Dict[str, str] = {}
    primary = _extract_primary_query(query)
    if primary:
        queries["primary"] = primary

    ask = _extract_ask_query(query, primary)
    if ask:
        queries["ask"] = ask

    topic = _extract_topic_query(query, primary, ask)
    if topic:
        queries["topic"] = topic

    return queries


def _is_neighbor_valid(
    n_asrt: Dict[str, Any],
    seen_ids: Set[str],
    engagement_id: Optional[str],
    permitted_engagements: Set[str],
) -> bool:
    """Validates candidate neighbor against deduplication, C-19 quarantine, and scope boundaries."""
    aid = n_asrt.get("assertion_id")
    if not aid or aid in seen_ids:
        return False

    claim = str(n_asrt.get("claim_text") or n_asrt.get("object_literal") or "")
    if has_c19_marker(claim) or is_c19_quarantined_assertion(n_asrt):
        return False

    if engagement_id:
        return is_assertion_in_scope(n_asrt, engagement_id, permitted_engagements)
    return is_enterprise_policy(n_asrt)


def _collect_anchor_nodes(cand: Dict[str, Any]) -> List[str]:
    """Extracts valid anchor entity nodes from candidate assertion."""
    subj = cand.get("subject") or cand.get("subject_node_id")
    obj = cand.get("object") or cand.get("object_node_id")
    return [n for n in (subj, obj) if n and not str(n).startswith("literal:")]


def _append_valid_node_neighbors(
    node_id: str,
    salient_assertions: Dict[str, List[Dict[str, Any]]],
    seen_ids: Set[str],
    engagement_id: Optional[str],
    permitted_engagements: Set[str],
    expanded: List[Dict[str, Any]],
    max_expanded: int,
) -> bool:
    """Appends valid neighbor assertions for node; returns True if max_expanded reached."""
    neighbors = salient_assertions.get(node_id, [])
    for n_asrt in neighbors:
        if not _is_neighbor_valid(n_asrt, seen_ids, engagement_id, permitted_engagements):
            continue
        aid = n_asrt.get("assertion_id")
        seen_ids.add(aid)
        expanded.append(n_asrt)
        if len(expanded) >= max_expanded:
            return True
    return False


def expand_1hop_graph_neighbors(
    initial_candidates: List[Dict[str, Any]],
    topology: Any,
    salient_assertions: Dict[str, List[Dict[str, Any]]],
    engagement_id: Optional[str],
    permitted_engagements: Set[str],
    max_expanded: int = 15,
) -> List[Dict[str, Any]]:
    """Expands 1-hop neighbor assertions for top candidate entities, enforcing strict scope check."""
    if not initial_candidates or not salient_assertions:
        return []

    seen_ids = {c.get("assertion_id") for c in initial_candidates if c.get("assertion_id")}
    expanded: List[Dict[str, Any]] = []

    for cand in initial_candidates[:5]:
        for node_id in _collect_anchor_nodes(cand):
            if _append_valid_node_neighbors(
                node_id, salient_assertions, seen_ids, engagement_id, permitted_engagements, expanded, max_expanded
            ):
                return expanded

    return expanded



def cross_encoder_rerank(
    candidates: List[Dict[str, Any]],
    query_str: str,
    cross_encoder_fn: Optional[Callable[[str, str], float]] = None,
) -> List[Dict[str, Any]]:
    """Re-ranks candidates using a high-precision cross-encoder function if provided."""
    if not candidates or cross_encoder_fn is None or not callable(cross_encoder_fn):
        return candidates

    scored: List[Tuple[float, Dict[str, Any]]] = []
    for cand in candidates:
        claim = str(cand.get("claim_text") or cand.get("object_literal") or "")
        try:
            score = float(cross_encoder_fn(query_str, claim))
        except Exception as e:
            logger.warning("Cross-encoder re-ranking failed for candidate %s: %s", cand.get("assertion_id"), e)
            score = 0.0
        scored.append((score, cand))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored]
