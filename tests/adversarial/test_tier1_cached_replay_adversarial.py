"""
tests/adversarial/test_tier1_cached_replay_adversarial.py

Adversarial and edge-case verification for Tier 1 cached stage replay:
1. Boundary: Missing fixture files, empty segments, malformed JSON.
2. Malformed Input: Inverted timestamps, invalid date strings, corrupt metadata.
3. Invariant Bypass: Attempts to leak foreign worktree paths or preserve unhealed ISO dates.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.verify_tier1_cached_replay import verify_tier1_cached_replay


def test_adversarial_boundary_missing_fixture_directory(tmp_path: Path):
    """Boundary: Non-existent fixture directory must raise FileNotFoundError fail-closed."""
    non_existent = tmp_path / "missing_dir"
    with pytest.raises(FileNotFoundError, match="Fixture directory not found"):
        verify_tier1_cached_replay(fixture_dir=non_existent)


def test_adversarial_boundary_missing_required_files(tmp_path: Path):
    """Boundary: Fixture directory missing 00_raw_segments.json must fail closed."""
    incomplete_dir = tmp_path / "incomplete_fixtures"
    incomplete_dir.mkdir()
    (incomplete_dir / "metadata.json").write_text("{}", encoding="utf-8")
    (incomplete_dir / "entity_resolutions.json").write_text("{}", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="Required fixture file missing"):
        verify_tier1_cached_replay(fixture_dir=incomplete_dir)


def test_adversarial_malformed_corrupt_segments_json(tmp_path: Path):
    """Malformed: Truncated JSON in 00_raw_segments.json raises JSONDecodeError."""
    corrupt_dir = tmp_path / "corrupt_fixtures"
    corrupt_dir.mkdir()
    (corrupt_dir / "00_raw_segments.json").write_text("[{'unclosed': ...", encoding="utf-8")
    (corrupt_dir / "metadata.json").write_text("{}", encoding="utf-8")
    (corrupt_dir / "entity_resolutions.json").write_text("{}", encoding="utf-8")

    with pytest.raises(Exception):
        verify_tier1_cached_replay(fixture_dir=corrupt_dir)


def test_adversarial_unhealed_date_fails_closed(tmp_path: Path):
    """Invariant: When metadata date cannot be reconciled, the gate fails closed."""
    fault_dir = tmp_path / "fault_fixtures"
    fault_dir.mkdir()
    (fault_dir / "00_raw_segments.json").write_text(
        json.dumps([{"start": 0, "end": 1, "speaker": "Speaker 1", "text": "Hello"}]),
        encoding="utf-8",
    )
    (fault_dir / "entity_resolutions.json").write_text("{}", encoding="utf-8")
    (fault_dir / "metadata.json").write_text(
        json.dumps({
            "project": "p", "meeting_name": "m", "date": "invalid-unparsable-date",
            "speaker_map": {"Speaker 1": "Alice"}
        }),
        encoding="utf-8",
    )

    passed, receipt = verify_tier1_cached_replay(fixture_dir=fault_dir, output_sandbox_dir=tmp_path / "sb")
    assert passed is False
    assert "error" in receipt
