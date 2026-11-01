"""
tests/end_to_end/test_doc_ingest_worktree_e2e.py

End-to-end lifecycle verification for document ingestion worktree provisioning.
Validates:
1. Document ingestion provisions .worktrees/ingest_doc_* on branch ingest/doc-*
2. Live recording continues provisioning .worktrees/live_* on branch feat/live-*
3. Re-ingestion idempotence inherits existing worktree cleanly
4. Independent verification via git worktree list porcelain
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
import pytest

from dual_pipeline.canonical_pipeline.lifecycle import calculate_target_path
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
    ensure_ingest_worktree,
    run_git,
)


@pytest.fixture
def test_git_repo(tmp_path: Path) -> Path:
    """Creates a temporary initialized git repository with an initial commit on main."""
    repo = tmp_path / "test_repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test Agent"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)

    # Initial commit
    readme = repo / "README.md"
    readme.write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "chore: initial commit"], cwd=repo, check=True, capture_output=True)
    return repo


def test_document_ingestion_worktree_lifecycle_e2e(test_git_repo: Path):
    """Verifies that DOCUMENT modality provisions ingest/doc-* branch and ingest_doc_* worktree."""
    meta = {
        "title": "Product Owner Role Description PartnerAdvisory",
        "meeting_name": "Product Owner Role Description PartnerAdvisory",
        "project": "project_nexus",
        "enforce_worktree": True,
    }

    # Execute target path resolution for DOCUMENT
    target_path = calculate_target_path(
        output_dir="auto",
        proj="project_nexus",
        date_fn="10-12-2026",
        slug="product_owner_role_description_partner_advisory",
        no_subfolder=False,
        modality=CanonicalModality.DOCUMENT,
        meta=meta,
    )

    # Note: calculate_target_path uses ROOT by default unless repo_root is passed in lower levels.
    # Test direct ensure_ingest_worktree in test_git_repo:
    wt_dir, branch = ensure_ingest_worktree(
        modality=CanonicalModality.DOCUMENT,
        project_name="project_nexus",
        title="Product Owner Role Description PartnerAdvisory",
        date_dirname="10-12-2026",
        repo_root=test_git_repo,
    )

    assert branch == "ingest/doc-project-nexus-10-12-2026-product-owner-role-description-partner_advisory"
    assert wt_dir.name == "ingest_doc_project_nexus_10_12_2026_product_owner_role_description_partner_advisory"
    assert wt_dir.is_dir()
    assert (wt_dir / ".git").is_file()

    # Independent check via git worktree list
    porcelain = run_git(["worktree", "list", "--porcelain"], cwd=test_git_repo)
    assert str(wt_dir) in porcelain
    assert f"refs/heads/{branch}" in porcelain

    # Verify re-ingestion idempotence: second call returns the same worktree without error
    wt_dir_2, branch_2 = ensure_ingest_worktree(
        modality=CanonicalModality.DOCUMENT,
        project_name="project_nexus",
        title="Product Owner Role Description PartnerAdvisory",
        date_dirname="10-12-2026",
        repo_root=test_git_repo,
    )
    assert wt_dir_2 == wt_dir
    assert branch_2 == branch


def test_recording_ingestion_preserves_live_worktree_e2e(test_git_repo: Path):
    """Verifies that AUDIO / recording modality continues provisioning feat/live-* and live_*."""
    wt_dir, branch = ensure_ingest_worktree(
        modality=CanonicalModality.AUDIO,
        project_name="project_nexus",
        title="Standup 9 30 Mitch PartnerAdvisory",
        date_dirname="10-12-2026",
        repo_root=test_git_repo,
    )

    assert branch == "feat/live-project-nexus-10-12-2026-standup-9-30-mitch-partner_advisory"
    assert wt_dir.name == "feat_live_project_nexus_10_12_2026_standup_9_30_mitch_partner_advisory"
    assert wt_dir.is_dir()

    # Independent check via git worktree list
    porcelain = run_git(["worktree", "list", "--porcelain"], cwd=test_git_repo)
    assert str(wt_dir) in porcelain
    assert f"refs/heads/{branch}" in porcelain
