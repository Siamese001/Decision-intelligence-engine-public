"""
tests/test_pr_latency_benchmark_adversarial.py

Adversarial and boundary test cases for Wave 1 PR promotion latency and diagnostics.
Covers boundary payloads, malformed inputs, failure propagation, and recovery.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from tools.pr_governance_core.diagnostics import (
    compute_bounded_error_tail,
    extract_failure_ids,
    format_failed_stage_diagnostics,
)
from tools.pr_governance_core.latency import PromotionTimeline


def test_boundary_empty_timeline_and_zero_phases() -> None:
    with tempfile.TemporaryDirectory(prefix="adv_empty_") as temp_dir:
        timeline = PromotionTimeline(branch="feat/empty", repo_root=Path(temp_dir))
        data = timeline.finish(terminal_outcome="PASS")

        assert data["wall_clock_seconds"] >= 0.0
        assert data["accounted_phase_seconds"] == 0.0
        assert len(data["phases"]) == 0
        assert data["phase_breakdown"] == {}
        assert data["tagged_intervals"]["human_wait"] == 0.0


def test_boundary_empty_and_oversized_stage_output() -> None:
    # 1. Empty output
    ids = extract_failure_ids(1, "Test Stage", "")
    assert len(ids) >= 1
    assert "STAGE_1_TEST_STAGE" in ids[0]

    tail = compute_bounded_error_tail("")
    assert tail == ""

    # 2. Oversized output (>100,000 chars)
    giant_output = "Line info\n" * 5000 + "CRITICAL_ERROR: memory exhausted\n"
    tail_giant = compute_bounded_error_tail(giant_output, max_lines=5, max_chars=100)
    assert len(tail_giant) <= 103  # with ellipsis
    assert "CRITICAL_ERROR" in tail_giant


def test_failure_propagation_in_phase_context_manager() -> None:
    with tempfile.TemporaryDirectory(prefix="adv_fail_") as temp_dir:
        timeline = PromotionTimeline(branch="feat/fail-test", repo_root=Path(temp_dir))

        with pytest.raises(RuntimeError, match="Simulated gate explosion"):
            with timeline.phase("readiness", scope="test"):
                raise RuntimeError("Simulated gate explosion")

        data = timeline.finish(terminal_outcome="FAIL")
        assert len(data["phases"]) == 1
        assert data["phases"][0]["phase"] == "readiness"
        assert data["phases"][0]["status"] == "FAIL"
        assert data["terminal_outcome"] == "FAIL"


def test_malformed_category_and_special_character_sanitization() -> None:
    with tempfile.TemporaryDirectory(prefix="adv_special_") as temp_dir:
        repo_root = Path(temp_dir)

        # Tag with unknown category - should gracefully default to repairs
        timeline = PromotionTimeline(branch="feat/weird", repo_root=repo_root)
        timeline.tag_interval("unknown_alien_category", 4.2)
        data = timeline.finish(terminal_outcome="PASS")
        assert data["tagged_intervals"]["repairs"] == 4.2

        # Malformed stage name with weird characters
        weird_name = "Stage / With: Illegal * Path <Chars> ?"
        diag = format_failed_stage_diagnostics(
            stage_idx=99,
            stage_name=weird_name,
            cmd=["test"],
            output="FAILED tests/adversarial_test.py::test_crash",
            repo_root=repo_root,
        )
        assert (repo_root / diag["full_log_path"]).exists()
        assert len(diag["log_digest"]) == 64
        assert any("test_crash" in fid for fid in diag["failure_ids"])


def test_adversarial_gate_manifest_fallback_and_malformed_cache() -> None:
    from tools.readiness_planner import load_gate_manifest
    from tools.readiness_core import _load_cached_receipt_for_sha
    import json

    with tempfile.TemporaryDirectory(prefix="adv_manifest_") as temp_dir:
        temp_path = Path(temp_dir)

        # 1. Non-existent manifest falls back to 32 stages
        non_existent = temp_path / "missing_manifest.json"
        manifest = load_gate_manifest(manifest_path=non_existent)
        assert manifest["total_registered_stages"] == 32
        assert len(manifest["lane_profiles"]["2"]["required_stage_indices"]) == 32

        # 2. Corrupted JSON manifest falls back to 32 stages
        corrupted = temp_path / "corrupted_manifest.json"
        corrupted.write_text("{ not valid json !!!", encoding="utf-8")
        manifest2 = load_gate_manifest(manifest_path=corrupted)
        assert manifest2["total_registered_stages"] == 32

        # 3. Malformed cached receipt returns None
        receipt_dir = temp_path / "reports" / "readiness"
        receipt_dir.mkdir(parents=True, exist_ok=True)
        r_file = receipt_dir / "local_main_readiness_test_receipt.json"

        # Case 3a: Non-JSON content
        r_file.write_text("corrupted", encoding="utf-8")
        assert _load_cached_receipt_for_sha("abcd1234ef", r_file) is None

        # Case 3b: Wrong SHA
        r_file.write_text(json.dumps({"head_commit_sha": "different_sha", "terminal_outcome": "PASS", "stages": [{"status": "PASS"}] * 32}), encoding="utf-8")
        assert _load_cached_receipt_for_sha("abcd1234ef", r_file) is None

        # Case 3c: Failing terminal outcome
        r_file.write_text(json.dumps({"head_commit_sha": "abcd1234ef", "terminal_outcome": "FAIL", "stages": [{"status": "PASS"}] * 32}), encoding="utf-8")
        assert _load_cached_receipt_for_sha("abcd1234ef", r_file) is None

        # Case 3d: Insufficient stages (< 6)
        r_file.write_text(json.dumps({"head_commit_sha": "abcd1234ef", "terminal_outcome": "PASS", "stages": [{"status": "PASS"}] * 5}), encoding="utf-8")
        assert _load_cached_receipt_for_sha("abcd1234ef", r_file) is None

        # Case 3e: Valid Lane 0 receipt (>= 6 stages with matching stages_evaluated)
        r_file.write_text(json.dumps({"head_commit_sha": "abcd1234ef", "terminal_outcome": "PASS", "stages_evaluated": 6, "stages": [{"status": "PASS"}] * 6}), encoding="utf-8")
        cached = _load_cached_receipt_for_sha("abcd1234ef", r_file)
        assert cached is not None
        assert cached["terminal_outcome"] == "PASS"
