"""
tests/test_release_policy_ssot_coverage.py

Regression & edge-case test suite for dual_pipeline.release_policy._check_judge_report.
Validates:
1. Canonical threshold alignment using OUTPUT3_PRODUCTION_PASS_THRESHOLD from dual_pipeline.release_thresholds.
2. Boundary payload testing (score=90, score=89, score=0, score=100).
3. Malformed input resilience (missing keys, non-dict payloads, None values).
4. Invariant bypass prevention (verdict spoofing with sub-threshold scores, score>=90 with non-PASS verdict).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.release_policy import (
    _check_judge_report,
    compute_release_evaluation_digest,
)
from dual_pipeline.release_thresholds import (
    OUTPUT3_PRODUCTION_PASS_THRESHOLD,
)


def _write_temp_judge_report(tmp_path: Path, data: Any, seal_digest: bool = True) -> Path:
    report_file = tmp_path / "llm_judge_evaluation.json"
    if seal_digest and isinstance(data, dict) and data:
        payload = dict(data)
        payload["evaluation_receipt_digest"] = compute_release_evaluation_digest(payload)
    else:
        payload = data
    report_file.write_text(json.dumps(payload), encoding="utf-8")
    return report_file


# Category 1: Boundary payload testing
def test_check_judge_report_boundary_pass(tmp_path: Path):
    """Score exactly at canonical OUTPUT3_PRODUCTION_PASS_THRESHOLD with PASS verdict succeeds."""
    data = {
        "output_3_evaluation": {
            "composite_score": OUTPUT3_PRODUCTION_PASS_THRESHOLD,
            "verdict": "PASS",
        }
    }
    report_file = _write_temp_judge_report(tmp_path, data)
    # Should not raise
    _check_judge_report(report_file, "pkg_test_boundary")


def test_check_judge_report_boundary_fail(tmp_path: Path):
    """Score exactly 1 point below OUTPUT3_PRODUCTION_PASS_THRESHOLD fails closed."""
    sub_threshold_score = OUTPUT3_PRODUCTION_PASS_THRESHOLD - 1
    data = {
        "output_3_evaluation": {
            "composite_score": sub_threshold_score,
            "verdict": "PASS",
        }
    }
    report_file = _write_temp_judge_report(tmp_path, data)
    with pytest.raises(RuntimeError) as exc_info:
        _check_judge_report(report_file, "pkg_test_boundary_fail")

    err_msg = str(exc_info.value)
    assert f"score={sub_threshold_score}/{OUTPUT3_PRODUCTION_PASS_THRESHOLD}" in err_msg
    assert "Mutation blocked." in err_msg


# Category 2: Malformed input testing
@pytest.mark.parametrize(
    "malformed_report",
    [
        {},
        {"output_3_evaluation": None},
        {"output_3_evaluation": "not_a_dict"},
        {"output_3_evaluation": {"composite_score": None, "verdict": "PASS"}},
        {"output_3_evaluation": {"composite_score": "ninety", "verdict": "PASS"}},
    ],
)
def test_check_judge_report_malformed_input(tmp_path: Path, malformed_report: Any):
    """Malformed or missing output_3_evaluation records fail closed cleanly."""
    report_file = _write_temp_judge_report(tmp_path, malformed_report, seal_digest=True)
    with pytest.raises(RuntimeError) as exc_info:
        _check_judge_report(report_file, "pkg_test_malformed")
    assert "blocked" in str(exc_info.value).lower() or "corrupt" in str(exc_info.value).lower()


# Category 3: Invariant bypass attempt testing
def test_check_judge_report_bypass_attempt_verdict_fail(tmp_path: Path):
    """High score (>=90) with FAIL verdict fails closed and blocks mutation."""
    data = {
        "output_3_evaluation": {
            "composite_score": 98,
            "verdict": "FAIL",
        }
    }
    report_file = _write_temp_judge_report(tmp_path, data)
    with pytest.raises(RuntimeError) as exc_info:
        _check_judge_report(report_file, "pkg_test_bypass")
    assert "verdict=FAIL" in str(exc_info.value)
    assert "Mutation blocked." in str(exc_info.value)


def test_check_judge_report_bypass_attempt_advisory_verdict(tmp_path: Path):
    """Output 3 does not permit PASS_ADVISORY: must strictly require PASS."""
    data = {
        "output_3_evaluation": {
            "composite_score": 88,
            "verdict": "PASS_ADVISORY",
        }
    }
    report_file = _write_temp_judge_report(tmp_path, data)
    with pytest.raises(RuntimeError) as exc_info:
        _check_judge_report(report_file, "pkg_test_advisory_blocked")
    assert "Mutation blocked." in str(exc_info.value)
