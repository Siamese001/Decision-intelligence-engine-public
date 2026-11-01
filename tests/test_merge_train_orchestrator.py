"""
Unit tests for merge train orchestrator and Data Highway.
"""

from __future__ import annotations

from pathlib import Path

from tools.merge_train_core import (
    DataHighwayResult,
    SpeculativeCohortReceipt,
    assemble_speculative_cohort,
)

ROOT = Path(__file__).resolve().parent.parent


def test_data_highway_result_dataclass():
    res = DataHighwayResult(
        branch="feat/test", is_eligible=True, has_triad=True, fast_track_passed=True
    )
    assert res.is_eligible
    assert res.fast_track_passed
    assert res.branch == "feat/test"
    d = res.to_dict()
    assert d["fast_track_passed"] is True


def test_speculative_cohort_receipt_dataclass():
    rec = SpeculativeCohortReceipt(
        cohort_name="platform", branches=["b1", "b2"], base_branch="main"
    )
    assert rec.cohort_name == "platform"
    assert len(rec.branches) == 2
    d = rec.to_dict()
    assert d["cohort_name"] == "platform"


def test_assemble_speculative_cohort_empty():
    rec = assemble_speculative_cohort("empty_cohort", [], root=ROOT)
    assert not rec.is_conflict_free
    assert "No branches" in rec.conflicts[0]
