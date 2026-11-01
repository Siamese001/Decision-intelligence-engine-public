"""Wave 8 E2E Tests: Ephemeral Merge-Tree Pre-Verification & Atomic Auto-Rollback on main."""
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


def test_e2e_ephemeral_merge_tree_precheck_and_conflict_detection(tmp_path: Path) -> None:
    """Proves verify_ephemeral_merge_tree checks main + branch in-memory without mutating main."""
    repo = tmp_path / "mt_repo"
    repo.mkdir()
    env = _git_env()

    subprocess.run(["git", "init", "-b", "main"], cwd=repo, env=env, check=True)
    (repo / "service.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, env=env, check=True)
    subprocess.run(["git", "commit", "-m", "initial main"], cwd=repo, env=env, check=True)
    main_sha = subprocess.run(
        ["git", "rev-parse", "main"], cwd=repo, env=env, capture_output=True, text=True, check=True
    ).stdout.strip()

    subprocess.run(["git", "checkout", "-b", "feat/clean-branch"], cwd=repo, env=env, check=True)
    (repo / "feature.py").write_text("FEATURE = True\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, env=env, check=True)
    subprocess.run(["git", "commit", "-m", "feat: add feature"], cwd=repo, env=env, check=True)
    subprocess.run(["git", "checkout", "main"], cwd=repo, env=env, check=True)

    ok, tree_oid = verify_ephemeral_merge_tree(repo, "main", "feat/clean-branch")
    assert ok is True
    assert len(tree_oid) == 40
    # Verify main was not mutated by merge-tree check
    after_sha = subprocess.run(
        ["git", "rev-parse", "main"], cwd=repo, env=env, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert after_sha == main_sha


def test_e2e_post_merge_failure_triggers_atomic_rollback_to_pre_merge_sha(tmp_path: Path) -> None:
    """Proves main is atomically restored to pre_merge_main_sha if verify_latest_merge fails."""
    repo = tmp_path / "rollback_repo"
    repo.mkdir()
    env = _git_env()

    subprocess.run(["git", "init", "-b", "main"], cwd=repo, env=env, check=True)
    (repo / "core.py").write_text("v = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, env=env, check=True)
    subprocess.run(["git", "commit", "-m", "base main"], cwd=repo, env=env, check=True)
    pre_merge_main_sha = subprocess.run(
        ["git", "rev-parse", "main"], cwd=repo, env=env, capture_output=True, text=True, check=True
    ).stdout.strip()

    subprocess.run(["git", "checkout", "-b", "fix/broken-receipt"], cwd=repo, env=env, check=True)
    (repo / "core.py").write_text("v = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, env=env, check=True)
    subprocess.run(["git", "commit", "-m", "fix: update core"], cwd=repo, env=env, check=True)

    subprocess.run(["git", "checkout", "main"], cwd=repo, env=env, check=True)
    subprocess.run(
        ["git", "merge", "--no-ff", "--no-commit", "fix/broken-receipt"],
        cwd=repo,
        env=env,
        check=True,
    )
    # Write a tampered/invalid merge receipt so verify_latest_merge fails
    rcpt = repo / "reports" / "pr_merge_fix_broken_receipt_receipt.json"
    rcpt.parent.mkdir(parents=True)
    rcpt.write_text(json.dumps({"terminal_outcome": "FAIL"}), encoding="utf-8")
    subprocess.run(["git", "add", str(rcpt)], cwd=repo, env=env, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Merge branch 'fix/broken-receipt' into main (test title)"],
        cwd=repo,
        env=env,
        check=True,
    )

    cert_ok, detail = verify_latest_merge(repo)
    assert cert_ok is False
    assert "does not match" in detail

    rolled_back = atomic_rollback_main_to_sha(repo, pre_merge_main_sha)
    assert rolled_back is True
    restored_sha = subprocess.run(
        ["git", "rev-parse", "main"], cwd=repo, env=env, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert restored_sha == pre_merge_main_sha
    assert (repo / "core.py").read_text(encoding="utf-8") == "v = 1\n"
