"""Support data structures, metrics, and BM25 scorer for edge design evaluation (Wave 5)."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np
from rank_bm25 import BM25Plus


@dataclass(frozen=True)
class EdgeSystemEvaluationResult:
    system_id: str
    system_name: str
    description: str
    edge_s1: float
    edge_s5: float
    edge_s10: float
    edge_s20: float
    edge_mrr: float
    edge_ndcg10: float
    forward_reverse_s5: float
    name_free_s5: float
    entity_s1: float
    crowding_drop: float
    margin_tie_rate: float
    leak_count: int
    p50_latency_ms: float
    p95_latency_ms: float
    passed_all_acceptance_criteria: bool


def tokenize(text: str) -> list[str]:
    """Tokenize lowercased text into alphanumeric words."""
    return re.findall(r"\w+", str(text or "").lower())


class BM25EdgeScorer:
    """In-memory BM25 scorer for edge verbalisations powered by rank-bm25 BM25Plus."""

    def __init__(self, documents: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus_size = len(documents)
        self.doc_tokens = [tokenize(doc) for doc in documents]
        self.bm25 = BM25Plus(self.doc_tokens, k1=k1, b=b) if self.doc_tokens else None

    def score_query(self, query: str) -> np.ndarray:
        """Compute BM25 scores for all documents given a query string."""
        q_tokens = tokenize(query)
        if not q_tokens or self.bm25 is None or self.corpus_size == 0:
            return np.zeros(self.corpus_size, dtype=np.float64)
        scores = self.bm25.get_scores(q_tokens)
        return np.asarray(scores, dtype=np.float64)


def compute_retrieval_metrics(
    ranked_lists: list[list[str]],
    gold_sets: list[set[str]],
) -> dict[str, float]:
    """Computes Success@{1,5,10,20}, MRR, and nDCG@10."""
    if not ranked_lists or not gold_sets:
        return {"s1": 0.0, "s5": 0.0, "s10": 0.0, "s20": 0.0, "mrr": 0.0, "ndcg10": 0.0}

    s1_hits, s5_hits, s10_hits, s20_hits = 0, 0, 0, 0
    mrrs, ndcgs = [], []
    total = len(ranked_lists)

    for rank_list, gold in zip(ranked_lists, gold_sets, strict=False):
        if not gold:
            continue
        h1 = any(r in gold for r in rank_list[:1])
        h5 = any(r in gold for r in rank_list[:5])
        h10 = any(r in gold for r in rank_list[:10])
        h20 = any(r in gold for r in rank_list[:20])

        s1_hits += int(h1)
        s5_hits += int(h5)
        s10_hits += int(h10)
        s20_hits += int(h20)

        # MRR
        rr = 0.0
        for rank, rid in enumerate(rank_list, 1):
            if rid in gold:
                rr = 1.0 / rank
                break
        mrrs.append(rr)

        # nDCG@10
        dcg = sum(
            1.0 / math.log2(rank + 1)
            for rank, rid in enumerate(rank_list[:10], 1)
            if rid in gold
        )
        ideal = sum(1.0 / math.log2(r + 1) for r in range(1, min(len(gold), 10) + 1))
        ndcgs.append(0.0 if ideal == 0 else dcg / ideal)

    return {
        "s1": round(s1_hits / total, 4),
        "s5": round(s5_hits / total, 4),
        "s10": round(s10_hits / total, 4),
        "s20": round(s20_hits / total, 4),
        "mrr": round(float(np.mean(mrrs)), 4) if mrrs else 0.0,
        "ndcg10": round(float(np.mean(ndcgs)), 4) if ndcgs else 0.0,
    }


def format_head_to_head_table(results: list[EdgeSystemEvaluationResult]) -> str:
    """Formats comparison results into markdown comparison table."""
    header = (
        "| System ID | Architecture Description | S@1 | S@5 | S@10 | Fwd/Rev S@5 | "
        "Name-Free S@5 | Entity S@1 | Margin-Tie | Leaks | p95 Latency | Decision Gate |\n"
        "| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |\n"
    )
    rows = []
    for r in results:
        status_icon = "PASS" if r.passed_all_acceptance_criteria else "FAIL"
        rows.append(
            f"| `{r.system_id}` | {r.system_name} | {r.edge_s1:.3f} | {r.edge_s5:.3f} | "
            f"{r.edge_s10:.3f} | {r.forward_reverse_s5:.3f} | {r.name_free_s5:.3f} | "
            f"{r.entity_s1:.3f} | {r.margin_tie_rate:.1%} | {r.leak_count} | "
            f"{r.p95_latency_ms:.1f}ms | {status_icon} |"
        )
    return header + "\n".join(rows) + "\n"
