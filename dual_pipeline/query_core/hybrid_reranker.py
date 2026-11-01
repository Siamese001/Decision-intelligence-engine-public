"""Reciprocal Rank Fusion (RRF) and BM25 lexical reranker.

Combines BGE-M3 dense/sparse vectors with knowledge-graph topological proximity distance.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, Union

from rank_bm25 import BM25Okapi

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RankedCandidate:
    """Represents a fused retrieval candidate with rank provenance."""
    candidate_id: str
    rrf_score: float
    graph_rank: Optional[int] = None
    vector_rank: Optional[int] = None
    graph_score: Optional[float] = None
    vector_score: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def node_id(self) -> str:
        """Alias for candidate_id matching graph conventions."""
        return self.candidate_id

    def to_dict(self) -> Dict[str, Any]:
        """Serialize candidate to standard dictionary."""
        return {
            "candidate_id": self.candidate_id,
            "node_id": self.candidate_id,
            "rrf_score": self.rrf_score,
            "graph_rank": self.graph_rank,
            "vector_rank": self.vector_rank,
            "graph_score": self.graph_score,
            "vector_score": self.vector_score,
            "metadata": dict(self.metadata),
        }


def _extract_id_and_score(
    item: Union[str, Dict[str, Any], RankedCandidate],
    idx: int,
    default_score_key: str = "score",
) -> Tuple[str, Optional[int], Optional[float], Dict[str, Any]]:
    """Normalizes candidate items into (candidate_id, explicit_rank, score, metadata)."""
    if isinstance(item, str):
        cid = item.strip()
        return cid, idx + 1, None, {}

    if isinstance(item, RankedCandidate):
        return item.candidate_id, item.graph_rank or item.vector_rank or (idx + 1), item.rrf_score, dict(item.metadata)

    if isinstance(item, Mapping):
        cid = str(item.get("candidate_id") or item.get("node_id") or item.get("id") or item.get("record_id") or f"cand_{idx}").strip()
        explicit_rank = item.get("rank")
        if explicit_rank is not None:
            try:
                explicit_rank = int(explicit_rank)
            except (ValueError, TypeError):
                explicit_rank = idx + 1
        else:
            explicit_rank = idx + 1

        raw_score = item.get("score") if "score" in item else item.get(default_score_key)
        score = float(raw_score) if raw_score is not None else None
        meta = {k: v for k, v in item.items() if k not in ("candidate_id", "node_id", "id", "record_id", "rank", "score", default_score_key)}
        return cid, explicit_rank, score, meta

    cid = str(item).strip()
    return cid, idx + 1, None, {}


def _parse_mapping_stream(
    mapping: Mapping[str, Any],
    default_score_key: str,
) -> Dict[str, Tuple[int, Optional[float], Dict[str, Any]]]:
    """Parse candidate mapping of {id: score} or {id: metadata_dict}."""
    items: List[Tuple[str, Any]] = list(mapping.items())
    if all(isinstance(v, (int, float)) for _, v in items):
        sorted_items = sorted(items, key=lambda kv: float(kv[1]), reverse=True)
        return {
            str(cid).strip(): (rk, float(sc), {})
            for rk, (cid, sc) in enumerate(sorted_items, start=1)
            if str(cid).strip()
        }
    parsed: Dict[str, Tuple[int, Optional[float], Dict[str, Any]]] = {}
    for idx, (cid, val) in enumerate(items):
        clean_cid = str(cid).strip()
        if isinstance(val, Mapping):
            _, rk, sc, meta = _extract_id_and_score(val, idx, default_score_key)
            parsed[clean_cid] = (rk or (idx + 1), sc, meta)
        else:
            parsed[clean_cid] = (idx + 1, None, {})
    return parsed


def _parse_sequence_stream(
    stream: Sequence[Any],
    default_score_key: str,
) -> Dict[str, Tuple[int, Optional[float], Dict[str, Any]]]:
    """Parse candidate sequence into deduplicated best-rank map."""
    parsed: Dict[str, Tuple[int, Optional[float], Dict[str, Any]]] = {}
    for idx, item in enumerate(stream):
        cid, rk, sc, meta = _extract_id_and_score(item, idx, default_score_key)
        if not cid:
            continue
        if cid in parsed:
            existing_rk, existing_sc, existing_meta = parsed[cid]
            if rk is not None and rk < existing_rk:
                parsed[cid] = (rk, sc or existing_sc, {**existing_meta, **meta})
        else:
            parsed[cid] = (rk or (idx + 1), sc, meta)
    return parsed


def _parse_candidate_stream(
    stream: Union[Sequence[Union[str, Dict[str, Any], RankedCandidate]], Mapping[str, Any]],
    default_score_key: str = "score",
) -> Dict[str, Tuple[int, Optional[float], Dict[str, Any]]]:
    """Parses a candidate stream into a deterministic mapping of {candidate_id: (rank, score, metadata)}."""
    if not stream:
        return {}
    if isinstance(stream, Mapping):
        return _parse_mapping_stream(stream, default_score_key)
    return _parse_sequence_stream(stream, default_score_key)


def rank_by_topological_distance(
    neighborhood_or_nodes: Union[Dict[str, Any], Sequence[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Ranks graph neighborhood nodes by topological hop distance ascending.

    Args:
        neighborhood_or_nodes: Result dict from traverse_k_hop_neighborhood, or node list.

    Returns:
        List of node dicts with assigned 'graph_rank' and normalized 'graph_score'.
    """
    if isinstance(neighborhood_or_nodes, Mapping) and "nodes" in neighborhood_or_nodes:
        raw_nodes = neighborhood_or_nodes["nodes"]
        if isinstance(raw_nodes, Mapping):
            node_list = list(raw_nodes.values())
        else:
            node_list = list(raw_nodes)
    elif isinstance(neighborhood_or_nodes, Sequence):
        node_list = list(neighborhood_or_nodes)
    elif isinstance(neighborhood_or_nodes, Mapping):
        node_list = list(neighborhood_or_nodes.values())
    else:
        node_list = []

    # Sort primarily by depth/min_hop ascending, secondarily by node_id
    def _hop_key(item: Dict[str, Any]) -> Tuple[int, str]:
        hop = item.get("depth", item.get("min_hop", item.get("hop", 99)))
        try:
            hop_val = int(hop)
        except (ValueError, TypeError):
            hop_val = 99
        nid = str(item.get("node_id") or item.get("candidate_id") or "")
        return (hop_val, nid)

    sorted_nodes = sorted(node_list, key=_hop_key)

    ranked: List[Dict[str, Any]] = []
    for rank, node in enumerate(sorted_nodes, start=1):
        clean_node = dict(node)
        hop = clean_node.get("depth", clean_node.get("min_hop", clean_node.get("hop", 0)))
        try:
            hop_int = int(hop)
        except (ValueError, TypeError):
            hop_int = 0
        clean_node["graph_rank"] = rank
        # Normalized proximity score: 1.0 / (1.0 + hop)
        clean_node["graph_score"] = round(1.0 / (1.0 + hop_int), 4)
        ranked.append(clean_node)

    return ranked


