"""Wave 8 Adversarial Tests: Merge-Tree Conflicts, Tampered Merge Commits & Rollback Boundaries."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from tools.pr_governance_core.lifecycle import (
    atomic_rollback_main_to_sha,
    verify_ephemeral_merge_tree,
)
from tools.verify_post_merge import verify_latest_merge


def _git_env() -> dict[str, str]:
    banned = {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX"}
    env = {k: v for k, v in os.environ.items() if k not in banned}
    env["GIT_AUTHOR_NAME"] = "Test"
    env["GIT_AUTHOR_EMAIL"] = "test@example.com"
    env["GIT_COMMITTER_NAME"] = "Test"
    env["GIT_COMMITTER_EMAIL"] = "test@example.com"
    return env


def test_adversarial_ephemeral_merge_tree_rejects_conflicting_branches(tmp_path: Path) -> None:
    """Proves verify_ephemeral_merge_tree fails closed when main and branch modify the same line."""
    repo = tmp_path / "conflict_repo"
    repo.mkdir()
    env = _git_env()

    subprocess.run(["git", "init", "-b", "main"], cwd=repo, env=env, check=True)
    (repo / "shared.py").write_text("X = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, env=env, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, env=env, check=True)

    subprocess.run(["git", "checkout", "-b", "feat/conflict"], cwd=repo, env=env, check=True)
    (repo / "shared.py").write_text("X = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, env=env, check=True)
    subprocess.run(["git", "commit", "-m", "branch edit"], cwd=repo, env=env, check=True)

    subprocess.run(["git", "checkout", "main"], cwd=repo, env=env, check=True)
    (repo / "shared.py").write_text("X = 3\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, env=env, check=True)
    subprocess.run(["git", "commit", "-m", "main edit"], cwd=repo, env=env, check=True)

    ok, detail = verify_ephemeral_merge_tree(repo, "main", "feat/conflict")
    assert ok is False
    assert "conflict" in detail.lower()


def test_adversarial_verify_post_merge_rejects_extra_injected_files(tmp_path: Path) -> None:
    """Proves verify_latest_merge rejects a merge commit that injects unreviewed files beyond the receipt."""
    repo = tmp_path / "injected_repo"
    repo.mkdir()
    env = _git_env()

    subprocess.run(["git", "init", "-b", "main"], cwd=repo, env=env, check=True)
    (repo / "app.py").write_text("A = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, env=env, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, env=env, check=True)
    main_sha = subprocess.run(
        ["git", "rev-parse", "main"], cwd=repo, env=env, capture_output=True, text=True, check=True
    ).stdout.strip()

    subprocess.run(["git", "checkout", "-b", "feat/clean"], cwd=repo, env=env, check=True)
    (repo / "app.py").write_text("A = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, env=env, check=True)
    subprocess.run(["git", "commit", "-m", "branch commit"], cwd=repo, env=env, check=True)
    branch_sha = subprocess.run(
        ["git", "rev-parse", "feat/clean"], cwd=repo, env=env, capture_output=True, text=True, check=True
    ).stdout.strip()

    subprocess.run(["git", "checkout", "main"], cwd=repo, env=env, check=True)
    subprocess.run(["git", "merge", "--no-ff", "--no-commit", "feat/clean"], cwd=repo, env=env, check=True)

    msg = "Merge branch 'feat/clean' into main (clean merge)"
    rcpt = repo / "reports" / "pr_merge_feat_clean_receipt.json"
    rcpt.parent.mkdir(parents=True)
    rcpt.write_text(
        json.dumps(
            {
                "pr_type": "LOCAL_FEATURE_BRANCH_MERGE",
                "branch": "feat/clean",
                "head_sha": branch_sha,
                "branch_head_sha": branch_sha,
                "main_base_sha": main_sha,
                "merge_message": msg,
                "target": "main",
                "terminal_outcome": "PASS",
            }
        ),
        encoding="utf-8",
    )
    # Inject a rogue file during merge commit
    (repo / "rogue_backdoor.py").write_text("BACKDOOR = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, env=env, check=True)
    subprocess.run(["git", "commit", "-m", msg], cwd=repo, env=env, check=True)

    ok, detail = verify_latest_merge(repo)
    assert ok is False
    assert "differs from the reviewed branch" in detail


def test_adversarial_boundary_and_invalid_refs_in_rollback_and_merge_tree(tmp_path: Path) -> None:
    """Handles empty SHAs and nonexistent refs fail-closed without raising unhandled exceptions."""
    assert atomic_rollback_main_to_sha(tmp_path, "") is False
    assert atomic_rollback_main_to_sha(tmp_path, "deadbeef" * 5) is False
    ok, _ = verify_ephemeral_merge_tree(tmp_path, "main", "feat/nonexistent")
    assert ok is False
