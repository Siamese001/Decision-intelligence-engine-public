"""Reciprocal Rank Fusion (RRF) and multi-vector ColBERT token scoring."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import numpy as np

from dual_pipeline.core import digest
from storage.connection import sqlite3

from .colbert_codec import fetch_and_validate_colbert_tokens
from .constants import (
    HYBRID_RETRIEVAL_POLICY,
    HYBRID_RETRIEVAL_POLICY_DIGEST,
    MAX_EXACT_ANCHOR_INJECTIONS_PER_LANE,
    MAX_EXACT_ENTITY_TITLE_MATCHES_PER_LANE,
)
from .filters import normalize_assertion_scope, normalize_object_facet_filters
from .gpu_scoring import score_colbert_tiled
from .records import resolve_retrieval_mode  # noqa: lazy-decouple


def _fetch_missing_colbert_tokens(
    conn: sqlite3.Connection,
    target_ids: List[str],
    cache: Dict[str, np.ndarray],
    strict: bool = True,
) -> None:
    fetch_and_validate_colbert_tokens(conn, target_ids, cache, strict=strict)


def _score_colbert_batched(
    query_mat: np.ndarray,
    target_ids: List[str],
    token_cache: Dict[str, np.ndarray],
    padded_cache: Optional[Any],
    strict: bool = True,
    tile_size: int = 64,
    gpu_adapter: Optional[Any] = None,
) -> Optional[List[Tuple[float, str]]]:
    return score_colbert_tiled(
        query_mat,
        target_ids,
        token_cache,
        padded_cache,
        strict=strict,
        tile_size=tile_size,
        gpu_adapter=gpu_adapter,
    )


def _validate_query_colbert_matrix(
    query_colbert: List[List[float]] | np.ndarray | None, strict: bool
) -> Optional[np.ndarray]:
    if query_colbert is None or len(query_colbert) == 0:
        return None
    matrix = np.asarray(query_colbert, dtype=np.float32)
    if not np.all(np.isfinite(matrix)):
        if strict:
            raise ValueError("Non-finite values detected in query ColBERT matrix")
        matrix = np.nan_to_num(matrix, nan=0.0, posinf=0.0, neginf=0.0)
    return matrix


def _serial_score_candidate(
    query_mat: np.ndarray, dv: Optional[np.ndarray], rid: str, strict: bool
) -> float:
    if dv is None or dv.shape[0] == 0:
        return 0.0
    if dv.ndim < 2 or dv.shape[1] != query_mat.shape[1]:
        if strict:
            raise ValueError(
                f"Dimension mismatch between query ({query_mat.shape[1]}) "
                f"and ColBERT document vector ({dv.shape[1] if dv.ndim >= 2 else dv.shape}) for '{rid}'"
            )
        return 0.0
    try:
        val = float(np.sum(np.max(np.matmul(query_mat, dv.T), axis=1)))
        if not np.isfinite(val):
            if strict:
                raise ValueError(f"Non-finite score calculated for ColBERT candidate '{rid}'")
            return 0.0
        return val
    except Exception:
        if strict:
            raise
        return 0.0


def score_colbert(
    connection: sqlite3.Connection,
    query_colbert: List[List[float]] | np.ndarray | None,
    target_colbert_ids: List[str],
    colbert_cache: Optional[Dict[str, np.ndarray]] = None,
    padded_cache: Optional[Any] = None,
    strict: bool = True,
    tile_size: int = 64,
    gpu_adapter: Optional[Any] = None,
    normalize_by_query_length: bool = False,
) -> List[Tuple[float, str]]:
    """Scores candidate documents using MaxSim token interaction matrix."""
    if not target_colbert_ids:
        return []
    query_colbert_matrix = _validate_query_colbert_matrix(query_colbert, strict)
    if query_colbert_matrix is None:
        return sorted([(0.0, rid) for rid in target_colbert_ids], key=lambda item: item[1])
    canonical_ids = sorted(dict.fromkeys(target_colbert_ids))
    token_cache = colbert_cache if colbert_cache is not None else {}
    _fetch_missing_colbert_tokens(connection, canonical_ids, token_cache, strict=strict)
    scores = (
        _score_colbert_batched(
            query_colbert_matrix, canonical_ids, token_cache, padded_cache,
            strict=strict, tile_size=tile_size, gpu_adapter=gpu_adapter,
        )
        if len(canonical_ids) >= 4
        else None
    )
    if scores is None:
        scores = [
            (_serial_score_candidate(query_colbert_matrix, token_cache.get(rid), rid, strict), rid)
            for rid in canonical_ids
        ]
    if normalize_by_query_length and query_colbert_matrix.shape[0] > 0:
        q_len = float(query_colbert_matrix.shape[0])
        scores = [(val / q_len, rid) for val, rid in scores]
    scores.sort(key=lambda item: (-item[0], item[1]))
    return scores


def renormalize_active_rrf_weights(
    weights: Dict[str, float],
    active_channels: Iterable[str],
) -> Dict[str, float]:
    """Renormalize RRF weights across channels that produced non-empty rankings."""
    active_set = set(active_channels)
    active_sum = sum(w for k, w in weights.items() if k in active_set and w > 0.0)
    if active_sum <= 0.0:
        return dict(weights)
    return {k: (w / active_sum if k in active_set else 0.0) for k, w in weights.items()}


def compute_candidate_union(
    preliminary_ranks: Dict[str, Dict[str, int]],
    preliminary_weights: Dict[str, float],
    rrf_k: int,
    union_limit: int,
    min_per_modality: Optional[int] = None,
) -> List[str]:
    """Computes RRF union pool with per-modality admission quota so no engine is starved."""
    candidate_ids: Set[str] = set().union(*(set(values) for values in preliminary_ranks.values()))
    active_prelim = [name for name, rmap in preliminary_ranks.items() if rmap]
    eff_weights = renormalize_active_rrf_weights(preliminary_weights, active_prelim)

    def _prelim_key(record_id: str) -> Tuple[float, str]:
        return (
            -sum(
                eff_weights.get(name, 0.0) / (rrf_k + component_ranks[record_id])
                for name, component_ranks in preliminary_ranks.items()
                if record_id in component_ranks
            ),
            record_id,
        )

    ranked_all = sorted(candidate_ids, key=_prelim_key)
    if len(ranked_all) <= union_limit:
        return ranked_all
    quota = min_per_modality if min_per_modality is not None else min(15, max(1, union_limit // 4))
    protected: Set[str] = {
        rid
        for name in active_prelim
        if eff_weights.get(name, 0.0) > 0.0
        for rid, rank in preliminary_ranks[name].items()
        if rank <= quota
    }
    admitted_set: Set[str] = set(sorted(protected, key=_prelim_key)[:union_limit])
    for rid in ranked_all:
        if len(admitted_set) >= union_limit:
            break
        admitted_set.add(rid)
    return sorted(admitted_set, key=_prelim_key)


def fuse_hybrid_ranks(
    admitted: List[str],
    ranks: Dict[str, Dict[str, int]],
    weights: Dict[str, float],
    rrf_k: int,
    injected_record_ids: List[str],
    exact_entity_title_record_ids: List[str],
    authorized_records: Dict[str, Dict[str, Any]],
    lane: str,
    top_k: int,
    component_scores: Dict[str, Dict[str, float]],
) -> Tuple[List[Dict[str, Any]], List[Tuple[float, str]]]:
    """Fuses quad-engine ranks using weighted RRF and formats output records."""
    eff_weights = renormalize_active_rrf_weights(
        weights, [name for name, rmap in ranks.items() if rmap]
    )
    fused: List[Tuple[float, str]] = []
    for record_id in admitted:
        score = sum(
            weight / (rrf_k + ranks[name][record_id])
            for name, weight in eff_weights.items()
            if record_id in ranks[name]
        )
        fused.append((score, record_id))
    fused.sort(key=lambda item: (-item[0], item[1]))

    excluded_injected = set(injected_record_ids)
    excluded_all = excluded_injected | set(exact_entity_title_record_ids)
    ordered_record_ids = [
        *injected_record_ids,
        *[rid for rid in exact_entity_title_record_ids if rid not in excluded_injected],
        *[rid for _s, rid in fused if rid not in excluded_all],
    ][: top_k + len(injected_record_ids)]

    fused_scores = {record_id: score for score, record_id in fused}
    results: List[Dict[str, Any]] = []

    for fused_rank, record_id in enumerate(ordered_record_ids, 1):
        artifact = authorized_records[record_id]
        stratum = artifact.get("evidence_stratum") or (
            "DEFINITIONS" if artifact.get("record_type") == "entity_facet"
            else "MUST_USE" if record_id in excluded_injected
            else "SUPPORTING" if lane in {"approved", "candidate"}
            else "BACKGROUND"
        )
        artifact["evidence_stratum"] = stratum
        results.append(
            {
                "record_id": record_id,
                "lane": lane,
                "evidence_stratum": stratum,
                "fused_rank": fused_rank,
                "fused_score": fused_scores.get(record_id, 1.0),
                "dense_rank": ranks["dense"].get(record_id),
                "dense_score": component_scores["dense"].get(record_id),
                "sparse_rank": ranks["sparse"].get(record_id),
                "sparse_score": component_scores["sparse"].get(record_id),
                "bm25_rank": ranks["bm25"].get(record_id),
                "colbert_rank": ranks["colbert"].get(record_id),
                "colbert_score": component_scores["colbert"].get(record_id),
                "exact_anchor_injected": record_id in injected_record_ids,
                "exact_entity_title_matched": (record_id in exact_entity_title_record_ids),
                "record": artifact,
            }
        )

    return results, fused


def build_lane_result(
    *,
    lane: str,
    resolved_mode: str,
    allowed_answer_uses: Iterable[str],
    normalized_facet_filters: Dict[str, List[str]] | None,
    facet_filtering_supported: bool,
    encoded: Dict[str, Any],
    bm25_assertion_ranks: Dict[str, int] | None,
    exact_anchor_ids: List[str],
    dense_scores: List[Tuple[float, str]],
    sparse_scores: List[Tuple[float, str]],
    bm25_ranks: Dict[str, int],
    admitted: List[str],
    injected_record_ids: List[str],
    exact_entity_title_record_ids: List[str],
    colbert_scores: List[Tuple[float, str]],
    authorized_records: Dict[str, Dict[str, Any]],
    weights: Dict[str, float],
    results: List[Dict[str, Any]],
    assertion_scope: Set[str] | None = None,
) -> Dict[str, Any]:
    """Constructs the canonical lane retrieval result envelope."""
    lane_result: Dict[str, Any] = {
        "lane": lane,
        "retrieval_mode": resolved_mode,
        "allowed_answer_uses": sorted(allowed_answer_uses),
        "facet_filters": normalized_facet_filters,
        "facet_filter_semantics": (
            "and_across_vocabularies_or_within_values"
            if normalized_facet_filters is not None
            else None
        ),
        "facet_filtering_supported": facet_filtering_supported,
        "query_representation_digest": digest(
            {
                "dense": encoded["dense"],
                "sparse": encoded["sparse"],
                "colbert": encoded["colbert"],
                "bm25_assertion_ranks": sorted((bm25_assertion_ranks or {}).items()),
                "exact_anchor_assertion_ids": exact_anchor_ids,
            }
        ),
        "dense_candidate_count": len(dense_scores),
        "sparse_candidate_count": len(sparse_scores),
        "bm25_candidate_count": len(bm25_ranks),
        "bm25_executed": bm25_assertion_ranks is not None,
        "candidate_union_count": len(admitted),
        "exact_anchor_injection": {
            "requested_assertion_count": len(exact_anchor_ids),
            "injected_record_ids": injected_record_ids,
            "maximum_per_lane": MAX_EXACT_ANCHOR_INJECTIONS_PER_LANE,
        },
        "entity_title_exact_match": {
            "matched_record_ids": exact_entity_title_record_ids,
            "maximum_per_lane": MAX_EXACT_ENTITY_TITLE_MATCHES_PER_LANE,
        },
        "colbert_candidate_count": len(colbert_scores),
        "authorized_record_count": len(authorized_records),
        "retrieval_policy_id": HYBRID_RETRIEVAL_POLICY["policy_id"],
        "retrieval_policy_digest": HYBRID_RETRIEVAL_POLICY_DIGEST,
        "rrf_weights": weights,
        "results": results,
    }
    if assertion_scope is not None:
        lane_result["assertion_scope_count"] = len(assertion_scope)
        lane_result["assertion_scope_digest"] = digest(sorted(assertion_scope))
    return lane_result


def multi_lane_retrieve(
    retriever: Any,
    query: str,
    allowed_lanes: List[str],
    *,
    classification: str,
    audiences: List[str],
    retrieval_mode: Optional[str] = None,
    encoded_query: Optional[Dict[str, Any]] = None,
    facet_filters: Optional[Dict[str, Iterable[str]]] = None,
    allowed_assertion_ids: Optional[Iterable[str]] = None,
    bm25_assertion_ranks_by_lane: Optional[Dict[str, Dict[str, int]]] = None,
    exact_anchor_assertion_ids_by_lane: Optional[Dict[str, Iterable[str]]] = None,
    rrf_weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Orchestrates parallel or sequential retrieval across authorized lanes."""
    resolved_encoding = (
        encoded_query if encoded_query is not None else retriever.backend.encode(query)
    )
    normalized_facet_filters = normalize_object_facet_filters(facet_filters)
    assertion_scope = normalize_assertion_scope(allowed_assertion_ids)
    resolved_mode, _ = resolve_retrieval_mode(allowed_lanes, retrieval_mode)
    if exact_anchor_assertion_ids_by_lane is not None:
        unexpected_lanes = set(exact_anchor_assertion_ids_by_lane) - set(allowed_lanes)
        if unexpected_lanes:
            raise ValueError(
                "exact anchor assertion IDs supplied for unauthorized lanes: "
                f"{sorted(unexpected_lanes)}"
            )
    lane_results: Dict[str, Any] = {}
    for lane in allowed_lanes:
        top_k = (
            {"approved": 12, "candidate": 4, "external_public": 4}[lane]
            if resolved_mode == "strategic" else 20
        )
        lane_results[lane] = retriever.retrieve_lane(
            query, lane, classification=classification, audiences=audiences,
            top_k=top_k, retrieval_mode=resolved_mode, encoded_query=resolved_encoding,
            facet_filters=normalized_facet_filters, allowed_assertion_ids=assertion_scope,
            bm25_assertion_ranks=(
                None if bm25_assertion_ranks_by_lane is None else bm25_assertion_ranks_by_lane.get(lane, {})
            ),
            exact_anchor_assertion_ids=(
                () if exact_anchor_assertion_ids_by_lane is None else exact_anchor_assertion_ids_by_lane.get(lane, ())
            ),
            rrf_weights=rrf_weights,
        )
    record_ids = [
        result["record_id"] for lane in allowed_lanes for result in lane_results[lane]["results"]
    ]
    if len(record_ids) != len(set(record_ids)):
        raise RuntimeError("retrieval lane contamination detected")
    retrieval = {
        "query": query,
        "allowed_lanes": allowed_lanes,
        "classification": classification,
        "audiences": audiences,
        "retrieval_mode": resolved_mode,
        "facet_filters": normalized_facet_filters,
        "lane_blending": False,
        "bm25_executed": all(lane_results[lane]["bm25_executed"] for lane in allowed_lanes),
        "retrieval_policy_id": HYBRID_RETRIEVAL_POLICY["policy_id"],
        "retrieval_policy_digest": HYBRID_RETRIEVAL_POLICY_DIGEST,
        "lanes": lane_results,
        "final_record_count": len(record_ids),
    }
    if assertion_scope is not None:
        retrieval["assertion_scope_count"] = len(assertion_scope)
        retrieval["assertion_scope_digest"] = digest(sorted(assertion_scope))
    return retrieval