def _build_fused_candidate(
    cid: str,
    g_info: Optional[Tuple[int, Optional[float], Dict[str, Any]]],
    v_info: Optional[Tuple[int, Optional[float], Dict[str, Any]]],
    rrf_k: int,
    graph_weight: float,
    vector_weight: float,
) -> RankedCandidate:
    """Build a RankedCandidate with combined RRF score and merged metadata."""
    g_rank, g_score, g_meta = (g_info[0], g_info[1], g_info[2]) if g_info else (None, None, {})
    v_rank, v_score, v_meta = (v_info[0], v_info[1], v_info[2]) if v_info else (None, None, {})
    score = 0.0
    if g_rank is not None:
        score += graph_weight * (1.0 / (rrf_k + g_rank))
    if v_rank is not None:
        score += vector_weight * (1.0 / (rrf_k + v_rank))
    return RankedCandidate(
        candidate_id=cid,
        rrf_score=round(score, 8),
        graph_rank=g_rank,
        vector_rank=v_rank,
        graph_score=g_score,
        vector_score=v_score,
        metadata={**g_meta, **v_meta},
    )


def reciprocal_rank_fusion(
    graph_ranks: Union[Sequence[Union[str, Dict[str, Any], RankedCandidate]], Mapping[str, Any]],
    vector_ranks: Union[Sequence[Union[str, Dict[str, Any], RankedCandidate]], Mapping[str, Any]],
    *,
    rrf_k: int = 60,
    graph_weight: float = 0.5,
    vector_weight: float = 0.5,
    limit: Optional[int] = None,
) -> List[RankedCandidate]:
    """Execute Reciprocal Rank Fusion combining graph topological ranks with vector ranks."""
    if rrf_k <= 0:
        raise ValueError(f"rrf_k must be a positive integer, got {rrf_k}")
    if graph_weight < 0.0 or vector_weight < 0.0:
        raise ValueError(f"Weights must be non-negative, got graph={graph_weight}, vector={vector_weight}")

    parsed_graph = _parse_candidate_stream(graph_ranks, default_score_key="graph_score")
    parsed_vector = _parse_candidate_stream(vector_ranks, default_score_key="vector_score")
    all_candidate_ids = set(parsed_graph.keys()) | set(parsed_vector.keys())
    if not all_candidate_ids:
        return []

    fused = [
        _build_fused_candidate(cid, parsed_graph.get(cid), parsed_vector.get(cid), rrf_k, graph_weight, vector_weight)
        for cid in all_candidate_ids
    ]
    fused.sort(key=lambda c: (-c.rrf_score, min((r for r in (c.graph_rank, c.vector_rank) if r is not None), default=999999), c.candidate_id))
    return fused[:limit] if (limit is not None and limit > 0) else fused


