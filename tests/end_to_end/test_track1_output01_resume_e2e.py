"""
Wave 1 E2E Test: Track 1 Partial Resume (`Output 01 -> Outputs 02 & 03`) & Frozen Deliverable Immutability.

Verifies that ingesting a pre-completed `01_*_hitl_resolved_transcript.md`:
1. Auto-detects Track 1 mode (`freeze_output_1=True`, `regenerate="02,03"`, `resume=True`).
2. Preserves exact disk SHA-256 of Deliverable 01 (0 bytes modified, even under `--force-rerun`).
3. Hydrates header metadata, `### [HH:MM:SS]\n\n**Name:**` turns, Attribution Summary roles,
   and `[OPERATOR VERIFIED]` ALR locks without scraping ALR/Summary tables as fake attendees.
4. Skips `AudioSpeakerAttributionEngine.analyze_and_resolve` and `DeterministicHealer.heal_output_1`
   so `quote_pointer` character offsets remain byte-exact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from dual_pipeline.canonical_pipeline.c0_pa_runner import execute_c0_pa
from dual_pipeline.canonical_pipeline.checkpoint_memoizer import (
    _count_transcript_turns,
    _is_output_1_truncated,
    _memoize_output_1,
)
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.strategies.synthesis import FullMeetingSynthesisStrategy
from dual_pipeline.canonical_pipeline.synthesis_loop_support import (
    check_deliverable_01_deploy_action,
)
from dual_pipeline.cli.ingest import _apply_semantic_flags
from dual_pipeline.remediation_core.state_machine import SelectiveRemediationStateMachine
from dual_pipeline.transcript_stages_core.output01_parser import (
    is_completed_output_01_path,
    is_completed_output_01_text,
    parse_completed_output_01,
)
from dual_pipeline.transcript_stages_core.u0_ingestion import (
    U0IngestionBoundary,
    _extract_table_attendees,
)


SAMPLE_OUTPUT_01 = """# Forensic Normalizer — HITL Resolved Transcript

**Meeting:** Apex Global Partner Alliances Sync
**Project:** partner_alliances
**Date:** 10/20/2026
**Time:** 10:00 AM - 10:45 AM ET
**Platform:** Microsoft Teams
**Classification:** Confidential

## Attribution Summary

| Acoustic Cluster | Resolved Speaker | Role & Title | Turn Count | Evidence Basis |
| :--- | :--- | :--- | :--- | :--- |
| SPEAKER_00 | Amit Ayer | Managing Director, PartnerAdvisory | 3 | Voiceprint + Host Channel |
| SPEAKER_01 | Speaker_Alpha McElroy | Partner Alliances Lead, Google | 3 | Self-Identification + Calendar |

## Ambiguity Lock Register

| Lock ID | Cluster / Span | Resolved Speaker | Evidence Basis | Resolution Status |
| :--- | :--- | :--- | :--- | :--- |
| ALR-001 | SPEAKER_00 (00:00:05) | Amit Ayer | Acoustic Match 0.94 | [OPERATOR VERIFIED] |
| ALR-002 | SPEAKER_01 (00:01:12) | Speaker_Alpha McElroy | Contextual Turn Lock | [OPERATOR VERIFIED] |

## Resolved Verbatim Dialogue

### [00:00:05]

**Amit Ayer:** We are aligning the Q4 joint partner roadmap for Apex Global grid modernization today.

### [00:01:12]

**Speaker_Alpha McElroy:** Google Cloud committed two principal architects to validate the Vertex AI telemetry pipeline by November 15.

### [00:02:30]

**Amit Ayer:** Let us lock the architecture review milestone and assign Speaker_Alpha McElroy as the directly responsible owner.

### [00:03:15]

