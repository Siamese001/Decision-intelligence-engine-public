"""End-to-end tests for evaluation suite query batch pre-warming."""

from __future__ import annotations

from typing import Any, List

import pytest

from dual_pipeline.evals.runner_core.prewarmed_backend import (
    PrewarmedQueryBackendWrapper,
    prewarm_suite_queries,
)


class MockEvalBackend:
    """Mock backend tracking single vs batch encode calls."""

    def __init__(self) -> None:
        self.single_encode_count = 0
        self.batch_encode_count = 0
        self.last_batch: List[str] = []
        self.backend_id = "bge-m3-dev"

    def encode(self, text: str) -> dict[str, Any]:
        self.single_encode_count += 1
        return {"dense": [0.1] * 1024, "text": text}

    def encode_many(self, texts: List[str], *, batch_size: int | None = None) -> List[dict[str, Any]]:
        self.batch_encode_count += 1
        self.last_batch = list(texts)
        return [{"dense": [0.1] * 1024, "text": t} for t in texts]


def test_prewarm_suite_queries_single_batch() -> None:
    """Verify that prewarm_suite_queries encodes all unique questions in one batched call."""
    backend = MockEvalBackend()
    cases = [
        {"pipeline": "query", "question": "What is Project Alpha?"},
        {"pipeline": "query", "question": "Who approved the SOW?"},
        {"pipeline": "query", "question": "What is Project Alpha?"},  # duplicate
        {"pipeline": "ingestion", "question": "Should ingestion case question be prewarmed?"},
    ]

    wrapped = prewarm_suite_queries(backend, cases)

    # All 3 unique questions were encoded in exactly 1 batch call
    assert backend.batch_encode_count == 1
    assert len(backend.last_batch) == 3
    assert set(backend.last_batch) == {
        "What is Project Alpha?",
        "Who approved the SOW?",
        "Should ingestion case question be prewarmed?",
    }
    assert backend.single_encode_count == 0

    # Querying through wrapper hits in-memory cache without calling inner backend
    res1 = wrapped.encode("What is Project Alpha?")
    assert res1["text"] == "What is Project Alpha?"
    assert backend.single_encode_count == 0

    res2 = wrapped.encode("Who approved the SOW?")
    assert res2["text"] == "Who approved the SOW?"
    assert backend.single_encode_count == 0

    # Querying an unexpected question falls through to inner backend
    res3 = wrapped.encode("Uncached question?")
    assert res3["text"] == "Uncached question?"
    assert backend.single_encode_count == 1