class HybridGraphVectorReranker:
    """Configurable hybrid reranker fusing graph topology and semantic vectors."""

    def __init__(
        self,
        *,
        rrf_k: int = 60,
        graph_weight: float = 0.5,
        vector_weight: float = 0.5,
    ) -> None:
        if rrf_k <= 0:
            raise ValueError(f"rrf_k must be positive, got {rrf_k}")
        if graph_weight < 0.0 or vector_weight < 0.0:
            raise ValueError("Weights must be non-negative")
        self.rrf_k = rrf_k
        self.graph_weight = graph_weight
        self.vector_weight = vector_weight

    def rerank(
        self,
        graph_ranks: Union[Sequence[Union[str, Dict[str, Any], RankedCandidate]], Mapping[str, Any]],
        vector_ranks: Union[Sequence[Union[str, Dict[str, Any], RankedCandidate]], Mapping[str, Any]],
        limit: Optional[int] = None,
    ) -> List[RankedCandidate]:
        """Reranks candidates using instance weights and RRF formula."""
        return reciprocal_rank_fusion(
            graph_ranks=graph_ranks,
            vector_ranks=vector_ranks,
            rrf_k=self.rrf_k,
            graph_weight=self.graph_weight,
            vector_weight=self.vector_weight,
            limit=limit,
        )

    def fuse_neighborhood(
        self,
        neighborhood: Union[Dict[str, Any], Sequence[Dict[str, Any]]],
        vector_candidates: Union[Sequence[Union[str, Dict[str, Any]]], Mapping[str, Any]],
        limit: Optional[int] = None,
    ) -> List[RankedCandidate]:
        """Converts graph neighborhood to topological rankings and fuses with vectors."""
        ranked_graph = rank_by_topological_distance(neighborhood)
        return self.rerank(ranked_graph, vector_candidates, limit=limit)


# -------------------------------------------------------------------------
# Retrieval Evaluation & Benchmark Validation Helpers
# -------------------------------------------------------------------------

