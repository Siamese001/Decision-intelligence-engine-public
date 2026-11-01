"""tests/unit/test_council_action_hardening_alignment.py

Unit tests for Wave 2: Unify Executive Disposition & Action Derivation in Council.
"""
from __future__ import annotations

from tools.pr_governance_core.council import deliberate_branch, harden_action_item
from tools.pr_governance_core.executive_render import format_executive_disposition


def test_superseded_code_dirty_emits_archive() -> None:
    item = {
        "branch": "fix/ingestion-live-e2e-certification",
        "ahead": 5,
        "behind": 10,
        "dirty": True,
        "dirty_files_count": 41,
        "clean_merge": False,
        "diff_category": "SUPERSEDED_CODE",
        "changed_files": ["dual_pipeline/cli/ingest.py"],
    }
    disp, action = format_executive_disposition(item)
    assert disp == "Decommission (No Value)"
    assert "tag archive" in action
    assert "superseded by main" in action
    assert "Rebase onto main" not in action


def test_superseded_code_clean_emits_prune() -> None:
    item = {
        "branch": "fix/old-certification",
        "ahead": 5,
        "behind": 10,
        "dirty": False,
        "clean_merge": False,
        "diff_category": "SUPERSEDED_CODE",
        "changed_files": ["dual_pipeline/cli/ingest.py"],
    }
    disp, action = format_executive_disposition(item)
    assert disp == "Decommission (No Value)"
    assert "superseded by main" in action
    assert "Rebase onto main" not in action


def test_dirty_and_conflicts_not_superseded_emits_commit_and_rebase() -> None:
    item = {
        "branch": "feat/active-work",
        "ahead": 2,
        "behind": 3,
        "dirty": True,
        "dirty_files_count": 2,
        "clean_merge": False,
        "rebase_effort": "LOW",
        "diff_category": "CODE_ACTIVE",
        "changed_files": ["dual_pipeline/cli/ingest.py"],
    }
    disp, action = format_executive_disposition(item)
    assert disp == "Complete In-Flight Work"
    assert "Commit 2 authored file(s)" in action
    assert "before rebasing onto main" in action


def test_deliberate_branch_superseded_consistency() -> None:
    item = {
        "branch": "fix/ingestion-live-e2e-certification",
        "ahead": 5,
        "behind": 10,
        "dirty": True,
        "dirty_files_count": 41,
        "clean_merge": False,
        "diff_category": "SUPERSEDED_CODE",
        "changed_files": ["dual_pipeline/cli/ingest.py"],
        "tier": "P1",
    }
    assessment = deliberate_branch(item)
    assert assessment.disposition == "Decommission (No Value)"
    assert "tag archive" in assessment.hardened_action or "archive" in assessment.hardened_action
    assert "Rebase onto main" not in assessment.hardened_action
