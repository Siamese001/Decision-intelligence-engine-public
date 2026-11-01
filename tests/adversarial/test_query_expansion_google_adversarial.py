"""Adversarial and boundary stress tests for Google Gemini Flash query expansion provider.

Covers:
1. Boundary payloads (massive input, special unicode, empty expansions).
2. Malformed input (invalid JSON, HTML, non-string items).
3. Invariant bypass attempts (identical string echoes, sub-threshold expansions).
4. Concurrency races (multithreaded query expansions).
5. Rollback and fault recovery (provider HTTP 429 and 503 errors).
"""
from __future__ import annotations

import concurrent.futures
import json
from unittest.mock import patch

from dual_pipeline.query_expansion_google import GoogleQueryExpansionProvider


def test_adversarial_boundary_payloads():
    """Category 1: Boundary payloads - massive queries and empty responses."""
    provider = GoogleQueryExpansionProvider()

    # Massive 100,000 char query
    massive_query = "What is the architecture " + ("and details " * 10000)
    simulated_resp = json.dumps({"expansions": ["Architecture and details overview"]})

    with patch("dual_pipeline.query_expansion_google.call_gemini_generate_content", return_value=simulated_resp):
        res = provider.expand_query(massive_query)
        assert isinstance(res, list)

    # Empty expansions list
    with patch("dual_pipeline.query_expansion_google.call_gemini_generate_content", return_value='{"expansions": []}'):
        res = provider.expand_query("Standard query")
        assert res == []


def test_adversarial_malformed_and_injection_input():
    """Category 2: Malformed inputs - non-JSON text, nested unexpected types."""
    provider = GoogleQueryExpansionProvider()

    # Non-JSON HTML response
    with patch("dual_pipeline.query_expansion_google.call_gemini_generate_content", return_value="<html>502 Bad Gateway</html>"):
        res = provider.expand_query("Sample query")
        assert res == []

    # Non-string elements in expansions
    corrupted_resp = json.dumps({"expansions": [123, {"bad": "dict"}, None, "Valid query expansion"]})
    with patch("dual_pipeline.query_expansion_google.call_gemini_generate_content", return_value=corrupted_resp):
        res = provider.expand_query("Sample query")
        assert len(res) <= 1


def test_adversarial_invariant_bypass_attempts():
    """Category 3: Invariant bypass attempts - echo bypass and below-threshold injection."""
    provider = GoogleQueryExpansionProvider(similarity_threshold=0.85)

    # Attempt to bypass by echoing exact original query (must be filtered out)
    echo_resp = json.dumps({
        "expansions": [
            "Exact Original Query",  # Echo
            "exact original query",  # Case-insensitive echo
            "completely orthogonal unrelated topic",  # Sub-threshold
        ]
    })
    with patch("dual_pipeline.query_expansion_google.call_gemini_generate_content", return_value=echo_resp):
        res = provider.expand_query("Exact Original Query")
        assert "Exact Original Query" not in res
        assert "exact original query" not in res
        assert "completely orthogonal unrelated topic" not in res
        assert res == []


def test_adversarial_concurrency_race():
    """Category 4: Concurrency races - multi-threaded simultaneous query expansions."""
    provider = GoogleQueryExpansionProvider(similarity_threshold=0.70)
    sample_response = json.dumps({
        "expansions": [
            "Concurrent query expansion 1",
            "Concurrent query expansion 2",
        ]
    })

    def run_worker(idx: int):
        with patch("dual_pipeline.query_expansion_google.call_gemini_generate_content", return_value=sample_response):
            return provider.expand_query(f"Concurrent query {idx}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(run_worker, i) for i in range(16)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 16
    for r in results:
        assert isinstance(r, list)


def test_adversarial_provider_fault_recovery():
    """Category 5: Rollback and fault recovery under simulated 429 and 503 provider errors."""
    provider = GoogleQueryExpansionProvider()

    # HTTP 429 Rate Limit
    with patch(
        "dual_pipeline.query_expansion_google.call_gemini_generate_content",
        side_effect=RuntimeError("HTTP 429: Rate limit exceeded"),
    ):
        res = provider.expand_query("Rate limited query")
        assert res == []

    # HTTP 503 Service Unavailable
    with patch(
        "dual_pipeline.query_expansion_google.call_gemini_generate_content",
        side_effect=RuntimeError("HTTP 503: Service Unavailable"),
    ):
        res = provider.expand_query("Unavailable service query")
        assert res == []
