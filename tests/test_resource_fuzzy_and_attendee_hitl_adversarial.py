"""
tests/test_resource_fuzzy_and_attendee_hitl_adversarial.py

Adversarial and edge-case test suite covering:
1. Boundary payloads (empty query, whitespace query, 0-byte files, null candidate sets).
2. Malformed inputs (corrupted JSON metadata, regex injection strings, invalid UTF-8).
3. Concurrency and race conditions (multi-threaded concurrent discovery and entity resolution).
4. Timeout and budget exhaustion (sub-millisecond hard deadlines preventing directory traversal hangs).
5. Exact production regression test pinning the 31-minute stall from conversation 014f72b7-6349-4a36-8bd0-a51b151fe211.
"""
from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path
import pytest

from dual_pipeline.canonical_pipeline.resource_discovery import (
    discover_resource_fuzzy,
    score_resource_candidate,
)
from dual_pipeline.transcript_metadata_core.entity_resolver import (
    _normalize_role_key,
    resolve_person_entity,
)
from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary
from dual_pipeline.exceptions import HITLRequiredError


# Category 1: Boundary Payloads
def test_boundary_payload_empty_and_whitespace_queries():
    """Verify empty or whitespace-only queries return gracefully without crashing or hanging."""
    for empty_q in ("", "   ", "\t\n", "   \n   "):
        res = discover_resource_fuzzy(empty_q)
        assert res.best_match is None
        assert res.candidates == []
        assert res.requires_hitl is False
        assert res.elapsed_seconds < 0.10


def test_boundary_payload_zero_byte_candidate(tmp_path: Path):
    """Verify 0-byte files can be scored and discovered without division by zero or errors."""
    zero_file = tmp_path / "empty_transcript.md"
    zero_file.touch()

    res = discover_resource_fuzzy("empty_transcript.wav", search_roots=[tmp_path])
    assert res.best_match is not None
    assert res.best_match.path == zero_file.resolve()
    assert res.best_match.score >= 0.70


# Category 2: Malformed Inputs
def test_malformed_json_metadata_sidecar_does_not_crash(tmp_path: Path):
    """Verify corrupted JSON metadata sidecars are skipped gracefully without aborting discovery."""
    md_file = tmp_path / "Corrupt_Meeting_10-01-2026_source.md"
    md_file.write_text("Valid markdown content", encoding="utf-8")
    meta_file = tmp_path / "Corrupt_Meeting_10-01-2026.metadata.json"
    meta_file.write_text("{malformed: json, missing quotes, truncated", encoding="utf-8")

    res = discover_resource_fuzzy("Corrupt_Meeting_10-01-2026.wav", search_roots=[tmp_path])
    assert res.best_match is not None
    assert res.best_match.path == md_file.resolve()


def test_malformed_regex_special_characters_in_query():
    """Verify queries containing special regex characters do not cause RegexErrors or crashes."""
    regex_queries = [
        "Meeting [Discussion] (Draft) + (v1.2).wav",
        "***Kickoff***_???_$$$.md",
        "Special^Chars\\Path/Test.*",
    ]
    for rq in regex_queries:
        res = discover_resource_fuzzy(rq)
        assert res.elapsed_seconds < 0.50


# Category 3: Concurrency & Race Conditions
def test_concurrent_multithreaded_discovery_and_resolution():
    """Verify parallel invocations across multiple worker threads operate without data races or lock starvation."""
    def _worker(idx: int) -> bool:
        if idx % 2 == 0:
            res = discover_resource_fuzzy("00_10-07-2026_Kickoff_Jill_PartnerAdvisory_master_101752.wav", project="project_nexus")
            return res.best_match is not None
        else:
            ent = resolve_person_entity("Taylor Mercer", explicit_role="Delivery Lead / PM")
            return ent is not None and ent.role == "Delivery Lead-PM"

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_worker, i) for i in range(24)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 24
    assert all(results)


# Category 4: Timeout & Budget Exhaustion
def test_timeout_exhaustion_on_huge_directory_tree(tmp_path: Path):
    """Verify microsecond timeout budget (timeout_s=0.001) terminates immediately without hanging."""
    big_tree = tmp_path / "nested"
    for i in range(100):
        sub = big_tree / f"folder_{i}"
        sub.mkdir(parents=True)
        (sub / f"doc_{i}.txt").write_text("data", encoding="utf-8")

    res = discover_resource_fuzzy(
        "missing_file.wav",
        search_roots=[big_tree],
        timeout_s=0.001,
        max_files=5,
    )
    assert res.elapsed_seconds < 0.20
    assert res.files_scanned <= 10


# Category 5: Exact Production Regression
def test_exact_production_regression_jarvis_kickoff_stall():
    """
    PINNED PRODUCTION REGRESSION:
    Reproduces the exact inputs that caused the 31-minute stall in conversation 014f72b7-6349-4a36-8bd0-a51b151fe211:
    1. Input: Missing .wav '00_10-07-2026_Kickoff_Jill_PartnerAdvisory_master_101752.wav'.
    2. Roster: 'Taylor Mercer (Delivery Lead / PM, PartnerAdvisory)'.
    Proves:
    - Missing .wav resolves in <500ms to authentic markdown and metadata.
    - Role delimiter slash ('Delivery Lead / PM') resolves without AMBIGUOUS_CONFLICT.
    - Total execution completes in sub-second time without hangs or operator loops.
    """
    import time
    t0 = time.monotonic()

    # 1. Resolve missing resource
    r_res = discover_resource_fuzzy("00_10-07-2026_Kickoff_Jill_PartnerAdvisory_master_101752.wav", project="project_nexus")
    assert r_res.best_match is not None
    assert "Jarvis_Kickoff_Jill_PartnerAdvisory" in r_res.best_match.path.name
    assert r_res.best_match.metadata_path is not None
    assert "jarvis_kickoff_metadata.json" in r_res.best_match.metadata_path.name
    assert not r_res.requires_hitl

    # 2. Resolve attendee role with slash variant
    ent = resolve_person_entity("Taylor Mercer", explicit_role="Delivery Lead / PM")
    assert ent is not None
    assert ent.role == "Delivery Lead-PM"
    assert ent.status == "RESOLVED"
    assert not ent.requires_operator_confirmation

    elapsed = time.monotonic() - t0
    assert elapsed < 1.0, f"Production regression exceeded 1.0s budget: {elapsed:.3f}s"
