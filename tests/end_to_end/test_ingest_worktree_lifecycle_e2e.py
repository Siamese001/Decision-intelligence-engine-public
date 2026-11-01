"""End-to-end lifecycle tests for governed ingestion worktree cleanup, dehydration, and batch binding (Wave 2).

Verifies:
1. Uncommitted deliverables in an isolated `.worktrees/ingest_doc_*` worktree are automatically staged and committed on `ingest/doc-*` during `teardown_ingest_worktree`.
2. Unmerged `ingest/doc-*` branches with committed deliverables are NEVER deleted by `teardown_ingest_worktree` (worktree directory is dehydrated while branch ref and `refs/archive/<branch>/<sha>` are preserved).
3. Dirty authored code files inside an ingestion worktree block `teardown_ingest_worktree` fail-closed.
4. Multi-attachment batch ingestion passing explicit `worktree_dir` or `worktree_branch` reuses a single shared worktree branch without spawning per-file worktrees.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dual_pipeline.canonical_pipeline.lifecycle import calculate_target_path
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.cli.parser import build_parser
from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
    ensure_ingest_worktree,
    run_git,
    teardown_ingest_worktree,
)


@pytest.fixture
def isolated_git_repo(tmp_path: Path) -> Path:
    """Creates an isolated temporary git repository with an initial commit on main."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test Operator"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "operator@example.com"], cwd=repo, check=True, capture_output=True)

    (repo / "README.md").write_text("# Main Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "chore: initial commit"], cwd=repo, check=True, capture_output=True)
    return repo


def test_unmerged_ingest_worktree_auto_commits_deliverables_and_preserves_branch_e2e(isolated_git_repo: Path) -> None:
    """Verifies teardown_ingest_worktree commits deliverables, writes archive ref, removes dir, and preserves unmerged branch."""
    wt_dir, branch_name = ensure_ingest_worktree(
        modality=CanonicalModality.DOCUMENT,
        project_name="hrit",
        title="Position Integration Field Mapping",
        date_dirname="10-20-2026",
        repo_root=isolated_git_repo,
    )
    assert wt_dir.is_dir()
    assert branch_name.startswith("ingest/doc-hrit-10-20-2026-")

    # Simulate generated deliverables inside the isolated worktree
    deliv_dir = wt_dir / "output" / "hrit" / "10-20-2026" / "Position_Integration_Field_Mapping"
    deliv_dir.mkdir(parents=True, exist_ok=True)
    proj_file = deliv_dir / "03_Position_Integration_Field_Mapping_canonical_signal_projection.json"
    proj_file.write_text('{"status": "PASS", "entities": 14}\n', encoding="utf-8")
    proof_dir = wt_dir / "reports" / "graph_mutation_proofs"
    proof_dir.mkdir(parents=True, exist_ok=True)
    (proof_dir / "graph-writer_proof.json").write_text('{"verified": true}\n', encoding="utf-8")

    # Teardown worktree while branch is still unmerged into main
    ok = teardown_ingest_worktree(wt_dir, branch_name, repo_root=isolated_git_repo)
    assert ok is True
    assert not wt_dir.exists(), "Worktree directory should be dehydrated to free WIP slot"

    # Branch MUST still exist in git because it has unmerged deliverable commits
    branch_sha = run_git(["rev-parse", f"refs/heads/{branch_name}"], cwd=isolated_git_repo)
    assert len(branch_sha) == 40

    # Immutable archive ref MUST exist
    archive_sha = run_git(["rev-parse", f"refs/archive/{branch_name}/{branch_sha}"], cwd=isolated_git_repo)
    assert archive_sha == branch_sha

    # Deliverables MUST be committed in the branch tree and root main output/ MUST remain clean
    tree_files = run_git(["ls-tree", "-r", "--name-only", branch_name], cwd=isolated_git_repo).splitlines()
    assert "output/hrit/10-20-2026/Position_Integration_Field_Mapping/03_Position_Integration_Field_Mapping_canonical_signal_projection.json" in tree_files
    assert "reports/graph_mutation_proofs/graph-writer_proof.json" in tree_files
    assert not (isolated_git_repo / "output").exists(), "Root main working tree must have zero untracked output leakage"


def test_teardown_ingest_worktree_blocks_on_dirty_authored_code_e2e(isolated_git_repo: Path) -> None:
    """Verifies teardown_ingest_worktree refuses to destroy a worktree with uncommitted code edits."""
    wt_dir, branch_name = ensure_ingest_worktree(
        modality=CanonicalModality.DOCUMENT,
        project_name="hrit",
        title="Dirty Code Guard Test",
        date_dirname="10-20-2026",
        repo_root=isolated_git_repo,
    )
    code_file = wt_dir / "dual_pipeline" / "some_module.py"
    code_file.parent.mkdir(parents=True, exist_ok=True)
    code_file.write_text("x = 1\n", encoding="utf-8")

    ok = teardown_ingest_worktree(wt_dir, branch_name, repo_root=isolated_git_repo)
    assert ok is False, "Teardown must block fail-closed when uncommitted code edits exist"
    assert wt_dir.exists(), "Worktree directory must remain intact when blocked"


def test_multi_attachment_batch_shares_single_designated_worktree_e2e(isolated_git_repo: Path) -> None:
    """Verifies multiple attachments specifying worktree_branch or worktree_dir share one isolated worktree."""
    parser = build_parser()
    parsed = parser.parse_args([
        "ingest-document",
        "--input", "doc1.xlsx",
        "--project", "hrit",
        "--date", "10-20-2026",
        "--worktree-branch", "ingest/doc-hrit-10-24-2026-clement-prf-package",
    ])
    assert parsed.worktree_branch == "ingest/doc-hrit-10-24-2026-clement-prf-package"

    shared_branch = "ingest/doc-hrit-10-24-2026-clement-prf-package"
    shared_wt = isolated_git_repo / ".worktrees" / "ingest_doc_hrit_10_24_2026_clement_prf_package"

    from dual_pipeline.canonical_pipeline.worktree_inheritance import resolve_target_worktree_path

    meta_1 = {"worktree_branch": shared_branch, "enforce_worktree": True}
    p1 = resolve_target_worktree_path(
        base_out=isolated_git_repo / "output" / "hrit" / "10-20-2026" / "Attachment_One",
        meta_dict=meta_1,
        proj="hrit",
        date_fn="10-20-2026",
        slug="Attachment_One",
        no_subfolder=False,
        output_dir="auto",
        repo_root=isolated_git_repo,
        modality=CanonicalModality.DOCUMENT,
    )
    meta_2 = {"worktree_branch": shared_branch, "enforce_worktree": True}
    p2 = resolve_target_worktree_path(
        base_out=isolated_git_repo / "output" / "hrit" / "10-20-2026" / "Attachment_Two",
        meta_dict=meta_2,
        proj="hrit",
        date_fn="10-20-2026",
        slug="Attachment_Two",
        no_subfolder=False,
        output_dir="auto",
        repo_root=isolated_git_repo,
        modality=CanonicalModality.DOCUMENT,
    )

    assert p1.parent == p2.parent
    assert meta_1["worktree_dir"] == str(shared_wt)
    assert meta_2["worktree_dir"] == str(shared_wt)
    assert meta_1["worktree_branch"] == shared_branch
    assert meta_2["worktree_branch"] == shared_branch
