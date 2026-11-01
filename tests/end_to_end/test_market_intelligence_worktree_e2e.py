"""
tests/end_to_end/test_market_intelligence_worktree_e2e.py

End-to-end lifecycle verification for market_intelligence document worktree provisioning.
Validates:
1. Market intelligence document ingestion provisions .worktrees/ingest_doc_* on branch ingest/doc-*
2. Neither branch nor worktree directory contains 'project_nexus'
3. Idempotent second invocation inherits same worktree without collision
4. Independent verification via git worktree list porcelain
"""
from __future__ import annotations

import subprocess
from pathlib import Path
import pytest

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.worktree_naming import derive_worktree_identity
from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
    ensure_ingest_worktree,
    run_git,
)
from dual_pipeline.query_router_core.project_router import determine_project_directory
from dual_pipeline.transcript_metadata_helper import scaffold_meeting_metadata


@pytest.fixture
def test_git_repo(tmp_path: Path) -> Path:
    """Creates a temporary initialized git repository with an initial commit on main."""
    repo = tmp_path / "test_repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test Agent"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)

    readme = repo / "README.md"
    readme.write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "chore: initial commit"], cwd=repo, check=True, capture_output=True)
    return repo


def test_market_intelligence_worktree_provisioning_e2e(test_git_repo: Path):
    """Verifies that market intelligence document provisions worktree with market_intelligence identity."""
    title = "Weekly Energy & Utilities Market Trends - 10/13/2026"
    doc_path = test_git_repo / "documents" / "market_intelligence" / "weekly_update_10-13-2026.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(f"# {title}\n\nMarket analysis content.", encoding="utf-8")

    # Step 1: Verify metadata resolution produces market_intelligence, not project_nexus
    meta = scaffold_meeting_metadata(
        input_path=doc_path,
        meeting_name=title,
    )
    resolved_proj = meta.get("project")
    assert resolved_proj == "market_intelligence"
    assert resolved_proj != "project_nexus"

    # Step 2: Verify ensure_ingest_worktree creates correct worktree directory and branch
    wt_dir, branch = ensure_ingest_worktree(
        modality=CanonicalModality.DOCUMENT,
        project_name=resolved_proj,
        title=title,
        date_dirname="10-13-2026",
        repo_root=test_git_repo,
    )

    assert "nexus" not in branch.lower()
    assert "nexus" not in wt_dir.name.lower()
    assert branch.startswith("ingest/doc-")
    assert wt_dir.name.startswith("ingest_doc_")
    assert wt_dir.is_dir()
    assert (wt_dir / ".git").is_file()

    # Step 3: Independent verification via git worktree list porcelain
    porcelain = run_git(["worktree", "list", "--porcelain"], cwd=test_git_repo)
    assert str(wt_dir) in porcelain
    assert f"refs/heads/{branch}" in porcelain

    # Step 4: Re-ingestion idempotence
    wt_dir_2, branch_2 = ensure_ingest_worktree(
        modality=CanonicalModality.DOCUMENT,
        project_name=resolved_proj,
        title=title,
        date_dirname="10-13-2026",
        repo_root=test_git_repo,
    )
    assert wt_dir_2 == wt_dir
    assert branch_2 == branch


def test_derive_worktree_identity_neutrality():
    """Verifies derive_worktree_identity produces conventional branch without model tokens or nexus leaks."""
    wt_dir, branch = derive_worktree_identity(
        modality="DOCUMENT",
        title="Weekly Energy & Utilities Market Trends - 10/13/2026",
        project="market_intelligence",
    )
    assert "project_nexus" not in branch
    assert "nexus" not in branch
    assert branch.startswith("ingest/doc-")
    assert "trends" in branch
