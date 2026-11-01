import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("dual_pipeline.retrieval.bge_reranker_service")

_RERANKER_CACHE_LOCK = threading.RLock()
_RERANKER_MODEL_CACHE: Dict[Tuple[str, bool], Any] = {}
_PAIR_SCORE_CACHE: Dict[Tuple[str, str, str], float] = {}
_MAX_PAIR_CACHE_ENTRIES = 10000
_DEFAULT_SERVICE_INSTANCE: Optional["BgeRerankerService"] = None


class BgeRerankerService:
    """High-performance cross-encoder runtime service with batching and memoization."""

    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3", use_fp16: bool = False):
        self.model_name = model_name
        self.use_fp16 = use_fp16
        self._reranker: Optional[Any] = None

    def _get_reranker(self) -> Any:
        if self._reranker is not None:
            return self._reranker

        cache_key = (self.model_name, self.use_fp16)
        with _RERANKER_CACHE_LOCK:
            if cache_key in _RERANKER_MODEL_CACHE:
                self._reranker = _RERANKER_MODEL_CACHE[cache_key]
                return self._reranker

            try:
                from FlagEmbedding import FlagReranker
                reranker_instance = FlagReranker(self.model_name, use_fp16=self.use_fp16)
                _RERANKER_MODEL_CACHE[cache_key] = reranker_instance
                self._reranker = reranker_instance
            except Exception as e:
                raise RuntimeError(
                    f"RERANKER_UNAVAILABLE: Failed to initialize FlagReranker('{self.model_name}'): {e}"
                ) from e
        return self._reranker

    def _store_and_evict_pair_cache(
        self,
        missing_indices: List[int],
        missing_pairs: List[Tuple[str, str]],
        computed: List[float],
        results: List[Optional[float]],
    ) -> None:
        with _RERANKER_CACHE_LOCK:
            for missing_idx, pair, score in zip(missing_indices, missing_pairs, computed):
                results[missing_idx] = score
                cache_key = (self.model_name, pair[0], pair[1])
                if cache_key in _PAIR_SCORE_CACHE:
                    del _PAIR_SCORE_CACHE[cache_key]
                _PAIR_SCORE_CACHE[cache_key] = score
            overflow = len(_PAIR_SCORE_CACHE) - _MAX_PAIR_CACHE_ENTRIES
            if overflow > 0:
                for stale_key in list(_PAIR_SCORE_CACHE.keys())[:overflow]:
                    del _PAIR_SCORE_CACHE[stale_key]

    def predict(self, sentence_pairs: List[Tuple[str, str]]) -> List[float]:
        """
        Takes a list of (sentence_A, sentence_B) and returns cross-encoder scores.
        Scores range between 0.0 and 1.0 (sigmoid normalized).
        Caches predictions by (model_name, sentence_A, sentence_B).
        """
        if not sentence_pairs:
            return []

        results: List[Optional[float]] = [None] * len(sentence_pairs)
        missing_indices: List[int] = []
        missing_pairs: List[Tuple[str, str]] = []

        with _RERANKER_CACHE_LOCK:
            for idx, pair in enumerate(sentence_pairs):
                cache_key = (self.model_name, pair[0], pair[1])
                if cache_key in _PAIR_SCORE_CACHE:
                    cached_score = _PAIR_SCORE_CACHE[cache_key]
                    del _PAIR_SCORE_CACHE[cache_key]
                    _PAIR_SCORE_CACHE[cache_key] = cached_score
                    results[idx] = cached_score
                else:
                    missing_indices.append(idx)
                    missing_pairs.append(pair)

        if missing_pairs:
            reranker = self._get_reranker()
            raw_scores = reranker.compute_score(missing_pairs, normalize=True)
            computed = [float(raw_scores)] if isinstance(raw_scores, (int, float)) else [float(s) for s in raw_scores]
            self._store_and_evict_pair_cache(missing_indices, missing_pairs, computed, results)

        return [r if r is not None else 0.0 for r in results]

    @classmethod
    def compute_rerank_score(cls, query: str, doc: str) -> float:
        global _DEFAULT_SERVICE_INSTANCE
        with _RERANKER_CACHE_LOCK:
            if _DEFAULT_SERVICE_INSTANCE is None or type(_DEFAULT_SERVICE_INSTANCE) is not cls:
                _DEFAULT_SERVICE_INSTANCE = cls()
            svc = _DEFAULT_SERVICE_INSTANCE
        scores = svc.predict([(query, doc)])
        return scores[0] if scores else 0.0


BGERerankerService = BgeRerankerService
