"""
End-to-end tests for Wave 3 (Combinations 1 & 2):
- Combination 1: Pre-completed 01_*_hitl_resolved_transcript.md -> freezes Output 1, generates Outputs 2 & 3 without re-invoking L2ForensicNormalizerExecutor.
- Combination 2: Pre-completed 01_* and 02_* -> freezes Outputs 1 & 2 via --regenerate 03, generates Output 3 without re-invoking L2ForensicNormalizerExecutor or L2ExecutiveRecapExecutor.
- Container directory --resume source path continuity (master .wav + 01_*.md).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.canonical_pipeline.content_loader import _resolve_container_source_file
from dual_pipeline.canonical_pipeline.pipeline import CanonicalAgenticPipeline
from dual_pipeline.remediation_core.state_machine import SelectiveRemediationStateMachine


def _build_valid_output_1() -> str:
    return (
        "# Forensic Transcript: Platform Sync\n"
        "**Project:** Internal Consulting\n"
        "**Date:** 10/16/2026\n"
        "**Time:** 10:00 AM\n"
        "**Attendees:** Alice Smith (PartnerAdvisory - Lead), Bob Jones (PartnerAdvisory - Architect)\n"
        "**Diarization Status:** DIARIZED_ACOUSTIC_RESOLVED\n\n"
        "---\n\n"
        "## 1. Verbatim Dialogue\n\n"
        "**[00:00:02] Alice Smith:** Let us finalize the Q4 cloud migration milestone by Friday.\n\n"
        "**[00:00:10] Bob Jones:** Agreed, I will publish the security architecture blueprint today.\n\n"
        "**[00:00:18] Alice Smith:** Great, that unblocks the production deployment wave.\n\n"
        "## 2. Ambiguity Lock & Verification Register\n\n"
        "| Term / Entity | Category | Resolution | Confidence | Source |\n"
        "| :--- | :--- | :--- | :--- | :--- |\n"
        "| Q4 cloud migration | Milestone | Verified | High | Transcript [00:00:02] |\n"
    )


def _build_valid_output_2() -> str:
    return (
        "# Executive Recap: Platform Sync\n"
        "**Project:** Internal Consulting\n"
        "**Date:** 10/16/2026\n"
        "**Attendees:** Alice Smith (PartnerAdvisory - Lead), Bob Jones (PartnerAdvisory - Architect)\n\n"
        "## 1. Meeting Objective\n\n"
        "Alice Smith and Bob Jones aligned on the Q4 cloud migration milestone and security blueprint.\n\n"
        "## 2. Action & Governance Ledger\n\n"
        "| # | Workstream | Action Item | Owner | Target Date |\n"
        "| :--- | :--- | :--- | :--- | :--- |\n"
        "| 1 | Architecture | Publish security architecture blueprint | Bob Jones | 10/16/2026 |\n\n"
        "## 3. Key Discussion Points & Findings\n\n"
        "- **Q4 Cloud Migration:** Finalize milestone by Friday ([00:00:02]).\n"
    )


def test_combo1_precompleted_output1_generates_outputs_2_and_3_only(tmp_path: Path, monkeypatch) -> None:
    """Combination 1: Pre-completed 01_*_hitl_resolved_transcript.md freezes Output 1 and runs only Outputs 2 & 3."""
    monkeypatch.setenv("TEST_SANDBOX_DIR", str(tmp_path / "sandbox"))
    out_dir = tmp_path / "combo1_out"
    out_dir.mkdir(parents=True, exist_ok=True)

    f1_path = out_dir / "01_Platform_Sync_10-16-2026_1000AM_hitl_resolved_transcript.md"
    f1_content = _build_valid_output_1()
    f1_path.write_text(f1_content, encoding="utf-8")

    pipeline = CanonicalAgenticPipeline(provider_type="offline")
    forensic_spy = MagicMock(side_effect=AssertionError("L2ForensicNormalizerExecutor must not run when 01_* is frozen"))
    pipeline.l2_forensic.execute = forensic_spy

    res = pipeline.run(
        input_path=f1_path,
        output_dir=str(out_dir),
        mutate_graph=False,
        auto_mutate_on_pass=False,
        test_fast=True,
        metadata={
            "project": "internal_consulting",
            "meeting_name": "Platform Sync",
            "date": "10/16/2026",
            "time": "10:00 AM",
            "attendees": ["Alice Smith (PartnerAdvisory - Lead)", "Bob Jones (PartnerAdvisory - Architect)"],
            "regenerate": "02,03",
            "mock": True,
            "test_fast": True,
        },
    )

    assert forensic_spy.call_count == 0
    assert res["status"] in ("COMPLETE", "PASS", "REVIEW_REQUIRED", "PARTIAL_QA_FAILURE")
    assert "02_recap" in res and "03_graph" in res
    assert (out_dir / "stage_seals.json").is_file()
    seals = json.loads((out_dir / "stage_seals.json").read_text(encoding="utf-8"))
    assert seals.get("output_1_seal") is True
    assert seals.get("output_1_pass") is True


def test_combo2_precompleted_outputs_1_and_2_regenerates_output_3_only(tmp_path: Path, monkeypatch) -> None:
    """Combination 2: Pre-completed 01_* and 02_* with --regenerate 03 freezes both and executes only Output 3."""
    monkeypatch.setenv("TEST_SANDBOX_DIR", str(tmp_path / "sandbox"))
    out_dir = tmp_path / "combo2_out"
    out_dir.mkdir(parents=True, exist_ok=True)

    f1_path = out_dir / "01_Platform_Sync_10-16-2026_1000AM_hitl_resolved_transcript.md"
    f2_path = out_dir / "02_Platform_Sync_10-16-2026_1000AM_executive_recap.md"
    f1_path.write_text(_build_valid_output_1(), encoding="utf-8")
    f2_path.write_text(_build_valid_output_2(), encoding="utf-8")

    sm = SelectiveRemediationStateMachine(max_attempts=1, fail_fast=True, checkpoint_dir=out_dir)
    meta = {
        "project": "internal_consulting",
        "meeting_name": "Platform Sync",
        "date": "10/16/2026",
        "attendees": ["Alice Smith", "Bob Jones"],
        "regenerate": "03",
    }
    seeded = sm.seed_for_selective_regeneration(out_dir, "03", metadata=meta)
    assert seeded["output_1"].valid is True
    assert seeded["output_2"].valid is True
    assert sm.is_frozen("output_1") is True
    assert sm.is_frozen("output_2") is True
    assert sm.is_frozen("output_3") is False

    pipeline = CanonicalAgenticPipeline(provider_type="offline")
    forensic_spy = MagicMock(side_effect=AssertionError("L2ForensicNormalizerExecutor must not run in Combo 2"))
    recap_spy = MagicMock(side_effect=AssertionError("L2ExecutiveRecapExecutor must not run in Combo 2"))
    pipeline.l2_forensic.execute = forensic_spy
    pipeline.l2_recap.execute = recap_spy

    res = pipeline.run(
        input_path=f1_path,
        output_dir=str(out_dir),
        mutate_graph=False,
        auto_mutate_on_pass=False,
        test_fast=True,
        metadata={
            "project": "internal_consulting",
            "meeting_name": "Platform Sync",
            "date": "10/16/2026",
            "time": "10:00 AM",
            "attendees": ["Alice Smith (PartnerAdvisory - Lead)", "Bob Jones (PartnerAdvisory - Architect)"],
            "regenerate": "03",
            "mock": True,
            "test_fast": True,
        },
    )

    assert forensic_spy.call_count == 0
    assert recap_spy.call_count == 0
    assert res is not None


def test_container_resume_preserves_original_wav_source_path(tmp_path: Path) -> None:
    """Verifies _resolve_container_source_file honors original master WAV on --resume even after 01_*.md is emitted."""
    container = tmp_path / "meeting_container"
    container.mkdir(parents=True, exist_ok=True)
    wav_file = container / "00_test_master_recording.wav"
    wav_file.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")
    wav_sha = hashlib.sha256(wav_file.read_bytes()).hexdigest()

    # Initial resolution before 01_*.md exists returns wav_file
    initial_src = _resolve_container_source_file(container, {"resume": False})
    assert initial_src == wav_file

    # Emit 01_*.md and checkpoint into container
    f1_file = container / "01_test_hitl_resolved_transcript.md"
    f1_file.write_text(_build_valid_output_1(), encoding="utf-8")
    (container / "metadata.json").write_text(
        json.dumps({"original_source_path": str(wav_file), "source_file_hash": wav_sha}),
        encoding="utf-8",
    )

    # With resume=True, _resolve_container_source_file returns the pinned wav_file instead of switching to 01_*.md
    resumed_src = _resolve_container_source_file(container, {"resume": True})
    assert resumed_src == wav_file
