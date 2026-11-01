"""Prewarmed query embedding cache wrapper for evaluation suite execution."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence


class PrewarmedQueryBackendWrapper:
    """Wraps an EmbeddingBackend to serve pre-encoded queries from memory."""

    def __init__(self, inner_backend: Any, prewarmed_cache: Dict[str, Any]):
        self._inner = inner_backend
        self._cache = prewarmed_cache

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def encode(self, text: str) -> Dict[str, Any]:
        if text in self._cache:
            return self._cache[text]
        res = self._inner.encode(text)
        self._cache[text] = res
        return res

    def encode_many(
        self, texts: Iterable[str], *, batch_size: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        text_list = list(texts)
        if not text_list:
            return []

        results: List[Optional[Dict[str, Any]]] = [None] * len(text_list)
        missing_indices: List[int] = []
        missing_texts: List[str] = []

        for idx, t in enumerate(text_list):
            if t in self._cache:
                results[idx] = self._cache[t]
            else:
                missing_indices.append(idx)
                missing_texts.append(t)

        if missing_texts:
            if hasattr(self._inner, "encode_many"):
                computed = self._inner.encode_many(missing_texts, batch_size=batch_size)
            else:
                computed = [self._inner.encode(t) for t in missing_texts]

            for m_idx, text, enc in zip(missing_indices, missing_texts, computed):
                self._cache[text] = enc
                results[m_idx] = enc

        return [r for r in results if r is not None]


def prewarm_suite_queries(backend: Any, cases: Sequence[Dict[str, Any]]) -> PrewarmedQueryBackendWrapper:
    """Extracts all query questions from eval cases and batch-encodes them in one call."""
    unique_questions: List[str] = []
    seen = set()
    for case in cases:
        q = case.get("question") or case.get("query")
        if q and q not in seen:
            seen.add(q)
            unique_questions.append(q)

    cache: Dict[str, Any] = {}
    if unique_questions:
        if hasattr(backend, "encode_many"):
            encoded = backend.encode_many(unique_questions)
        else:
            encoded = [backend.encode(q) for q in unique_questions]
        for q, enc in zip(unique_questions, encoded):
            cache[q] = enc

    return PrewarmedQueryBackendWrapper(backend, cache)
