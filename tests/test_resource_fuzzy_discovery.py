"""
tests/test_resource_fuzzy_discovery.py

Unit tests for non-hanging bounded fuzzy resource discovery and Stage U0 fallback.
Verifies sub-second resolution, zero .worktrees traversal, timeout safety, and HITL escalation.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
import pytest

from dual_pipeline.canonical_pipeline.resource_discovery import (
    PRUNED_DIR_NAMES,
    ResourceDiscoveryResult,
    discover_resource_fuzzy,
    score_resource_candidate,
)
from dual_pipeline.exceptions import HITLRequiredError


def test_pruned_directories_include_worktrees_and_env():
    """Verify high-churn directory trees are strictly pruned from discovery traversal."""
    assert ".worktrees" in PRUNED_DIR_NAMES
    assert ".git" in PRUNED_DIR_NAMES
    assert ".venv" in PRUNED_DIR_NAMES
    assert ".local" in PRUNED_DIR_NAMES
    assert "node_modules" in PRUNED_DIR_NAMES


def test_exact_existing_file_resolves_instantly(tmp_path: Path):
    """If exact file exists, it returns immediately with score 1.0."""
    sample = tmp_path / "meeting_recording.mp3"
    sample.write_text("dummy audio", encoding="utf-8")

    res = discover_resource_fuzzy(str(sample))
    assert res.best_match is not None
    assert res.best_match.path == sample.resolve()
    assert res.best_match.score == 1.0
    assert res.best_match.is_exact_stem is True
    assert res.confidence_margin == 1.0
    assert not res.requires_hitl


def test_missing_wav_resolves_to_source_transcript_and_metadata():
    """Missing .wav stem resolves to authentic kickoff markdown transcript and co-located metadata."""
    query = "00_10-07-2026_Kickoff_Jill_PartnerAdvisory_master_101752.wav"
    t0 = time.monotonic()
    res = discover_resource_fuzzy(query, project="project_nexus")
    elapsed = time.monotonic() - t0

    assert elapsed < 0.50, f"Discovery exceeded 500ms budget: {elapsed:.3f}s"
    assert res.best_match is not None
    assert "Jarvis_Kickoff_Jill_PartnerAdvisory" in res.best_match.path.name
    assert res.best_match.metadata_path is not None
    assert "jarvis_kickoff_metadata.json" in res.best_match.metadata_path.name
    assert res.best_match.score >= 0.70
    assert not res.requires_hitl


def test_timeout_and_max_files_enforcement(tmp_path: Path):
    """Hard deadline and max_files prevent runaway filesystem walks."""
    deep_dir = tmp_path / "tree"
    for i in range(50):
        sub = deep_dir / f"folder_{i}"
        sub.mkdir(parents=True)
        (sub / f"dummy_transcript_{i}.md").write_text("text", encoding="utf-8")

    t0 = time.monotonic()
    res = discover_resource_fuzzy(
        "nonexistent_meeting.wav",
        search_roots=[deep_dir],
        timeout_s=0.05,
        max_files=10,
    )
    elapsed = time.monotonic() - t0

    assert elapsed < 0.50
    assert res.files_scanned <= 15  # Halts near max_files


def test_ambiguous_matches_trigger_hitl(tmp_path: Path):
    """When two candidates have close scores (<30% delta), requires_hitl is true."""
    root = tmp_path / "inputs"
    root.mkdir()
    f1 = root / "Architecture_Review_10-01-2026_source.md"
    f2 = root / "Architecture_Review_10-02-2026_source.md"
    f1.write_text("review 1", encoding="utf-8")
    f2.write_text("review 2", encoding="utf-8")

    res = discover_resource_fuzzy(
        "Architecture_Review.wav",
        search_roots=[root],
    )
    assert len(res.candidates) >= 2
    assert res.confidence_margin <= 0.30
    assert res.requires_hitl is True


def test_ingress_inspect_file_target_fuzzy_resolution(tmp_path: Path):
    """Verify _inspect_file_target in ingress resolves missing path via fuzzy discovery."""
    from dual_pipeline.transcript_pipeline_core.ingress import _inspect_file_target

    meta = {"project": "project_nexus"}
    is_file, input_p, input_ext, hash_val = _inspect_file_target(
        "00_10-07-2026_Kickoff_Jill_PartnerAdvisory_master_101752.wav",
        meta,
    )
    assert is_file is True
    assert input_p is not None
    assert "Jarvis_Kickoff_Jill_PartnerAdvisory" in input_p.name
    assert input_ext == ".md"
    assert "_colocated_metadata" in meta


def test_cli_validate_canonical_input_fuzzy_resolution():
    """Verify _validate_canonical_input in CLI resolves missing input and pairs metadata."""
    import argparse
    from dual_pipeline.cli.ingest import _validate_canonical_input

    args = argparse.Namespace(
        input="00_10-07-2026_Kickoff_Jill_PartnerAdvisory_master_101752.wav",
        project="project_nexus",
    )
    resolved_p = _validate_canonical_input(args)
    assert resolved_p.is_file()
    assert "Jarvis_Kickoff_Jill_PartnerAdvisory" in resolved_p.name
    assert getattr(args, "metadata", None) is not None
    assert "jarvis_kickoff_metadata.json" in str(args.metadata)


def test_cli_ambiguous_input_raises_hitl_required_error(tmp_path: Path):
    """Verify _validate_canonical_input raises HITLRequiredError on ambiguous candidates."""
    import argparse
    from dual_pipeline.cli.ingest import _validate_canonical_input

    # Create synthetic ambiguous candidates
    in_dir = tmp_path / "inputs" / "ambig_proj"
    in_dir.mkdir(parents=True)
    (in_dir / "Design_Sync_10-01-2026_notes.md").write_text("sync 1", encoding="utf-8")
    (in_dir / "Design_Sync_10-02-2026_notes.md").write_text("sync 2", encoding="utf-8")

    from unittest.mock import patch
    with patch("dual_pipeline.canonical_pipeline.resource_discovery.Path.cwd", return_value=tmp_path):
        args = argparse.Namespace(
            input="Design_Sync.wav",
            project="ambig_proj",
        )
        with pytest.raises(HITLRequiredError) as exc_info:
            _validate_canonical_input(args)
        assert exc_info.value.hitl_type == "resource_resolution"
        assert len(exc_info.value.options) >= 2
