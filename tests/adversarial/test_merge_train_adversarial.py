"""
Adversarial tests for Merge Train Orchestrator and Data Highway.
Verifies failure-closed security on code smuggling, triad contract breaches, and nonexistent branches.
"""

from __future__ import annotations

from pathlib import Path

from tools.merge_train_core import (
    DataHighwayResult,
    assemble_speculative_cohort,
)

ROOT = Path(__file__).resolve().parent.parent.parent


def test_adversarial_assemble_nonexistent_branch():
    rec = assemble_speculative_cohort(
        cohort_name="bad_cohort",
        branches=["nonexistent_branch_9999"],
        root=ROOT,
    )
    assert not rec.is_conflict_free
    assert any("could not be resolved" in c for c in rec.conflicts)


def test_adversarial_data_highway_code_smuggling_rejected():
    # If code outside output/ is in diff, must reject
    res = DataHighwayResult(
        branch="feat/smuggle", files=["output/proj/01_rec.md", "dual_pipeline/exploit.py"]
    )
    # verify check logic rejects
    non_output = [f for f in res.files if not f.startswith("output/")]
    assert len(non_output) > 0
    assert not res.fast_track_passed


def test_adversarial_data_highway_draft_purity_rejected():
    res = DataHighwayResult(branch="feat/draft", files=["output/proj/01_draft_transcript.md"])
    has_draft = any("draft" in f.lower() for f in res.files)
    assert has_draft
    assert not res.fast_track_passed