def precision_at_k(
    ranked_candidate_ids: Sequence[str],
    relevant_ids: Union[Set[str], Mapping[str, int]],
    k: int = 10,
    relevance_threshold: int = 1,
) -> float:
    """Computes Precision@k against set of relevant ground truth IDs."""
    if k <= 0 or not ranked_candidate_ids:
        return 0.0
    cutoff = list(ranked_candidate_ids)[:k]
    if isinstance(relevant_ids, Mapping):
        positives = {cid for cid, grade in relevant_ids.items() if grade >= relevance_threshold}
    else:
        positives = set(relevant_ids)

    if not positives:
        return 0.0

    hits = sum(1 for cid in cutoff if cid in positives)
    return hits / float(k)


def reciprocal_rank(
    ranked_candidate_ids: Sequence[str],
    relevant_ids: Union[Set[str], Mapping[str, int]],
    k: int = 10,
    relevance_threshold: int = 1,
) -> float:
    """Computes Reciprocal Rank (1/rank) for the first relevant candidate within top-k."""
    if k <= 0 or not ranked_candidate_ids:
        return 0.0
    if isinstance(relevant_ids, Mapping):
        positives = {cid for cid, grade in relevant_ids.items() if grade >= relevance_threshold}
    else:
        positives = set(relevant_ids)

    if not positives:
        return 0.0

    for rank, cid in enumerate(list(ranked_candidate_ids)[:k], start=1):
        if cid in positives:
            return 1.0 / float(rank)
    return 0.0


def evaluate_retrieval_benchmark(
    fused_results: Mapping[str, Sequence[str]],
    qrels: Mapping[str, Union[Set[str], Mapping[str, int]]],
    k: int = 10,
    relevance_threshold: int = 1,
) -> Dict[str, Any]:
    """Evaluates Precision@k and MRR across benchmark queries."""
    if not qrels:
        return {
            "total_queries": 0,
            "mean_precision_at_k": 0.0,
            "mean_reciprocal_rank": 0.0,
            "passed": False,
        }

    precisions: List[float] = []
    reciprocal_ranks: List[float] = []

    for qid, relevant in qrels.items():
        retrieved = fused_results.get(qid, [])
        p = precision_at_k(retrieved, relevant, k=k, relevance_threshold=relevance_threshold)
        rr = reciprocal_rank(retrieved, relevant, k=k, relevance_threshold=relevance_threshold)
        precisions.append(p)
        reciprocal_ranks.append(rr)

    mean_p = sum(precisions) / len(precisions) if precisions else 0.0
    mean_mrr = sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 0.0

    return {
        "total_queries": len(qrels),
        "k": k,
        "mean_precision_at_k": round(mean_p, 4),
        "mean_reciprocal_rank": round(mean_mrr, 4),
        "query_metrics": {
            qid: {"precision": round(p, 4), "mrr": round(rr, 4)}
            for qid, p, rr in zip(qrels.keys(), precisions, reciprocal_ranks)
        },
        "passed": mean_p >= 0.90 and mean_mrr >= 0.85,
    }


class BM25Reranker:
    """Deterministic BM25 lexical reranker powered by rank-bm25 BM25Okapi."""

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        import re
        return re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", (text or "").lower())

    def __init__(self, corpus: Sequence[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.corpus = list(corpus)
        self.tokenized_corpus = [self._tokenize(doc) for doc in self.corpus]
        self.bm25 = BM25Okapi(self.tokenized_corpus, k1=k1, b=b)

    def score_query(self, query: str) -> list[float]:
        """Compute BM25 score vector for all corpus documents."""
        tokens = self._tokenize(query)
        if not tokens or not self.corpus:
            return [0.0] * len(self.corpus)
        scores = self.bm25.get_scores(tokens)
        return [float(s) for s in scores]

    def rank(self, query: str, limit: Optional[int] = None) -> list[tuple[int, float]]:
        """Return (doc_idx, score) tuples sorted by descending BM25 score."""
        scores = self.score_query(query)
        ranked = sorted(enumerate(scores), key=lambda x: -x[1])
        return ranked[:limit] if limit else ranked
