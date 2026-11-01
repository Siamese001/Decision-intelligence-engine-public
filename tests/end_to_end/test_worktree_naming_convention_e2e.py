"""
tests/end_to_end/test_worktree_naming_convention_e2e.py

End-to-End lifecycle verification for worktree and branch naming convention.
Validates:
1. AUDIO / LIVE modality creates audio/<project>-<date>-<clean_slug> and .worktrees/live_<project>_<date>_<clean_slug>
2. MEETING_TRANSCRIPT modality creates transcript/<project>-<date>-<clean_slug> and .worktrees/transcript_<project>_<date>_<clean_slug>
3. DOCUMENT modality creates ingest/doc-<project>-<date>-<clean_slug> and .worktrees/ingest_doc_<project>_<date>_<clean_slug>
4. Worktree provisioning creates authentic worktree with porcelain confirmation and INV-WT-03/04 conformance
5. Repeated invocation inherits existing worktree idempotently without collision
"""
from __future__ import annotations

import subprocess
from pathlib import Path
import pytest

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.worktree_naming import (
    CONVENTIONAL_BRANCH_RE,
    derive_worktree_identity,
)
from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
    ensure_ingest_worktree,
    ensure_live_transcription_worktree,
    run_git,
)
from tools.pr_governance_core.common import slugify_branch
from tools.pr_governance_core.registry import verify_worktree_registry


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


def test_live_meeting_worktree_naming_e2e(test_git_repo: Path):
    """Verifies that live meeting recording embeds project, date, and clean_slug."""
    project = "LENS"
    meeting_name = "LENS Business meeting"
    date_str = "10-15-2026"
    clean_slug = "Business_PartnerAdvisoryteam_Daniel"

    wt_dir, branch = ensure_live_transcription_worktree(
        project_name=project,
        meeting_name=meeting_name,
        date_dirname=date_str,
        clean_slug=clean_slug,
        repo_root=test_git_repo,
    )

    # Invariant assertions
    assert CONVENTIONAL_BRANCH_RE.match(branch)
    assert branch.startswith("audio/")
    assert "lens" in branch
    assert "10-15-2026" in branch
    assert "business-partner_advisoryteam-daniel" in branch

    assert wt_dir.name.startswith("audio_")
    assert "lens" in wt_dir.name
    assert "10_15_2026" in wt_dir.name
    assert "business_partner_advisoryteam_daniel" in wt_dir.name

    # Physical git assertions
    assert wt_dir.is_dir()
    assert (wt_dir / ".git").is_file()

    porcelain = run_git(["worktree", "list", "--porcelain"], cwd=test_git_repo)
    assert str(wt_dir) in porcelain
    assert f"refs/heads/{branch}" in porcelain

    # Idempotent re-invocation
    wt_dir_2, branch_2 = ensure_live_transcription_worktree(
        project_name=project,
        meeting_name=meeting_name,
        date_dirname=date_str,
        clean_slug=clean_slug,
        repo_root=test_git_repo,
    )
    assert wt_dir_2 == wt_dir
    assert branch_2 == branch


def test_transcript_modality_worktree_naming_e2e(test_git_repo: Path):
    """Verifies that meeting transcript ingestion embeds project, date, and clean_slug."""
    wt_dir, branch = ensure_ingest_worktree(
        modality=CanonicalModality.MEETING_TRANSCRIPT,
        project_name="LENS",
        title="LENS Strategy Session",
        date_dirname="10-15-2026",
        clean_slug="Strategy_Session_Alex",
        repo_root=test_git_repo,
    )

    assert branch.startswith("transcript/")
    assert "lens" in branch
    assert "10-15-2026" in branch
    assert "strategy-session-alex" in branch
    assert CONVENTIONAL_BRANCH_RE.match(branch)

    assert wt_dir.name.startswith("transcript_")
    assert wt_dir.is_dir()