**Speaker_Alpha McElroy:** Confirmed. I will deliver the signed architecture blueprint before the steering committee on Friday.
"""


def test_track1_cli_auto_detects_completed_output01() -> None:
    with tempfile.TemporaryDirectory(prefix="eval_sandbox_t1_") as tmp:
        f1_path = Path(tmp) / "01_10-20-2026_1000AM_partner_alliances_hitl_resolved_transcript.md"
        f1_path.write_text(SAMPLE_OUTPUT_01, encoding="utf-8")

        args = argparse.Namespace(
            input=str(f1_path),
            document=False,
            notes=False,
            copilot_notes=False,
            output3_only=False,
            command="ingest-meeting",
            regenerate=None,
        )
        meta: dict = {}
        _apply_semantic_flags(meta, args)

        assert is_completed_output_01_path(f1_path) is True
        assert is_completed_output_01_text(SAMPLE_OUTPUT_01) is True
        assert meta["freeze_output_1"] is True
        assert meta["resume"] is True
        assert meta["regenerate"] == "02,03"
        assert meta["outputs"] == ["02", "03"]


def test_track1_output01_parser_and_u0_table_isolation() -> None:
    parsed = parse_completed_output_01(SAMPLE_OUTPUT_01)
    assert parsed["turn_count"] >= 4
    assert "Amit Ayer" in parsed["attribution_roles"]
    assert "Speaker_Alpha McElroy" in parsed["attribution_roles"]
    assert len(parsed["alr_locks"]) == 2
    assert all(lock["operator_verified"] for lock in parsed["alr_locks"])

    # Ensure _extract_table_attendees does NOT scrape ALR or Attribution Summary table columns
    scraped = _extract_table_attendees(SAMPLE_OUTPUT_01)
    for forbidden in ("SPEAKER_00 (00:00:05)", "SPEAKER_01 (00:01:12)", "ALR-001", "Voiceprint + Host Channel"):
        assert forbidden not in scraped

    # Verify multi-line `### [HH:MM:SS]\n\n**Name:**` turns are counted properly
    assert _count_transcript_turns(SAMPLE_OUTPUT_01) >= 4
    assert _is_output_1_truncated(SAMPLE_OUTPUT_01, {"turn_count": 4}) is False


def test_track1_frozen_output01_byte_immutability_and_quote_pointer_alignment() -> None:
    with tempfile.TemporaryDirectory(prefix="eval_sandbox_t1_e2e_") as tmp:
        out_dir = Path(tmp)
        f1_path = out_dir / "01_10-20-2026_1000AM_partner_alliances_hitl_resolved_transcript.md"
        f1_path.write_text(SAMPLE_OUTPUT_01, encoding="utf-8")
        pre_sha = hashlib.sha256(f1_path.read_bytes()).hexdigest()

        sm = SelectiveRemediationStateMachine()
        meta = {
            "input_path": str(f1_path),
            "freeze_output_1": True,
            "resume": True,
            "regenerate": "02,03",
            "force_rerun": True,  # Even with force_rerun, frozen Output 01 must NOT be overwritten
            "state_machine": sm,
        }

        memoized = _memoize_output_1(out_dir, sm, meta, "02,03")
        assert memoized is True
        assert sm.is_frozen("output_1") is True
        assert sm.get_frozen_content("output_1") == SAMPLE_OUTPUT_01

        # Simulate a synthesis result that attempts to mutate Output 01 by 1 byte
        mutated_synth = SimpleNamespace(output_1=SAMPLE_OUTPUT_01 + "\n<!-- healer mutation -->\n")
        should_write, content_to_write = check_deliverable_01_deploy_action(
            f1_path, out_dir, mutated_synth, meta
        )
        assert should_write is False
        assert content_to_write == SAMPLE_OUTPUT_01

        # Verify FullMeetingSynthesisStrategy._resolve_output_1 returns frozen Output 01 without invoking l2_forensic
        mock_l2_forensic = MagicMock()
        strategy = FullMeetingSynthesisStrategy(
            provider=MagicMock(),
            l2_forensic=mock_l2_forensic,
            l2_recap=MagicMock(),
            l2_graph=MagicMock(),
        )
        stage1_out = strategy._resolve_output_1(
            source_text=SAMPLE_OUTPUT_01,
            metadata=meta,
            attempt=1,
            remediation_directive=None,
            progress_notify=None,
            ckpt_dir=out_dir,
            pa_context={"state_machine": sm, "output_dir": str(out_dir)},
        )
        assert stage1_out == SAMPLE_OUTPUT_01
        mock_l2_forensic.execute.assert_not_called()

        # Verify quote_pointer exact slice alignment against disk Output 01
        target_quote = "Google Cloud committed two principal architects to validate the Vertex AI telemetry pipeline by November 15."
        start_char = SAMPLE_OUTPUT_01.index(target_quote)
        end_char = start_char + len(target_quote)
        disk_text = f1_path.read_text(encoding="utf-8")
        post_sha = hashlib.sha256(f1_path.read_bytes()).hexdigest()

        assert pre_sha == post_sha
        assert disk_text[start_char:end_char] == target_quote


def test_track1_c0_pa_skips_speaker_reattribution_on_frozen_output01() -> None:
    with tempfile.TemporaryDirectory(prefix="eval_sandbox_t1_c0_") as tmp:
        out_dir = Path(tmp)
        f1_path = out_dir / "01_10-20-2026_1000AM_partner_alliances_hitl_resolved_transcript.md"
        f1_path.write_text(SAMPLE_OUTPUT_01, encoding="utf-8")

        meta = {
            "input_path": str(f1_path),
            "freeze_output_1": True,
            "diarization_status": "DIARIZED_ACOUSTIC_RESOLVED",
            "project": "partner_alliances",
            "meeting_name": "Apex Global Partner Alliances Sync",
            "date": "10/20/2026",
            "time": "10:00 AM - 10:45 AM ET",
            "classification": "Confidential",
        }
        mock_pipeline = MagicMock()
        mock_tracker = MagicMock()
        with patch(
            "dual_pipeline.speaker_attribution_core.AudioSpeakerAttributionEngine.analyze_and_resolve"
        ) as mock_attr:
            _, res_text = execute_c0_pa(
                pipeline=mock_pipeline,
                modality=CanonicalModality.MEETING_TRANSCRIPT,
                source_text=SAMPLE_OUTPUT_01,
                input_hash="abc1234",
                meta=meta,
                presentation_path=None,
                out_p=out_dir,
                tracker=mock_tracker,
            )
            mock_attr.assert_not_called()
            assert res_text == SAMPLE_OUTPUT_01
