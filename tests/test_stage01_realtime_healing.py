"""
tests/test_stage01_realtime_healing.py

Unit tests for Stage 01 Real-Time Healing and Shift-Left Checkpoint Gatekeeper.
Verifies:
1. In-flight ISO date normalization (YYYY-MM-DD -> MM/DD/YYYY).
2. YouTube-style ASR outro hallucination purging in real time.
3. Recording gap and whitespace normalization.
4. Pre-flight healing in evaluate_stage_01_checkpoint preventing unnecessary fail-closed aborts.
5. Strict fail-closed preservation on unrecoverable 0-turn / empty dialogue.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from dual_pipeline.remediation_core import SelectiveRemediationStateMachine
from dual_pipeline.transcript_pipeline_core.stage_checkpoint import (
    evaluate_stage_01_checkpoint,
)
from dual_pipeline.transcript_stages_core.forensic_normalizer import ForensicNormalizer


def _build_sample_raw_transcript(
    date_str: str = "2026-09-18",
    include_outro: bool = False,
    include_whitespace: bool = False,
) -> str:
    ws = "   \n\n\n\n" if include_whitespace else "\n"
    body = f"""# Forensic Meeting Transcript
**Date:** {date_str}
**Time:** 12:43 PM - 01:39 PM ET
**Duration:** 56 minutes

### Attendees
- Amit Ayer (Lead Enterprise Architect, PartnerAdvisory)
- Jake Stark (Participant / LENS Lead, PartnerAdvisory)

## Transcript
### [00:01:00]
**Amit Ayer:** Good afternoon everyone, let us review the LENS status.{ws}### [00:01:30]
**Jake Stark:** Thanks Amit, the ingestion engine is progressing well.
"""
    if include_outro:
        body += """
### [00:55:00]
**PartnerAdvisory Speaker:** Thank you for watching.
"""
    body += """
## Attribution Summary
| Speaker | Attributed Identity | Confidence | Turns |
| :--- | :--- | :--- | :--- |
| Speaker 1 | Amit Ayer | HIGH | 1 |
| Speaker 2 | Jake Stark | HIGH | 1 |

## Ambiguity Lock Register
| Item | Ambiguity Key | Timestamp / Section | Canonical Value | Resolution Rationale | Confidence |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | Speaker 1 | [00:01:00] | Amit Ayer | Self-identified | 100% |
"""
    return body


def test_stage01_normalizer_heals_iso_dates_and_whitespace():
    """Verify ForensicNormalizer._post_process_forensic normalizes ISO dates and whitespace in real time."""
    normalizer = ForensicNormalizer()
    raw = _build_sample_raw_transcript(date_str="2026-09-18", include_whitespace=True)
    meta = {"meeting_date": "2026-09-18", "date": "2026-09-18"}

    healed = normalizer._post_process_forensic(raw, metadata=meta)

    # ISO date must be converted to US canonical format MM/DD/YYYY
    assert "2026-09-18" not in healed
    assert "**Date:** 09/18/2026" in healed
    # Excessive newlines must be normalized
    assert "\n\n\n\n" not in healed


def test_stage01_normalizer_purges_youtube_outro_hallucinations():
    """Verify that YouTube ASR outro hallucinations are stripped in real time during Stage 01."""
    normalizer = ForensicNormalizer()
    raw = _build_sample_raw_transcript(date_str="09/18/2026", include_outro=True)
    meta = {"meeting_date": "09/18/2026"}

    healed = normalizer._post_process_forensic(raw, metadata=meta)

    assert "Thank you for watching" not in healed
    assert "### [00:55:00]" not in healed


def test_stage01_checkpoint_preflight_heals_and_freezes():
    """Verify evaluate_stage_01_checkpoint heals candidate transcript and freezes on Attempt 1."""
    sm = SelectiveRemediationStateMachine()
    rec = MagicMock()
    notify = MagicMock()
    artifacts: dict = {}

    # Provide raw transcript with ISO date (which would fail unhealed Gate 1)
    raw_transcript = _build_sample_raw_transcript(date_str="2026-09-18", include_outro=True)
    meta = {"meeting_date": "2026-09-18", "date": "2026-09-18"}

    frozen_res = evaluate_stage_01_checkpoint(
        pipeline=MagicMock(),
        forensic_transcript=raw_transcript,
        source_text="source text placeholder",
        metadata=meta,
        state_machine=sm,
        artifacts=artifacts,
        recorder=rec,
        notify_step=notify,
        attempt=1,
        is_notes_mode=False,
    )

    # Gate 1 must pass and freeze
    assert sm.is_frozen("output_1")
    assert "2026-09-18" not in frozen_res
    assert "**Date:** 09/18/2026" in frozen_res
    assert "Thank you for watching" not in frozen_res
    assert artifacts["01_hitl_resolved_transcript.md"] == frozen_res
    assert artifacts["stage01_qa_gate1.json"]["passed"] is True


def test_stage01_checkpoint_preserves_fatal_fail_closed_on_zero_turns():
    """Verify evaluate_stage_01_checkpoint still fails closed immediately on unrecoverable 0-turn defect."""
    sm = SelectiveRemediationStateMachine()
    rec = MagicMock()
    notify = MagicMock()
    artifacts: dict = {}

    with pytest.raises(RuntimeError, match="Stage 01 Checkpoint Gate 1 FAIL-CLOSED"):
        evaluate_stage_01_checkpoint(
            pipeline=MagicMock(),
            forensic_transcript="",
            source_text="source audio text",
            metadata={},
            state_machine=sm,
            artifacts=artifacts,
            recorder=rec,
            notify_step=notify,
            attempt=1,
            is_notes_mode=False,
        )
    assert not sm.is_frozen("output_1")
