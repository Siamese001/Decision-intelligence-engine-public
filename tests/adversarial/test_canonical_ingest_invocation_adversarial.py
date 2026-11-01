"""
Adversarial and boundary test suite for canonical ingestion invocation binding.
Covers boundary payloads, malformed inputs, invariant bypass attempts,
and exact production regression reproduction under e2e-and-edge-case-coverage.md.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.core import ROOT
from dual_pipeline.cli.canonical_invocation import build_canonical_ingest_command
from dual_pipeline.cli.parser import build_parser
from dual_pipeline.cli.ingest import _extract_flag_attributes
from dual_pipeline.canonical_pipeline.worktree_inheritance import (
    inherit_worktree_from_metadata,
    resolve_inherited_target_path,
)


def test_boundary_empty_and_whitespace_slugs():
    """Boundary: Verify empty or whitespace slug arguments do not pollute emitted command."""
    cmd_empty = build_canonical_ingest_command(
        input_path="/tmp/a.wav", project="p", meeting="m", date="01/01/2026",
        filename_slug="", worktree_dir="",
    )
    assert "--filename-slug" not in cmd_empty
    assert "--worktree-dir" not in cmd_empty

    cmd_none = build_canonical_ingest_command(
        input_path="/tmp/a.wav", project="p", meeting="m", date="01/01/2026",
        filename_slug=None, worktree_dir=None,
    )
    assert "--filename-slug" not in cmd_none
    assert "--worktree-dir" not in cmd_none


def test_boundary_nonexistent_worktree_dir_handling(tmp_path):
    """Boundary: Non-existent worktree path must return None from inherit_worktree_from_metadata."""
    non_existent = tmp_path / "does_not_exist_wt"
    meta = {"worktree_dir": str(non_existent)}
    inherited = inherit_worktree_from_metadata(meta, repo_root=tmp_path)
    assert inherited is None


def test_malformed_slug_and_path_normalization(tmp_path):
    """Malformed: Path with redundant slashes and dots is normalized properly."""
    fake_wt = tmp_path / ".worktrees" / "live_standup_amit"
    fake_wt.mkdir(parents=True, exist_ok=True)
    (fake_wt / ".git").write_text("gitdir: ...")

    # Pass relative path with redundant current dir components
    raw_wt_str = "./.worktrees/live_standup_amit/."
    meta = {"worktree_dir": raw_wt_str}
    inherited = inherit_worktree_from_metadata(meta, repo_root=tmp_path)
    assert inherited is not None
    wt_dir, _ = inherited
    assert wt_dir.resolve() == fake_wt.resolve()


def test_invariant_bypass_prevented_by_locked_filename_slug(tmp_path):
    """Invariant bypass: Existing different subfolder in date_dir must NOT override explicit filename_slug."""
    fake_wt = tmp_path / ".worktrees" / "live_standup_amit"
    date_dir = fake_wt / "output" / "project_nexus" / "2026-10-09"
    date_dir.mkdir(parents=True, exist_ok=True)
    # Simulate a drift directory that would otherwise be fuzzy-matched
    drift_dir = date_dir / "Standup_Amit_Nozim_Extra"
    drift_dir.mkdir()
    (drift_dir / "01_2026-10-09_Standup_Amit_Nozim_Extra_live_transcript.md").touch()

    meta = {
        "worktree_dir": str(fake_wt),
        "filename_slug": "Standup_Amit",  # Explicit locked slug
    }

    target = resolve_inherited_target_path(
        meta=meta,
        proj="project_nexus",
        date_fn="2026-10-09",
        slug="Standup_Amit",
        no_subfolder=False,
        repo_root=tmp_path,
    )
    # Must preserve exact explicit slug "Standup_Amit", NOT drift_dir "Standup_Amit_Nozim_Extra"
    assert meta["filename_slug"] == "Standup_Amit"
    assert target == date_dir / "Standup_Amit"


def test_exact_production_regression_no_bifurcated_worktree_spawn(tmp_path):
    """
    Exact production regression:
    Given a recording concluding in .worktrees/live_standup_amit with slug Standup_Amit,
    verify that parsing and resolving the emitted command routes strictly into
    the existing worktree without calling ensure_live_transcription_worktree to spawn a 2nd branch.
    """
    fake_wt = tmp_path / ".worktrees" / "live_standup_amit"
    fake_wt.mkdir(parents=True, exist_ok=True)
    (fake_wt / ".git").write_text("gitdir: ...")

    emitted_cmd = build_canonical_ingest_command(
        input_path=fake_wt / "output" / "00_master.wav",
        project="project_nexus",
        meeting="Standup Amit",
        date="10/09/2026",
        attendees="Amit, Nozim, Speaker_Beta, Speaker_Alpha",
        filename_slug="Standup_Amit",
        worktree_dir=fake_wt,
    )

    # Verify flags present
    assert '--filename-slug "Standup_Amit"' in emitted_cmd
    assert f'--worktree-dir "{fake_wt}"' in emitted_cmd

    # Parse command as CLI args
    import shlex
    parser = build_parser()
    # Strip 'nee' prefix
    cli_tokens = shlex.split(emitted_cmd)[1:]
    args = parser.parse_args(cli_tokens)

    meta = {}
    _extract_flag_attributes(args, meta)
    assert meta["worktree_dir"] == str(fake_wt)
    assert meta["filename_slug"] == "Standup_Amit"

    # Verify that worktree inheritance catches it and avoids provisioning new worktrees
    with patch("dual_pipeline.live_meeting_recorder_core.worktree_manager._provision_git_worktree") as mock_prov:
        target = resolve_inherited_target_path(
            meta=meta,
            proj="project_nexus",
            date_fn="2026-10-09",
            slug=meta["filename_slug"],
            repo_root=tmp_path,
        )
        assert mock_prov.call_count == 0
        assert target.is_relative_to(fake_wt)
        assert target == fake_wt / "output" / "project_nexus" / "2026-10-09" / "Standup_Amit"


def test_adversarial_nested_worktree_doubling_prevented_when_invoked_inside_worktree(tmp_path):
    """BUG-09 regression: Relative --worktree-dir .worktrees/<slug> invoked inside .worktrees/<slug> must never double."""
    fake_wt = tmp_path / ".worktrees" / "live_10_16_2026_ed_amit"
    fake_wt.mkdir(parents=True, exist_ok=True)
    (fake_wt / ".git").write_text("gitdir: ...")

    # Simulate invoking with relative worktree_dir when repo_root IS already inside fake_wt
    meta = {"worktree_dir": ".worktrees/live_10_16_2026_ed_amit"}
    inherited = inherit_worktree_from_metadata(meta, repo_root=fake_wt)
    assert inherited is not None
    wt_dir, _ = inherited
    assert wt_dir == fake_wt.resolve()
    assert ".worktrees/live_10_16_2026_ed_amit/.worktrees" not in str(wt_dir)


def test_adversarial_rbac_operator_deferred_hitl_vs_untrusted_rejection():
    """BUG-08 regression: reviewer='operator' enters DEFERRED_HITL_APPROVAL without fake receipt; untrusted fails closed."""
    import argparse
    from dual_pipeline.cli.ingest import _resolve_ingest_rbac_authorization
    from dual_pipeline.rbac import RBACAuthorizationError

    args = argparse.Namespace(auth_token=None, auto_auth=True)
    meta_op = {}
    _resolve_ingest_rbac_authorization(args, "operator", "Ingest meeting", meta_op)
    assert meta_op["rbac_auth"]["auth_type"] == "DEFERRED_HITL_APPROVAL"
    assert meta_op["rbac_auth"]["authorized"] is False
    assert "approval_receipt" not in meta_op

    meta_untrusted = {}
    with pytest.raises(RBACAuthorizationError):
        _resolve_ingest_rbac_authorization(args, "Mallory", "Ingest meeting", meta_untrusted)


def test_adversarial_binary_wav_skipped_in_speaker_collapse_precheck(tmp_path):
    """BUG-08 regression: _assert_no_speaker_collapse must skip binary audio files."""
    from dual_pipeline.cli.ingest import _assert_no_speaker_collapse

    wav_file = tmp_path / "00_10-16-2026_Ed_Amit_master.wav"
    # Write text that would trigger collapse if read as transcript text
    wav_file.write_text("\n".join([f"**Speaker 1** [00:00:{i:02d}]: hello world" for i in range(20)]), encoding="utf-8")
    meta = {"attendees": [{"name": "Eduardo More"}, {"name": "Amit Ayer"}]}
    # Should return cleanly without inspecting binary audio extension
    _assert_no_speaker_collapse(str(wav_file), meta)


def test_adversarial_worktree_resource_discovery_from_primary_root(tmp_path):
    """BUG-10 regression: discover_resource_fuzzy resolves audio inside worktree_dir without full .worktrees scan."""
    from dual_pipeline.canonical_pipeline.resource_discovery import discover_resource_fuzzy

    wt_dir = tmp_path / ".worktrees" / "live_10_16_2026_ed_amit"
    cache_dir = wt_dir / "output" / "transcript_cache" / "project_lens" / "10-16-2026"
    cache_dir.mkdir(parents=True, exist_ok=True)
    wav_file = cache_dir / "00_10-16-2026_Ed_Amit_master.wav"
    wav_file.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32)

    rel_query = "output/project_lens/10-16-2026/10-16-2026_Ed_Amit/00_10-16-2026_Ed_Amit_master.wav"
    res = discover_resource_fuzzy(
        rel_query,
        repo_root=tmp_path,
        project="project_lens",
        date="10-16-2026",
        worktree_dir=Path(".worktrees/live_10_16_2026_ed_amit"),
    )
    assert res.best_match is not None
    assert res.best_match.path.resolve() == wav_file.resolve()

