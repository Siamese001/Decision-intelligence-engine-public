"""Adversarial and boundary stress tests for Stage L1 Google metadata adapter.

Covers:
1. Boundary payloads (empty, 200k char transcript, empty JSON).
2. Malformed input (code-fenced JSON, corrupted bytes, array returned instead of dict).
3. Invariant bypass attempts (SQL injection dates, path traversal project codes).
4. Concurrency races (multi-threaded invocation).
5. Rollback and fault recovery (provider 429, 503 HTTP exceptions).
"""
from __future__ import annotations

import concurrent.futures
import json
from unittest.mock import patch

from dual_pipeline.l1_google_adapter import (
    L1GoogleMetadataAdapter,
    validate_l1_header_metadata,
)


def test_adversarial_boundary_payloads():
    """Category 1: Boundary payloads - massive input truncation and empty transcript."""
    adapter = L1GoogleMetadataAdapter()

    # Empty transcript
    with patch("dual_pipeline.l1_google_adapter.call_gemini_generate_content", return_value="{}"):
        res = adapter.extract_header_metadata("")
        assert res == {}

    # Massive 200,000 char transcript - adapter should slice to 4000 characters without memory spike
    massive_transcript = "A" * 200000
    with patch("dual_pipeline.l1_google_adapter.call_gemini_generate_content") as mock_call:
        mock_call.return_value = json.dumps({"meeting_name": "Massive", "date": "09/25/2026", "time": "10:00 AM"})
        res = adapter.extract_header_metadata(massive_transcript)
        assert res["meeting_name"] == "Massive"
        # Verify passed snippet was bounded
        called_args = mock_call.call_args[1]
        assert len(called_args["user_content"]) < 5000


def test_adversarial_malformed_and_markdown_fenced_json():
    """Category 2: Malformed input - markdown code-blocks, array returned instead of dict."""
    adapter = L1GoogleMetadataAdapter()

    # Markdown fenced json with preamble
    fenced_output = (
        "Here is the extracted metadata:\n"
        "```json\n"
        "{\n"
        '  "meeting_name": "Fenced Meeting",\n'
        '  "date": "09/25/2026",\n'
        '  "time": "10:00 AM",\n'
        '  "project_name": "project_core"\n'
        "}\n"
        "```\n"
        "Hope this helps!"
    )
    with patch("dual_pipeline.l1_google_adapter.call_gemini_generate_content", return_value=fenced_output):
        res = adapter.extract_header_metadata("Some snippet")
        assert res["meeting_name"] == "Fenced Meeting"
        assert res["date"] == "09/25/2026"

    # Array returned instead of dict
    with patch("dual_pipeline.l1_google_adapter.call_gemini_generate_content", return_value='["item1", "item2"]'):
        res = adapter.extract_header_metadata("Some snippet")
        assert res == {}

    # Total garbage
    with patch("dual_pipeline.l1_google_adapter.call_gemini_generate_content", return_value="<!DOCTYPE html>"):
        res = adapter.extract_header_metadata("Some snippet")
        assert res == {}


def test_adversarial_invariant_bypass_attempts():
    """Category 3: Invariant bypass attempts - injection attacks and path traversal."""
    # Date injection attempt
    malicious_date = {
        "meeting_name": "Injection Test",
        "date": "09/25/2026'; DROP TABLE nodes; --",
        "project_name": "project_nexus",
    }
    is_valid, errors = validate_l1_header_metadata(malicious_date)
    assert is_valid is False
    assert any("Invalid date format" in e for e in errors)

    # Path traversal project name attempt
    traversal_proj = {
        "meeting_name": "Traversal Test",
        "date": "09/25/2026",
        "project_name": "../../etc/shadow",
    }
    is_valid, errors = validate_l1_header_metadata(traversal_proj)
    assert is_valid is False
    assert any("Invalid project_name format" in e for e in errors)


def test_adversarial_concurrency_race():
    """Category 4: Concurrency races - multi-threaded simultaneous extractions."""
    adapter = L1GoogleMetadataAdapter()
    sample_response = json.dumps({
        "meeting_name": "Concurrent Meeting",
        "date": "09/25/2026",
        "time": "10:00 AM",
        "project_name": "project_nexus",
    })

    def run_worker(idx: int):
        with patch("dual_pipeline.l1_google_adapter.call_gemini_generate_content", return_value=sample_response):
            return adapter.extract_header_metadata(f"Transcript from thread {idx}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(run_worker, i) for i in range(20)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 20
    for r in results:
        assert r["meeting_name"] == "Concurrent Meeting"
        assert r["date"] == "09/25/2026"


def test_adversarial_provider_fault_recovery():
    """Category 5: Rollback and fault recovery under simulated 429 and 503 provider errors."""
    adapter = L1GoogleMetadataAdapter()

    # Provider HTTP 429 Rate Limit
    with patch(
        "dual_pipeline.l1_google_adapter.call_gemini_generate_content",
        side_effect=RuntimeError("HTTP 429: RESOURCE_EXHAUSTED: Quota exceeded"),
    ):
        res = adapter.extract_header_metadata("Rate limited snippet")
        assert res == {}

    # Provider HTTP 503 Service Unavailable
    with patch(
        "dual_pipeline.l1_google_adapter.call_gemini_generate_content",
        side_effect=RuntimeError("HTTP 503: The service is temporarily unavailable"),
    ):
        res = adapter.extract_header_metadata("Unavailable snippet")
        assert res == {}
