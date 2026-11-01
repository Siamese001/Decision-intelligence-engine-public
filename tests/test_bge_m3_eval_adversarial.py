"""Adversarial and boundary tests for evaluation batching and cache invalidation."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, List

import pytest

from dual_pipeline.evals.runner_core.prewarmed_backend import (
    PrewarmedQueryBackendWrapper,
    prewarm_suite_queries,
)


class MockFlakyBackend:
    """Mock backend to test fallback and empty cases."""

    def __init__(self) -> None:
        self.encode_count = 0

    def encode(self, text: str) -> dict[str, Any]:
        self.encode_count += 1
        return {"dense": [0.5] * 1024, "text": text}


def test_prewarm_suite_queries_empty_and_missing_keys() -> None:
    """Boundary test: empty cases, cases without questions or queries."""
    backend = MockFlakyBackend()

    # Empty list
    w1 = prewarm_suite_queries(backend, [])
    assert isinstance(w1, PrewarmedQueryBackendWrapper)
    assert backend.encode_count == 0

    # Cases with no question or query keys
    cases = [{"pipeline": "noop"}, {"foo": "bar"}]
    w2 = prewarm_suite_queries(backend, cases)
    assert backend.encode_count == 0

    # Encode many fallback on wrapped backend
    res = w2.encode_many(["q1", "q2"])
    assert len(res) == 2
    assert backend.encode_count == 2

    # Second call hits cache
    res_cached = w2.encode_many(["q1", "q2"])
    assert len(res_cached) == 2
    assert backend.encode_count == 2  # No increase
