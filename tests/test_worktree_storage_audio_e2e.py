"""
End-to-end and boundary tests for Wave 2 storage, audio, and dirty file guards.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.pr_governance_core.audio_repatriation import (
    REPATRIATED_SIDECAR_SUFFIXES,
    repatriate_worktree_audio_artifacts,
)
from tools.pr_governance_core.worktree import (
    find_valuable_ignored_files,
    is_ephemeral_worktree_path,
    partition_worktree_dirty_files,
)


def test_sqlite_wal_not_treated_as_ephemeral() -> None:
    # Verifies Detective 5 / Invariant fix: .sqlite-wal is NOT ephemeral
    assert is_ephemeral_worktree_path("storage/graph.sqlite-wal") is False
    assert is_ephemeral_worktree_path(".local/state.sqlite-wal") is False
    # Transient test/run artifacts remain ephemeral
    assert is_ephemeral_worktree_path("output/test_run/attempts/attempt_1.json") is True


def test_audio_sidecars_include_voiceprints_and_speaker_map() -> None:
    assert ".voiceprints.json" in REPATRIATED_SIDECAR_SUFFIXES
    assert "speaker_map.json" in REPATRIATED_SIDECAR_SUFFIXES


def test_audio_repatriation_preserves_sidecars(tmp_path: Path) -> None:
    root = tmp_path / "root"
    wt = tmp_path / "wt"
    root.mkdir()
    wt.mkdir()

    wt_cache = wt / "output" / "transcript_cache" / "20261022_120000"
    wt_cache.mkdir(parents=True)

    master_wav = wt_cache / "master_audio.wav"
    master_wav.write_bytes(b"RIFF" + b"\x00" * 50)  # > 45 bytes

    vprints = wt_cache / "master_audio.voiceprints.json"
    vprints.write_text('{"speakers": ["Speaker 1"]}', encoding="utf-8")

    speaker_map = wt_cache / "speaker_map.json"
    speaker_map.write_text('{"Speaker 1": "Amit"}', encoding="utf-8")

    res = repatriate_worktree_audio_artifacts(wt, root)
    assert res.success is True

    root_cache = root / "output" / "transcript_cache" / "20261022_120000"
    assert (root_cache / "master_audio.wav").is_file()
    assert (root_cache / "master_audio.voiceprints.json").is_file()
    assert (root_cache / "speaker_map.json").is_file()
    assert (root_cache / "speaker_map.json").read_text(encoding="utf-8") == '{"Speaker 1": "Amit"}'


def test_partition_worktree_dirty_files() -> None:
    paths = [
        "dual_pipeline/core.py",
        "output/project_lens/10-21-2026/Debrief/01_transcript.md",
        "output/test/attempts/attempt_1.json",
        "random_notes.txt",
    ]
    parts = partition_worktree_dirty_files(paths)
    assert "dual_pipeline/core.py" in parts["authored_code_files"]
    assert "output/project_lens/10-21-2026/Debrief/01_transcript.md" in parts["deliverable_files"]
    assert "output/test/attempts/attempt_1.json" in parts["ephemeral_attempt_files"]
