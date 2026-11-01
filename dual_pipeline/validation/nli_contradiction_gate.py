from typing import Any, Dict, List


class NLIContradictionGate:
    """
    Pairwise assertion cross-attention validator evaluating contradiction scores.
    """
    def __init__(self, reranker_service: Any = None):
        self.reranker = reranker_service or __import__('dual_pipeline.retrieval.bge_reranker_service', fromlist=['BgeRerankerService']).BgeRerankerService()
        self.contradiction_margin = 0.40

    def evaluate_conflict(self, active_facts: List[Dict[str, Any]], candidate_claim: str) -> Dict[str, Any]:
        """
        Evaluates a candidate claim against active facts.
        Returns contradiction state and confidence.
        """
        if not active_facts:
            return {"has_contradiction": False, "max_conflict_score": 0.0, "conflicting_fact": None}

        # BGE Reranker assigns high scores to semantic match/entailment
        # How do we detect contradiction?
        # A true NLI cross encoder gives P(Entailment), P(Neutral), P(Contradiction).
        # We will assume `predict_nli` returns a dict with these probabilities,
        # or we map the reranker score if it's a dedicated contradiction model.
        # For the sake of the gate, we'll interface with a predict_nli method if available,
        # otherwise fallback to a generic score mapping (low score on similar text = contradiction).

        # To detect contradiction:
        # 1. They must be discussing the same topic (dense similarity high).
        # 2. They must have opposite meaning (cross-encoder score very low, or explicit NLI contradiction high).

        # Let's assume self.reranker has an NLI mode or we use a mockable NLI function.
        pairs = [(fact.get("text", ""), candidate_claim) for fact in active_facts]

        # We will use predict_nli if the service supports it, otherwise mock it.
        if hasattr(self.reranker, "predict_nli"):
            nli_scores = self.reranker.predict_nli(pairs)
        else:
            # Fallback: pseudo-NLI from generic relevance
            relevance_scores = self.reranker.predict(pairs)
            nli_scores = [{"contradiction": 1.0 - score, "entailment": score} for score in relevance_scores]

        max_conflict_score = 0.0
        conflicting_fact = None

        for fact, scores in zip(active_facts, nli_scores, strict=False):
            contradiction_prob = scores.get("contradiction", 0.0)
            entailment_prob = scores.get("entailment", 0.0)

            # Contradiction confidence margin Δ
            margin = contradiction_prob - entailment_prob

            if margin > max_conflict_score:
                max_conflict_score = margin
                conflicting_fact = fact

        has_contradiction = max_conflict_score > self.contradiction_margin

        return {
            "has_contradiction": has_contradiction,
            "max_conflict_score": max_conflict_score,
            "conflicting_fact": conflicting_fact
        }
