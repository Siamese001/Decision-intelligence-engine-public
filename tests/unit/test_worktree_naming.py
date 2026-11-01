"""
tests/unit/test_worktree_naming.py

Unit tests for modality-aware worktree and branch identity derivation.
Verifies compliance with INV-WT-03, INV-WT-04, and AGENTS.md Invariant 7.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
import pytest

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.worktree_naming import (
    CONVENTIONAL_BRANCH_RE,
    derive_worktree_identity,
    slugify_branch_name,
)
from tools.pr_governance_core.common import slugify_branch


def test_slugify_branch_name_basic():
    slug = slugify_branch_name("Product Owner Role Description PartnerAdvisory")
    assert slug == "product-owner-role-description-partner_advisory"
    assert len(slug) <= 40
    assert not slug.endswith("-")


def test_slugify_branch_name_sanitizes_underscores_and_symbols():
    slug = slugify_branch_name("PRODUCT_OWNER_ROLE__DESCRIPTION_2026_10_12!@#")
    assert "_" not in slug
    assert "--" not in slug
    assert slug.startswith("product-owner-role-description")
    assert len(slug) <= 40


def test_slugify_branch_name_filters_prohibited_model_tokens():
    slug = slugify_branch_name("GPT-4 Analysis of Claude and Gemini Architecture")
    assert "gpt" not in slug
    assert "claude" not in slug
    assert "gemini" not in slug
    assert slug == "analysis-of-and-architecture"


def test_slugify_branch_name_fallback_on_empty():
    assert slugify_branch_name("") == "session"
    assert slugify_branch_name("   ") == "session"
    assert slugify_branch_name("---___---") == "session"


def test_derive_worktree_identity_document():
    root = Path("/fake/repo/root")
    wt_dir, branch = derive_worktree_identity(
        modality=CanonicalModality.DOCUMENT,
        title="Product Owner Role Description PartnerAdvisory",
        repo_root=root,
    )
    assert branch == "ingest/doc-product-owner-role-description-partner_advisory"
    assert wt_dir == root / ".worktrees" / "ingest_doc_product_owner_role_description_partner_advisory"
    assert CONVENTIONAL_BRANCH_RE.match(branch)
    assert slugify_branch(branch) == "ingest_doc_product_owner_role_description_partner_advisory"


def test_derive_worktree_identity_document_from_string():
    root = Path("/fake/repo/root")
    wt_dir, branch = derive_worktree_identity(
        modality="DOCUMENT",
        title="Architecture Blueprint V2",
        repo_root=root,
    )
    assert branch == "ingest/doc-architecture-blueprint-v2"
    assert wt_dir == root / ".worktrees" / "ingest_doc_architecture_blueprint_v2"
    assert CONVENTIONAL_BRANCH_RE.match(branch)


def test_derive_worktree_identity_live_audio():
    root = Path("/fake/repo/root")
    wt_dir, branch = derive_worktree_identity(
        modality=CanonicalModality.AUDIO,
        title="Standup 9 30 Mitch PartnerAdvisory",
        repo_root=root,
    )
    assert branch == "audio/standup-9-30-mitch-partner_advisory"
    assert wt_dir == root / ".worktrees" / "audio_standup_9_30_mitch_partner_advisory"
    assert CONVENTIONAL_BRANCH_RE.match(branch)


def test_derive_worktree_identity_meeting_transcript():
    root = Path("/fake/repo/root")
    wt_dir, branch = derive_worktree_identity(
        modality=CanonicalModality.MEETING_TRANSCRIPT,
        title="Infrastructure Orals Review",
        repo_root=root,
    )
    assert branch == "transcript/infrastructure-orals-review"
    assert wt_dir == root / ".worktrees" / "transcript_infrastructure_orals_review"
    assert CONVENTIONAL_BRANCH_RE.match(branch)


def test_git_check_ref_format_compliance():
    """Verify that derived branches pass git check-ref-format."""
    test_titles = [
        "Product Owner Role Description PartnerAdvisory",
        "Strategic Plan 2026-Q4",
        "Executive / Board Sync 10-12",
        "Multi...Dot..Handling Test!",
    ]
    for title in test_titles:
        _, branch = derive_worktree_identity(CanonicalModality.DOCUMENT, title)
        res = subprocess.run(
            ["git", "check-ref-format", "--branch", branch],
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, f"Branch '{branch}' failed git check-ref-format: {res.stderr}"
