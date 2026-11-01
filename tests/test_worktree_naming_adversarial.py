"""
tests/test_worktree_naming_adversarial.py

Adversarial and boundary edge-case tests for worktree naming convention.
Covers at least 3 required categories per e2e-and-edge-case-coverage.md:
1. Boundary payload: empty input, null values, single-char tokens, empty project/date
2. Malformed input: forbidden characters, consecutive hyphens, mixed casing, prohibited model tokens
3. Overflow & exhaustion: excessive slug lengths (>100 chars), verifying graceful max_len budget clamping
4. Rollback & collision: deconfliction across same-title meetings with different dates or projects
"""
from __future__ import annotations

from pathlib import Path
import pytest

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.worktree_naming import (
    CONVENTIONAL_BRANCH_RE,
    derive_worktree_identity,
    slugify_branch_name,
)
from tools.pr_governance_core.common import slugify_branch


def test_adversarial_boundary_payload_empty_and_whitespace():
    """Category 1: Boundary payload - handles empty, None, and whitespace strings gracefully."""
    wt_dir, branch = derive_worktree_identity(
        modality="AUDIO",
        title="   ",
        project="",
        date="",
        clean_slug=None,
    )
    assert CONVENTIONAL_BRANCH_RE.match(branch)
    assert branch == "audio/session"
    assert wt_dir.name == "audio_session"


def test_adversarial_malformed_input_and_symbols():
    """Category 2: Malformed input - strips invalid characters, symbols, and prohibited tokens."""
    wt_dir, branch = derive_worktree_identity(
        modality="DOCUMENT",
        title="### $100M Architecture Blueprint @ PartnerAdvisory !!! (GPT-4 / Claude-3)",
        project="PROJECT_NEXUS???",
        date="10/15/2026",
        clean_slug="Blueprint$$$___PartnerAdvisory###",
    )
    assert CONVENTIONAL_BRANCH_RE.match(branch)
    assert "gpt" not in branch
    assert "claude" not in branch
    assert "$" not in branch
    assert "@" not in branch
    assert "#" not in branch
    assert "--" not in branch
    assert not branch.endswith("-")


def test_adversarial_overflow_and_budget_exhaustion():
    """Category 3: Overflow & exhaustion - clamps ultra-long composite slugs to budget without malforming."""
    ultra_long_title = "A" * 200
    ultra_long_project = "P" * 100
    ultra_long_attendees = "Att_" * 30

    wt_dir, branch = derive_worktree_identity(
        modality="AUDIO",
        title=ultra_long_title,
        project=ultra_long_project,
        date="10-15-2026",
        clean_slug=ultra_long_attendees,
    )
    assert CONVENTIONAL_BRANCH_RE.match(branch)
    assert not branch.endswith("-")
    assert not wt_dir.name.endswith("_")
    # Derived slug should not exceed 80 chars
    slug_part = branch.replace("audio/", "")
    assert len(slug_part) <= 80


def test_adversarial_deconfliction_same_title_different_dates():
    """Category 4: Exact regression & collision prevention - same title on different dates produces distinct branches."""
    _, branch_day1 = derive_worktree_identity(
        modality="AUDIO",
        title="LENS Daily Standup",
        project="LENS",
        date="10-15-2026",
        clean_slug="Standup_Team",
    )
    _, branch_day2 = derive_worktree_identity(
        modality="AUDIO",
        title="LENS Daily Standup",
        project="LENS",
        date="10-16-2026",
        clean_slug="Standup_Team",
    )
    assert branch_day1 != branch_day2
    assert "10-15-2026" in branch_day1
    assert "10-16-2026" in branch_day2
