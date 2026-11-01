"""
tests/test_stage_coordinator_checkpoint.py

Unit tests for Stage 01 Shift-Left Checkpoint Gatekeeper.
Verifies fail-fast behavior on 0-turn / invalid transcripts and freezing on valid transcripts.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from dual_pipeline.remediation_core import SelectiveRemediationStateMachine
from dual_pipeline.transcript_pipeline_core.stage_checkpoint import (
    evaluate_stage_01_checkpoint,
    is_fatal_stage_01_defect,
)


def test_is_fatal_stage_01_defect():
    assert is_fatal_stage_01_defect(["Gate 1.6: turn density failure: 0 turns detected"])
    assert is_fatal_stage_01_defect(["Output 1 contains 0 dialogue turns"])
    assert not is_fatal_stage_01_defect(["Gate 1.3: header date format mismatch"])


def test_checkpoint_notes_mode_skips():
    sm = SelectiveRemediationStateMachine()
    rec = MagicMock()
    notify = MagicMock()
    artifacts = {}

    res = evaluate_stage_01_checkpoint(
        pipeline=MagicMock(),
        forensic_transcript="some text",
        source_text="some text",
        metadata={"notes_mode": True},
        state_machine=sm,
        artifacts=artifacts,
        recorder=rec,
        notify_step=notify,
        attempt=1,
        is_notes_mode=True,
    )
    assert res == ""
    assert not sm.is_frozen("output_1")


def test_checkpoint_fail_fast_on_zero_turns():
    sm = SelectiveRemediationStateMachine()
    rec = MagicMock()
    notify = MagicMock()
    artifacts = {}

    # Empty transcript triggers immediate fail-closed
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


def test_checkpoint_freezes_valid_transcript():
    sm = SelectiveRemediationStateMachine()
    rec = MagicMock()
    notify = MagicMock()
    artifacts = {}

    valid_f1 = """# Forensic Meeting Transcript
**Date:** 09/18/2026
**Time:** 12:43 PM - 12:45 PM ET
**Duration:** 2 minutes

### Attendees
- Amit Ayer (Principal Architect & Technology Strategist)
- Jake Stark (UI and Prompting Lead)

## Transcript

### [00:01:00]
**Amit Ayer:** Good afternoon everyone, let us review the LENS status.

### [00:01:30]
**Jake Stark:** Thanks Amit, the ingestion engine is progressing well.

## Attribution Summary

| Speaker | Org | Role | Turns Attributed | Confidence Tier | Primary Anchor Cue |
|---|---|---|---|---|---|
| Amit Ayer | PartnerAdvisory | Lead Enterprise Architect / Lead Client Partner | 1 | HIGH | Named introduction, technical contribution, and forensic acoustic identification |
| Jake Stark | PartnerAdvisory | UI and Prompting Lead | 1 | HIGH | Named introduction, technical contribution, and forensic acoustic identification |

## Ambiguity Lock Register
| Item | Ambiguity Key | Timestamp / Section | Canonical Value | Resolution Rationale | Confidence |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | Amit Ayer | [00:01:00] | Amit Ayer | Speaker attribution confirmed via self-identification | HIGH (95%) |
| 2 | Jake Stark | [00:01:30] | Jake Stark | Speaker attribution confirmed via direct address | HIGH (95%) |
| 3 | LENS | [00:01:00] | Project LENS | Project name | HIGH (95%) |
"""
    res = evaluate_stage_01_checkpoint(
        pipeline=MagicMock(),
        forensic_transcript=valid_f1,
        source_text=valid_f1,
        metadata={"attendees": {"PartnerAdvisory": ["Amit Ayer", "Jake Stark"]}},
        state_machine=sm,
        artifacts=artifacts,
        recorder=rec,
        notify_step=notify,
        attempt=1,
        is_notes_mode=False,
    )
    assert res == valid_f1
    assert sm.is_frozen("output_1")
    assert sm.get_frozen_content("output_1") == valid_f1

    # Second call uses frozen cache directly without calling Gate 1 again
    res2 = evaluate_stage_01_checkpoint(
        pipeline=MagicMock(),
        forensic_transcript="mutated_invalid_text",
        source_text="source text",
        metadata={},
        state_machine=sm,
        artifacts=artifacts,
        recorder=rec,
        notify_step=notify,
        attempt=2,
        is_notes_mode=False,
    )
    assert res2 == valid_f1
