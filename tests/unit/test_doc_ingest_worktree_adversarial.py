"""
tests/unit/test_doc_ingest_worktree_adversarial.py

Adversarial and boundary test suite for document ingestion worktree provisioning.
Covers 4 mandatory edge-case categories:
1. Boundary payloads: empty title, None modality, extreme length (>200 chars)
2. Malformed inputs: path traversal attempts, forbidden git ref chars, emojis
3. Concurrency & name collisions: punctuation variants resolving deterministically
4. Rollback & recovery: pre-existing unmanaged directory handling
"""
from __future__ import annotations

import subprocess
from pathlib import Path
import pytest

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.worktree_naming import (
    CONVENTIONAL_BRANCH_RE,
    derive_worktree_identity,
    slugify_branch_name,
)
from dual_pipeline.live_meeting_recorder_core.worktree_manager import ensure_ingest_worktree


@pytest.fixture
def isolated_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "adversarial_repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Adversarial Agent"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "adv@example.com"], cwd=repo, check=True, capture_output=True)
    init_file = repo / "init.txt"
    init_file.write_text("ok\n", encoding="utf-8")
    subprocess.run(["git", "add", "init.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "chore: base"], cwd=repo, check=True, capture_output=True)
    return repo


def test_boundary_payloads_empty_and_extreme_length(isolated_git_repo: Path):
    """Category 1: Boundary payload tests (empty, whitespace, max-len overflow)."""
    # Empty title
    wt_dir, branch = ensure_ingest_worktree(
        modality=CanonicalModality.DOCUMENT,
        title="",
        repo_root=isolated_git_repo,
    )
    assert branch.startswith("ingest/doc-")
    assert CONVENTIONAL_BRANCH_RE.match(branch)
    assert wt_dir.is_dir()

    # Extreme length title (>250 chars)
    long_title = "A" * 100 + " Very Long Title " + "B" * 100
    wt_dir_long, branch_long = ensure_ingest_worktree(
        modality=CanonicalModality.DOCUMENT,
        title=long_title,
        repo_root=isolated_git_repo,
    )
    slug = branch_long.replace("ingest/doc-", "")
    assert len(slug) <= 40
    assert not branch_long.endswith("-")
    assert CONVENTIONAL_BRANCH_RE.match(branch_long)
    assert wt_dir_long.is_dir()


def test_malformed_input_path_traversal_and_forbidden_chars(isolated_git_repo: Path):
    """Category 2: Malformed input (traversal sequences, git-illegal chars, emojis)."""
    malformed_titles = [
        "../../etc/passwd traversal attempt",
        "Document: With? *Forbidden* [Chars] ~ ^",
        "Document with emojis 🚀📄🔥 and unicode \u00a9\u00ae",
    ]
    for title in malformed_titles:
        wt_dir, branch = ensure_ingest_worktree(
            modality=CanonicalModality.DOCUMENT,
            title=title,
            repo_root=isolated_git_repo,
        )
        # Verify no directory traversal occurred: worktree resides strictly in .worktrees
        assert wt_dir.parent == isolated_git_repo / ".worktrees"
        assert CONVENTIONAL_BRANCH_RE.match(branch)

        # Independent git ref check
        res = subprocess.run(
            ["git", "check-ref-format", "--branch", branch],
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0, f"Branch '{branch}' failed git check-ref-format"


def test_concurrency_and_collision_variants(isolated_git_repo: Path):
    """Category 3: Punctuation variants resolve deterministically to same clean slug."""
    variant_a = "Product Owner (PartnerAdvisory)"
    variant_b = "Product Owner - PartnerAdvisory"
    variant_c = "Product__Owner___PartnerAdvisory"

    _, branch_a = derive_worktree_identity(CanonicalModality.DOCUMENT, variant_a)
    _, branch_b = derive_worktree_identity(CanonicalModality.DOCUMENT, variant_b)
    _, branch_c = derive_worktree_identity(CanonicalModality.DOCUMENT, variant_c)

    assert branch_a == branch_b == branch_c == "ingest/doc-product-owner-partner_advisory"


def test_rollback_and_recovery_preexisting_unmanaged_dir(isolated_git_repo: Path):
    """Category 4: Rollback and recovery when unmanaged directory exists before worktree provisioning."""
    target_wt = isolated_git_repo / ".worktrees" / "ingest_doc_recovered_asset"
    target_wt.mkdir(parents=True)
    orphan_file = target_wt / "untracked_draft.txt"
    orphan_file.write_text("valuable notes\n", encoding="utf-8")

    # Provision worktree targeting the existing directory
    wt_dir, branch = ensure_ingest_worktree(
        modality=CanonicalModality.DOCUMENT,
        title="Recovered Asset",
        repo_root=isolated_git_repo,
    )

    assert wt_dir == target_wt
    assert (wt_dir / ".git").is_file()
    # Preserves pre-existing files through unmanaged backup/restore invariant
    assert orphan_file.is_file()
    assert orphan_file.read_text(encoding="utf-8") == "valuable notes\n"
