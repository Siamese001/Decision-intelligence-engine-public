from typing import Any, Dict, List, Optional

import numpy as np

from dual_pipeline.index_core.backends import (
    _resolve_embedding_device,
    get_backend,
)


class ColbertMaxSimAuditor:
    """Token-level late-interaction engine for claim verification."""

    def __init__(self, backend: Optional[Any] = None, threshold: float = 0.72):
        if backend is None:
            device = _resolve_embedding_device()
            self.backend = get_backend("bge-m3-internal", allow_internal_backend=True, device=device)
        else:
            self.backend = backend
        self.threshold = threshold

    def _normalize_score(self, score: float, num_query_tokens: int) -> float:
        if num_query_tokens == 0:
            return 0.0
        return score / num_query_tokens

    def audit_claim(self, claim_text: str, transcript_chunks: List[str]) -> Dict[str, Any]:
        """Verify claim against transcript chunks using ColBERT MaxSim."""
        if not transcript_chunks:
            return {
                "claim_text": claim_text,
                "best_score": 0.0,
                "best_chunk": None,
                "grounded": False,
            }

        # Lossless batched ColBERT encoding: encode claim and all chunks in one batch
        all_texts = [claim_text] + list(transcript_chunks)
        if hasattr(self.backend, "encode_many_colbert_only"):
            encoded_all = self.backend.encode_many_colbert_only(all_texts)
            query_mat = np.array(encoded_all[0], dtype=np.float32)
            chunk_mats = [np.array(e, dtype=np.float32) for e in encoded_all[1:]]
        elif hasattr(self.backend, "encode_many"):
            encoded_all = self.backend.encode_many(all_texts)
            query_mat = np.array(encoded_all[0]["colbert"], dtype=np.float32)
            chunk_mats = [np.array(e["colbert"], dtype=np.float32) for e in encoded_all[1:]]
        else:
            encoded_claim = self.backend.encode(claim_text)
            query_mat = np.array(encoded_claim["colbert"], dtype=np.float32)
            chunk_mats = [np.array(self.backend.encode(c)["colbert"], dtype=np.float32) for c in transcript_chunks]

        num_query_tokens = len(query_mat)
        best_score = 0.0
        best_chunk = None

        for chunk, doc_mat in zip(transcript_chunks, chunk_mats):
            if doc_mat.size == 0 or query_mat.size == 0:
                continue

            # query_mat: (Q, D), doc_mat.T: (D, N) -> sim: (Q, N)
            sim = np.matmul(query_mat, doc_mat.T)
            # Max over document tokens (axis=1), then sum
            raw_score = float(np.sum(np.max(sim, axis=1)))
            norm_score = self._normalize_score(raw_score, num_query_tokens)

            if norm_score > best_score:
                best_score = norm_score
                best_chunk = chunk

        return {
            "claim_text": claim_text,
            "best_score": best_score,
            "best_chunk": best_chunk,
            "grounded": best_score >= self.threshold
        }
