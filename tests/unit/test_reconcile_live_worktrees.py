"""
Unit tests validating bifurcated worktree coalescence and hygiene pruner hardening for Wave 4.
Validates coalesce_live_worktree_pair, detect_bifurcated_pairs, and abandoned live worktree pruning.
"""
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.audit_worktree_hygiene_core.pruner import (
    _is_abandoned_live_worktree,
    cleanup_abandoned_zero_commit_worktrees,
)
from tools.reconcile_live_worktrees import (
    classify_source_and_target,
    coalesce_live_worktree_pair,
    detect_bifurcated_pairs,
    migrate_artifacts,
    _sha256,
)


def _setup_git_repo(repo_dir: Path) -> None:
    """Helper to initialize an authentic git repo with main branch."""
    subprocess.run(["git", "init", "-b", "main"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=str(repo_dir), check=True)
    (repo_dir / "README.md").write_text("# Test Repo\n")
    subprocess.run(["git", "add", "README.md"], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(repo_dir), check=True)


def test_classify_source_and_target(tmp_path):
    """Verify classification identifies audio-holder as source and deliverable-holder as target."""
    wt_a = tmp_path / "live_standup_amit"
    wt_b = tmp_path / "live_standup_amit_nozim"

    # wt_a has audio only
    dir_a = wt_a / "output" / "project_nexus" / "10-09-2026" / "Standup_Amit"
    dir_a.mkdir(parents=True)
    (dir_a / "00_master.wav").write_text("AUDIO_DATA")

    # wt_b has deliverables
    dir_b = wt_b / "output" / "project_nexus" / "10-09-2026" / "Standup_Amit_Nozim"
    dir_b.mkdir(parents=True)
    (dir_b / "01_transcript.md").write_text("# Transcript")
    (dir_b / "02_recap.md").write_text("# Recap")
    (dir_b / "03_signal_projection.json").write_text('{"nodes": []}')

    source, target = classify_source_and_target(wt_a, wt_b)
    assert source == wt_a
    assert target == wt_b


def test_detect_bifurcated_pairs(tmp_path):
    """Verify pair detection identifies matching base and attendee-expanded worktrees."""
    wt_dir = tmp_path / ".worktrees"
    wt_dir.mkdir()

    wt1 = wt_dir / "live_standup_amit"
    wt2 = wt_dir / "live_standup_amit_nozim"
    wt_other = wt_dir / "live_sprint_planning_client"

    for w in (wt1, wt2, wt_other):
        w.mkdir()

    pairs = detect_bifurcated_pairs(tmp_path)
    assert len(pairs) == 1
    src, tgt = pairs[0]
    names = {src.name, tgt.name}
    assert names == {"live_standup_amit", "live_standup_amit_nozim"}


def test_coalesce_live_worktree_pair_full(tmp_path):
    """
    Test suite validating successful coalescence of live_standup_amit and live_standup_amit_nozim:
    Merges audio from feat/live-standup_amit into feat/live-standup_amit_nozim and removes redundant worktree.
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _setup_git_repo(repo_root)

    wt_dir = repo_root / ".worktrees"
    wt_dir.mkdir()

    wt_source = wt_dir / "live_standup_amit"
    wt_target = wt_dir / "live_standup_amit_nozim"

    # Provision authentic worktrees
    subprocess.run(["git", "worktree", "add", "-b", "feat/live-standup_amit", str(wt_source), "main"], cwd=str(repo_root), check=True, capture_output=True)
    subprocess.run(["git", "worktree", "add", "-b", "feat/live-standup_amit_nozim", str(wt_target), "main"], cwd=str(repo_root), check=True, capture_output=True)

    # Populate source with audio and cache
    source_out = wt_source / "output" / "project_nexus" / "10-09-2026" / "Standup_Amit"
    source_out.mkdir(parents=True)
    master_wav = source_out / "00_10-09-2026_Standup_Amit_master.wav"
    master_wav.write_text("RIFF...WAVEfmt ...PCM AUDIO BYTES")
    expected_hash = _sha256(master_wav)
    (wt_source / ".cache").mkdir()
    (wt_source / ".cache" / "segments.jsonl").write_text('{"text": "hello"}\n')

    # Populate target with deliverables
    target_out = wt_target / "output" / "project_nexus" / "10-09-2026" / "Standup_Amit_Nozim"
    target_out.mkdir(parents=True)
    (target_out / "01_transcript.md").write_text("# Transcript")
    (target_out / "02_recap.md").write_text("# Executive Recap")
    (target_out / "03_projection.json").write_text('{"signal": 1}')

    res = coalesce_live_worktree_pair(
        source_wt=wt_source,
        target_wt=wt_target,
        repo_root=repo_root,
        dry_run=False,
    )

    assert res["status"] == "PASS"
    assert res["pruned"] is True

    # Verify target worktree now contains all 4 outputs
    assert (target_out / "00_10-09-2026_Standup_Amit_master.wav").exists()
    assert (target_out / "01_transcript.md").exists()
    assert (target_out / "02_recap.md").exists()
    assert (target_out / "03_projection.json").exists()
    assert (wt_target / ".cache" / "segments.jsonl").exists()

    # Verify hash equality
    assert _sha256(target_out / "00_10-09-2026_Standup_Amit_master.wav") == expected_hash

    # Verify source worktree directory is removed
    assert not wt_source.exists()

    # Verify source branch is deleted from git
    branch_check = subprocess.run(["git", "branch", "--list", "feat/live-standup_amit"], cwd=str(repo_root), capture_output=True, text=True)
    assert "feat/live-standup_amit" not in branch_check.stdout


def test_pruner_abandoned_zero_commit_worktree(tmp_path):
    """Verify cleanup_abandoned_zero_commit_worktrees prunes zero-commit empty live worktrees."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _setup_git_repo(repo_root)

    wt_dir = repo_root / ".worktrees"
    wt_dir.mkdir()
    wt_abandoned = wt_dir / "live_sprint_4_00_pm_et_client_partner_advisory_160532_943"

    subprocess.run(["git", "worktree", "add", "-b", "feat/live-sprint_4_00_pm_et_client_partner_advisory_160532_943", str(wt_abandoned), "main"], cwd=str(repo_root), check=True, capture_output=True)

    # Run cleanup with max_age_hours=0.0
    cleaned = cleanup_abandoned_zero_commit_worktrees(repo_root, base_branch="main", max_age_hours=0.0)
    assert len(cleaned) == 1
    assert "feat/live-sprint_4_00_pm_et_client_partner_advisory_160532_943" in cleaned[0]
    assert not wt_abandoned.exists()


def test_pruner_preserves_worktrees_with_audio(tmp_path):
    """Verify pruner refuses to delete zero-commit worktrees that contain uncommitted audio."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _setup_git_repo(repo_root)

    wt_dir = repo_root / ".worktrees"
    wt_dir.mkdir()
    wt_active = wt_dir / "live_standup_active"

    subprocess.run(["git", "worktree", "add", "-b", "feat/live-standup_active", str(wt_active), "main"], cwd=str(repo_root), check=True, capture_output=True)

    # Add uncommitted audio
    audio_dir = wt_active / "output" / "proj" / "date" / "slug"
    audio_dir.mkdir(parents=True)
    (audio_dir / "00_master.wav").write_text("AUDIO")

    cleaned = cleanup_abandoned_zero_commit_worktrees(repo_root, base_branch="main", max_age_hours=0.0)
    assert len(cleaned) == 0
    assert wt_active.exists()
