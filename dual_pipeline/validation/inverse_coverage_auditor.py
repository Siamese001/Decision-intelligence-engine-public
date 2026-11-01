from typing import Any, Dict, List, Optional

from dual_pipeline.index_core.backends import (
    _resolve_embedding_device,
    get_backend,
)


class InverseCoverageAuditor:
    """
    Replaces LLM recap completeness review. Audits whether high-salience
    decisions/risks in the transcript were omitted from outputs.
    """

    STRATEGIC_CATALYSTS = {"agreed", "decided", "blocked", "risk", "owner", "approved", "rejected", "architecture", "mandatory"}

    def __init__(self, backend: Optional[Any] = None, coverage_threshold: float = 0.90):
        if backend is None:
            device = _resolve_embedding_device()
            self.backend = get_backend("bge-m3-internal", allow_internal_backend=True, device=device)
        else:
            self.backend = backend

        self.coverage_threshold = coverage_threshold

    def _is_turn_salient(self, turn_text: str) -> bool:
        """
        Uses lexical weights (or simple token matching as a proxy) to determine
        if a turn contains strategic decision catalysts.
        """
        text_lower = turn_text.lower()
        return any(catalyst in text_lower for catalyst in self.STRATEGIC_CATALYSTS)

    def _score_saliency_sparse(self, turn_text: str) -> float:
        """
        Uses BGE-M3 sparse lexical weights to score saliency.
        Returns sum of weights for catalyst tokens.
        """
        encoded = self.backend.encode(turn_text)
        sparse_dict = encoded.get("sparse", {})

        # If backend doesn't support sparse, fallback to word count of catalysts
        if not sparse_dict:
            return float(self._is_turn_salient(turn_text))

        salient_score = 0.0
        # Check tokens
        for token, weight in sparse_dict.items():
            if any(cat in token.lower() for cat in self.STRATEGIC_CATALYSTS):
                salient_score += weight
        return salient_score

    def audit_coverage(self, transcript_turns: List[Dict[str, Any]], recap_sentences: List[str]) -> Dict[str, Any]:
        """
        Executes inverse retrieval: for each strategic transcript turn, check if
        it is represented in the recap sentences via semantic similarity.
        """
        salient_turns = []
        for turn in transcript_turns:
            text = turn.get("text", "")
            # We consider it salient if it hits lexical catalysts strongly
            if self._score_saliency_sparse(text) > 0.0:
                salient_turns.append(text)

        if not salient_turns:
            # If nothing was strategic, coverage is trivially 100%
            return {"coverage_ratio": 1.0, "omitted_turns": [], "passed": True}

        # Encode all recap sentences into dense embeddings
        recap_dense = []
        for sentence in recap_sentences:
            enc = self.backend.encode(sentence)
            recap_dense.append(enc.get("dense", [0.0]*1024))

        import numpy as np
        recap_mat = np.array(recap_dense, dtype=np.float32)
        if recap_mat.size == 0:
            return {"coverage_ratio": 0.0, "omitted_turns": salient_turns, "passed": False}

        # Normalize recap matrix
        norms = np.linalg.norm(recap_mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        recap_mat = recap_mat / norms

        omitted_turns = []
        covered_count = 0

        for turn in salient_turns:
            enc_turn = self.backend.encode(turn)
            turn_dense = np.array(enc_turn.get("dense", [0.0]*1024), dtype=np.float32)
            turn_norm = np.linalg.norm(turn_dense)
            if turn_norm > 0:
                turn_dense = turn_dense / turn_norm

            # Cosine similarity against all recap sentences
            sims = np.dot(recap_mat, turn_dense)
            max_sim = np.max(sims)

            # Threshold for semantic representation (heuristic, usually 0.7-0.8 for BGE-M3)
            # Since this is a test, we assume > 0.75 means covered
            if max_sim > 0.75:
                covered_count += 1
            else:
                omitted_turns.append(turn)

        coverage_ratio = covered_count / len(salient_turns)

        return {
            "coverage_ratio": coverage_ratio,
            "omitted_turns": omitted_turns,
            "passed": coverage_ratio >= self.coverage_threshold
        }
