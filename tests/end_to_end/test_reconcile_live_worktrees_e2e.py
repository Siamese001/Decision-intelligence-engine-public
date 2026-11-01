"""
End-to-End test suite for bifurcated live worktree reconciliation and safe pruning.
Validates CLI invocation of reconcile_live_worktrees.py in an authentic multi-worktree git repository.
"""
import subprocess
import sys
from pathlib import Path

import pytest

from tools.reconcile_live_worktrees import _sha256


def _setup_git_repo(repo_dir: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=str(repo_dir), check=True)
    (repo_dir / "README.md").write_text("# Test Repo\n")
    subprocess.run(["git", "add", "README.md"], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(repo_dir), check=True)


def test_reconcile_live_worktrees_cli_e2e(tmp_path):
    """End-to-end test executing reconcile_live_worktrees CLI with --execute."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _setup_git_repo(repo_root)

    wt_dir = repo_root / ".worktrees"
    wt_dir.mkdir()

    wt_source = wt_dir / "live_standup_amit"
    wt_target = wt_dir / "live_standup_amit_nozim"

    subprocess.run(["git", "worktree", "add", "-b", "feat/live-standup_amit", str(wt_source), "main"], cwd=str(repo_root), check=True, capture_output=True)
    subprocess.run(["git", "worktree", "add", "-b", "feat/live-standup_amit_nozim", str(wt_target), "main"], cwd=str(repo_root), check=True, capture_output=True)

    # Audio in source
    source_meeting = wt_source / "output" / "project_nexus" / "10-09-2026" / "Standup_Amit"
    source_meeting.mkdir(parents=True)
    source_wav = source_meeting / "00_10-09-2026_Standup_Amit_master.wav"
    source_wav.write_text("AUDIO_RAW_PCM_STREAM_TEST")
    expected_hash = _sha256(source_wav)

    # Deliverables in target
    target_meeting = wt_target / "output" / "project_nexus" / "10-09-2026" / "Standup_Amit_Nozim"
    target_meeting.mkdir(parents=True)
    (target_meeting / "01_transcript.md").write_text("# Transcript")
    (target_meeting / "02_recap.md").write_text("# Recap")
    (target_meeting / "03_projection.json").write_text('{"signal": 1}')

    # Execute reconciliation CLI tool
    cli_tool = Path(__file__).resolve().parents[2] / "tools" / "reconcile_live_worktrees.py"
    cmd = [sys.executable, str(cli_tool), "--repo-root", str(repo_root), "--execute"]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)

    assert proc.returncode == 0
    assert "Coalesced live_standup_amit -> live_standup_amit_nozim" in proc.stdout

    # Target worktree has all 4 outputs
    assert (target_meeting / "00_10-09-2026_Standup_Amit_master.wav").exists()
    assert (target_meeting / "01_transcript.md").exists()
    assert (target_meeting / "02_recap.md").exists()
    assert (target_meeting / "03_projection.json").exists()
    assert _sha256(target_meeting / "00_10-09-2026_Standup_Amit_master.wav") == expected_hash

    # Source worktree and branch removed
    assert not wt_source.exists()
    branch_check = subprocess.run(["git", "branch", "--list", "feat/live-standup_amit"], cwd=str(repo_root), capture_output=True, text=True)
    assert "feat/live-standup_amit" not in branch_check.stdout

    # Target worktree git state clean
    status_target = subprocess.run(["git", "status", "--porcelain"], cwd=str(wt_target), capture_output=True, text=True)
    assert status_target.stdout.strip() == ""