def test_document_modality_worktree_naming_e2e(test_git_repo: Path):
    """Verifies that document ingestion embeds project, date, and clean_slug."""
    wt_dir, branch = ensure_ingest_worktree(
        modality=CanonicalModality.DOCUMENT,
        project_name="market_intelligence",
        title="Weekly Energy Update",
        date_dirname="10-13-2026",
        clean_slug="Weekly_Energy_Update",
        repo_root=test_git_repo,
    )

    assert branch.startswith("ingest/doc-")
    assert "market-intelligence" in branch
    assert "10-13-2026" in branch
    assert "weekly-energy-update" in branch
    assert CONVENTIONAL_BRANCH_RE.match(branch)

    assert wt_dir.name.startswith("ingest_doc_")
    assert wt_dir.is_dir()


def test_worktree_inheritance_and_routing_e2e(test_git_repo: Path, monkeypatch):
    """Verifies that resolve_routing and worktree inheritance preserve project, date, and clean_slug."""
    # Run from the temp repo so a real worktree CWD cannot take precedence.
    monkeypatch.chdir(test_git_repo)
    from dual_pipeline.canonical_pipeline.lifecycle import resolve_routing

    project = "LENS"
    date_fn = "10-15-2026"
    clean_slug = "Business_PartnerAdvisoryteam_Daniel"

    # Pre-create worktree with existing subfolder
    wt_dir = test_git_repo / ".worktrees" / "audio_lens_10_15_2026_business_partner_advisoryteam_daniel"
    meeting_dir = wt_dir / "output" / project / date_fn / clean_slug
    meeting_dir.mkdir(parents=True, exist_ok=True)
    live_trans = meeting_dir / f"01_{date_fn}_{clean_slug}_live_transcript.md"
    live_trans.write_text("# Live Transcript\nVoice A: Hello", encoding="utf-8")

    meta = {
        "project": project,
        "meeting_name": "LENS Business meeting",
        "date": "10/15/2026",
        "input_path": str(live_trans),
        "attendees": "Daniel Sabet, Jen Pan",
    }

    target_path = resolve_routing(meta, output_dir="auto", modality=CanonicalModality.AUDIO)
    assert meta.get("worktree_dir") == str(wt_dir)
    assert target_path == meeting_dir
    assert meta.get("filename_slug") == clean_slug


