"""
tests/test_live_collateral_repoint_and_hydration.py

Wave 1 test suite validating:
1. CollateralFsWatcher records trusted drop entries (source: 'live_drop', drop_event_id)
   with non-zero elapsed timestamps that pass is_authenticated_drop().
2. CollateralFsWatcher.repoint() correctly migrates watched directories, drop logs,
   and staged files.
3. repoint_recorder_paths() repoints collateral paths and invokes watcher repoint.
4. hydrate_live_container_artifacts() auto-hydrates attachment_dir and collateral_drop_log.
5. build_canonical_ingest_command() emits --attachment-dir when referenced_artifacts exist.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dual_pipeline.attachment_core.review_provenance import is_authenticated_drop
from dual_pipeline.canonical_pipeline.u0_ingress import hydrate_live_container_artifacts
from dual_pipeline.cli.canonical_invocation import build_canonical_ingest_command
from dual_pipeline.live_meeting_recorder_core.collateral_watcher import CollateralFsWatcher
from dual_pipeline.live_meeting_recorder_core.worktree_repointer import repoint_recorder_paths


def test_collateral_watcher_records_trusted_drops_and_authenticates(tmp_path: Path) -> None:
    ref_dir = tmp_path / "referenced_artifacts"
    ref_dir.mkdir(parents=True)
    log_path = tmp_path / "collateral_drop_log.json"

    # Simulate session clock at 1470.5s (24m 30s)
    current_elapsed = 1470.5
    watcher = CollateralFsWatcher(
        ref_dir=ref_dir,
        get_elapsed_fn=lambda: current_elapsed,
        log_path=log_path,
    )

    # Drop a simulated screenshot
    img_file = ref_dir / "lens_admin_dashboard.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\nfake_image_bytes_12345")

    # Perform scan
    watcher._scan()

    assert len(watcher.drops) == 1
    drop = watcher.drops[0]
    assert drop["filename"] == "lens_admin_dashboard.png"
    assert drop["dropped_at_elapsed"] == "00:24:30"
    assert drop["source"] == "live_drop"
    assert drop["drop_event_id"].startswith("drop-0001-")
    assert drop["attribution_mode"] == "COLLATERAL_REVIEWED"

    # Verify drop passes authentication in review_provenance
    assert is_authenticated_drop(drop) is True

    # Verify log was written to disk
    assert log_path.is_file()
    saved = json.loads(log_path.read_text(encoding="utf-8"))
    assert len(saved) == 1
    assert saved[0]["dropped_at_elapsed"] == "00:24:30"
    assert is_authenticated_drop(saved[0]) is True


def test_collateral_watcher_repoint_migrates_files_and_drops(tmp_path: Path) -> None:
    old_dir = tmp_path / "staging" / "referenced_artifacts"
    old_dir.mkdir(parents=True)
    old_log = tmp_path / "staging" / "collateral_drop_log.json"

    watcher = CollateralFsWatcher(
        ref_dir=old_dir,
        get_elapsed_fn=lambda: 600.0,
        log_path=old_log,
    )

    # Create a pre-repoint drop
    drop_file = old_dir / "slide_deck.pdf"
    drop_file.write_bytes(b"%PDF-1.4 mock content")
    watcher._scan()
    assert len(watcher.drops) == 1

    # Now repoint to worktree
    new_dir = tmp_path / "worktree" / "output" / "referenced_artifacts"
    new_log = tmp_path / "worktree" / "output" / "collateral_drop_log.json"

    watcher.repoint(new_ref_dir=new_dir, new_log_path=new_log)

    # Verify file was migrated to new_dir
    migrated_file = new_dir / "slide_deck.pdf"
    assert migrated_file.is_file()
    assert migrated_file.read_bytes() == b"%PDF-1.4 mock content"

    # Verify log was flushed to new_log
    assert new_log.is_file()
    new_saved = json.loads(new_log.read_text(encoding="utf-8"))
    assert len(new_saved) == 1
    assert new_saved[0]["filename"] == "slide_deck.pdf"


def test_repoint_recorder_paths_updates_collateral_subsystem(tmp_path: Path) -> None:
    staging_dir = tmp_path / "staging"
    staging_ref = staging_dir / "referenced_artifacts"
    staging_ref.mkdir(parents=True)
    staging_log = staging_dir / "collateral_drop_log.json"

    worktree_out = tmp_path / "worktree" / "output"
    worktree_cache = tmp_path / "worktree" / "output" / ".cache"
    worktree_chunks = tmp_path / "worktree" / "output" / "chunks"

    mock_watcher = MagicMock()

    class MockRecorder:
        def __init__(self) -> None:
            self.date_dirname = "10-16-2026"
            self.clean_slug = "ed_amit"
            self.session_tag = "120000"
            self._target_output_dir = worktree_out
            self._target_cache_dir = worktree_cache
            self._target_chunks_dir = worktree_chunks
            self.output_dir = staging_dir
            self.cache_dir = staging_dir
            self.chunks_dir = staging_dir
            self.collateral_ref_dir = staging_ref
            self.collateral_drop_log = staging_log
            self.collateral_watcher = mock_watcher

    rec = MockRecorder()
    repoint_recorder_paths(rec)

    assert rec.output_dir == worktree_out
    assert rec.collateral_ref_dir == worktree_out / "referenced_artifacts"
    assert rec.collateral_drop_log == worktree_out / "collateral_drop_log.json"
    mock_watcher.repoint.assert_called_once_with(
        new_ref_dir=worktree_out / "referenced_artifacts",
        new_log_path=worktree_out / "collateral_drop_log.json",
    )


def test_hydrate_live_container_artifacts_discovers_attachments(tmp_path: Path) -> None:
    container_dir = tmp_path / "output" / "project_lens" / "10-16-2026" / "Ed_Amit"
    ref_dir = container_dir / "referenced_artifacts"
    ref_dir.mkdir(parents=True)

    img = ref_dir / "lens_review_queue.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\ntest")

    drop_log = container_dir / "collateral_drop_log.json"
    drop_log.write_text(json.dumps([{"filename": "lens_review_queue.png", "attribution_mode": "COLLATERAL_REVIEWED"}]), encoding="utf-8")

    manifest = container_dir / "00_10-16-2026_ed_amit_120000_manifest.json"
    manifest.write_text(json.dumps({"meeting": "Ed Amit", "project": "project_lens", "date": "10-16-2026"}), encoding="utf-8")

    wav = container_dir / "00_10-16-2026_ed_amit_master_120000.wav"
    wav.write_bytes(b"RIFFdummywav")

    metadata: dict = {"input_path": str(wav)}
    hydrated = hydrate_live_container_artifacts(metadata, input_path=str(wav))

    assert "attachment_dir" in hydrated
    assert metadata["attachment_dir"] == str(ref_dir)
    assert "collateral_drop_log" in hydrated
    assert metadata["collateral_drop_log_path"] == str(drop_log)


def test_build_canonical_ingest_command_emits_attachment_dir(tmp_path: Path) -> None:
    container_dir = tmp_path / "output" / "project_lens" / "10-16-2026" / "Ed_Amit"
    ref_dir = container_dir / "referenced_artifacts"
    ref_dir.mkdir(parents=True)
    (ref_dir / "lens_table.png").write_bytes(b"\x89PNGfake")

    wav = container_dir / "00_10-16-2026_ed_amit_master_120000.wav"
    wav.write_bytes(b"RIFFfake")
    (container_dir / "00_manifest.json").write_text("{}", encoding="utf-8")

    cmd = build_canonical_ingest_command(
        input_path=wav,
        project="project_lens",
        meeting="Ed Amit",
        date="10-16-2026",
    )

    assert f'--attachment-dir "{ref_dir}"' in cmd or "--attachment-dir" in cmd
