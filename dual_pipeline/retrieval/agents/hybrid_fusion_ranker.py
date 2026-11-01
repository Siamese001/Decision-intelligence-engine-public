"""Agent-C3: Quad-Engine Hybrid Fusion Ranker Agent.

Combines retrieval candidates across dense, sparse, BM25, and late-interaction
multi-vectors using Reciprocal Rank Fusion (RRF).
Enforces Invariant 8.1: Retrieval rank score is strictly decoupled from option scores.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from dual_pipeline.a2a import (
    A2ATask,
    AgentCard,
    ComputePosture,
    ModelBinding,
)

logger = logging.getLogger("dual_pipeline.agents.hybrid_fusion_ranker")


def get_hybrid_fusion_ranker_card() -> AgentCard:
    return AgentCard(
        agent_id="urn:nee:agent:pipeline-c:hybrid-fusion-ranker:1.0.0",
        name="HybridFusionRanker",
        version="1.0.0",
        role="Information Retrieval Scientist",
        domain_focus="Reciprocal Rank Fusion across Dense, Sparse, BM25, and ColBERT multi-vectors",
        compute_posture=ComputePosture.CLIENT_SAFE,
        model_binding=ModelBinding(
            model_id="deterministic",
            provider="local",
            temperature=0.0,
        ),
        skills=["ReciprocalRankFusion", "DecoupleRetrievalFromOptionRank", "CalibrateEvidenceRelevance"],
        allowed_tools=["rrf_scoring"],
    )


class HybridFusionRanker:
    """Agent performing Reciprocal Rank Fusion (RRF) across retrieval streams."""

    def __init__(self, rrf_k: int = 60) -> None:
        self.rrf_k = rrf_k
        self.card = get_hybrid_fusion_ranker_card()

    async def handle_task(self, task: A2ATask) -> Dict[str, Any]:
        """Fuses multiple ranked lists of retrieved facts."""
        ranked_lists: List[List[Dict[str, Any]]] = task.input_payload.get("ranked_lists", [])
        limit = int(task.input_payload.get("limit", 10))
        stream_weights: List[float] = task.input_payload.get("stream_weights", [])

        logger.info(f"HybridFusionRanker fusing {len(ranked_lists)} candidate lists (limit={limit})")

        rrf_scores: Dict[str, float] = {}
        node_catalog: Dict[str, Dict[str, Any]] = {}

        for idx, r_list in enumerate(ranked_lists):
            weight = float(stream_weights[idx]) if idx < len(stream_weights) else 1.0
            for rank, item in enumerate(r_list):
                node_id = (
                    item.get("node_id")
                    or item.get("id")
                    or item.get("assertion_id")
                    or f"stream_{idx}:rank_{rank}"
                )
                if node_id not in node_catalog:
                    node_catalog[node_id] = dict(item)

                # Calibrated RRF formula: weight / (k + rank)
                contribution = weight / (self.rrf_k + (rank + 1))
                rrf_scores[node_id] = rrf_scores.get(node_id, 0.0) + contribution

        # Sort by RRF score descending
        sorted_nodes = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)

        fused_facts: List[Dict[str, Any]] = []
        for node_id, score in sorted_nodes[:limit]:
            fact = node_catalog[node_id]
            fact["rrf_retrieval_score"] = round(score, 6)
            # Invariant 8.1 enforcement: Ensure option_score is NOT set here
            fact.pop("option_score", None)
            fused_facts.append(fact)

        return {
            "task_id": task.task_id,
            "status": "COMPLETED",
            "fused_facts": fused_facts,
            "fused_count": len(fused_facts),
            "input_list_count": len(ranked_lists),
        }