def test_worktree_lock_detection_with_divergent_slug(test_git_repo: Path):
    """Verifies is_worktree_locked_or_active detects locks even when slug in session_locks differs from worktree name."""
    import fcntl
    import os
    from tools.reconcile_live_worktrees import is_worktree_locked_or_active

    wt_dir = test_git_repo / ".worktrees" / "audio_lens_business_meeting"
    meeting_dir = wt_dir / "output" / "LENS" / "10-15-2026" / "Business_PartnerAdvisoryteam_Daniel"
    meeting_dir.mkdir(parents=True, exist_ok=True)
    live_trans = meeting_dir / "01_10-15-2026_Business_PartnerAdvisoryteam_Daniel_live_transcript.md"
    live_trans.write_text("# Transcript", encoding="utf-8")

    locks_dir = test_git_repo / ".session_locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    lock_file = locks_dir / "Business_PartnerAdvisoryteam_Daniel.flock"
    lock_file.touch()

    # Unlocked state
    assert not is_worktree_locked_or_active(wt_dir, test_git_repo)

    # Acquire lock
    fd = os.open(str(lock_file), os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Should now be detected as locked
        assert is_worktree_locked_or_active(wt_dir, test_git_repo)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    # Should now be unlocked
    assert not is_worktree_locked_or_active(wt_dir, test_git_repo)


def test_governance_registry_and_live_meeting_bridge(test_git_repo: Path):
    """Verifies live meeting bridge and PR governance registry classify unified naming conventions without violation."""
    from tools.pr_governance_core.live_meeting_bridge import (
        LiveMeetingStatus,
        classify_live_worktree,
        is_live_meeting_container,
    )
    from tools.pr_governance_core.registry import verify_worktree_registry

    # 1. Container detection across unified prefixes
    assert is_live_meeting_container(test_git_repo / ".worktrees" / "audio_lens_10_15_2026_business_partner_advisoryteam_daniel")
    assert is_live_meeting_container(test_git_repo / ".worktrees" / "transcript_lens_10_15_2026_business_partner_advisoryteam_daniel")
    assert is_live_meeting_container(test_git_repo / ".worktrees" / "ingest_meeting_lens_10_15_2026_business_partner_advisoryteam_daniel")
    assert not is_live_meeting_container(test_git_repo / ".worktrees" / "ingest_doc_lens_10_15_2026_role_description")

    # 2. Registry validation on unified worktree/branch pair
    worktrees = [
        {"path": str(test_git_repo), "branch": "refs/heads/main"},
        {
            "path": str(test_git_repo / ".worktrees" / "audio_lens_10_15_2026_business_partner_advisoryteam_daniel"),
            "branch": "refs/heads/audio/lens-10-15-2026-business-partner_advisoryteam-daniel",
        },
        {
            "path": str(test_git_repo / ".worktrees" / "transcript_lens_10_15_2026_business_partner_advisoryteam_daniel"),
            "branch": "refs/heads/transcript/lens-10-15-2026-business-partner_advisoryteam-daniel",
        },
        {
            "path": str(test_git_repo / ".worktrees" / "ingest_doc_lens_10_15_2026_role_description"),
            "branch": "refs/heads/ingest/doc-lens-10-15-2026-role-description",
        },
    ]
    violations = verify_worktree_registry(test_git_repo, worktrees)
    assert not violations, f"Expected 0 registry violations, got: {violations}"


def test_remediate_misnamed_worktree_scan_and_auto_derive(test_git_repo: Path):
    """Verifies remediate_misnamed_worktree accurately identifies legacy worktrees and auto-derives canonical targets."""
    import json
    from tools.remediate_misnamed_worktree import (
        is_legacy_misnamed_worktree,
        remediate_worktree,
        scan_legacy_worktrees,
    )

    # Provision simulated legacy worktree
    legacy_wt = test_git_repo / ".worktrees" / "audio_lens_business_meeting"
    meeting_dir = legacy_wt / "output" / "LENS" / "10-15-2026" / "Business_PartnerAdvisoryteam_Daniel"
    meeting_dir.mkdir(parents=True, exist_ok=True)
    (meeting_dir / "01_10-15-2026_Business_PartnerAdvisoryteam_Daniel_live_transcript.md").write_text("# Trans", encoding="utf-8")
    meta_payload = {
        "project": "LENS",
        "date": "10/15/2026",
        "clean_slug": "Business_PartnerAdvisoryteam_Daniel",
        "meeting_name": "LENS Business meeting",
        "modality": "AUDIO",
    }
    (meeting_dir / "metadata.json").write_text(json.dumps(meta_payload), encoding="utf-8")

    # 1. Detection
    is_legacy, reason, canonical = is_legacy_misnamed_worktree(legacy_wt)
    assert is_legacy
    assert canonical is not None
    assert canonical[0] == "audio/lens-10-15-2026-business-partner_advisoryteam-daniel"
    assert canonical[1].name == "audio_lens_10_15_2026_business_partner_advisoryteam_daniel"

    # 2. Scanning
    scan_results = scan_legacy_worktrees(test_git_repo)
    matched = [w for w in scan_results if w["name"] == "audio_lens_business_meeting"]
    assert len(matched) == 1
    assert matched[0]["canonical_branch"] == "audio/lens-10-15-2026-business-partner_advisoryteam-daniel"

    # 3. Dry-run auto-remediation
    dry_res = remediate_worktree(legacy_wt, dry_run=True, auto_derive=True, repo_root=test_git_repo)
    assert dry_res["status"] == "DRY_RUN_PASS"
    assert dry_res["new_branch"] == "audio/lens-10-15-2026-business-partner_advisoryteam-daniel"
    assert Path(dry_res["target_dir"]).name == "audio_lens_10_15_2026_business_partner_advisoryteam_daniel"

