"""
End-to-End tests for worktree GC and rebase daemon CLIs.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def test_retire_superseded_branch_cli_e2e(tmp_path: Path):
    # 1. Protected branch refusal
    cmd_protected = [
        sys.executable,
        str(ROOT / "tools" / "retire_superseded_branch.py"),
        "main",
        "--dry-run",
    ]
    res_prot = subprocess.run(cmd_protected, cwd=ROOT, capture_output=True, text=True, check=False)
    assert res_prot.returncode != 0
    assert "is protected" in res_prot.stderr

    # 2. Isolated feature branch dry run
    repo_dir = tmp_path / "gc_e2e_repo"
    repo_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo_dir, check=True)
    (repo_dir / "init.txt").write_text("ok\n", encoding="utf-8")
    subprocess.run(["git", "add", "init.txt"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True)
    subprocess.run(["git", "branch", "feat/sample-feat"], cwd=repo_dir, check=True)

    # Retire via CLI from the temporary repo
    cmd_feat = [
        sys.executable,
        str(ROOT / "tools" / "retire_superseded_branch.py"),
        "feat/sample-feat",
        "--dry-run",
    ]
    res_feat = subprocess.run(cmd_feat, cwd=repo_dir, capture_output=True, text=True, check=False)
    assert res_feat.returncode == 0
    assert "[DRY-RUN]" in res_feat.stdout


def test_continuous_rebase_daemon_cli_e2e(tmp_path: Path):
    json_path = tmp_path / "daemon.json"
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "continuous_rebase_daemon.py"),
        "--json",
        str(json_path),
    ]
    res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    assert res.returncode == 0
    assert "CONTINUOUS REBASE SENTINEL" in res.stdout
    assert json_path.exists()
