"""Adversarial and edge-case tests for LiveProactiveInflectionAgent and TemporalDriftSupersessionAgent.

Covers mandatory edge-case categories:
1. Boundary & malformed input (empty turns, invalid max_steps / max_words, missing database file).
2. Invariant bypass attempts (reading *_live_transcript.md directly blocked; auto_apply=True / approval_required=False blocked).
3. Pre-egress C-19 firewall & confidence floor abstention (executive compensation band, INTERNAL_ADVISORY, and low-confidence turns blocked).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from dual_pipeline.live_meeting_recorder_core.proactive_inflection_agent import (
    LiveProactiveInflectionAgent,
    compress_for_speak_gate,
)
from dual_pipeline.staleness_core.temporal_drift_agent import (
    TemporalDriftSupersessionAgent,
)


def test_boundary_and_malformed_inputs_fail_closed(tmp_path: Path) -> None:
    """Category 1: Boundary & malformed parameters fail closed."""
    with pytest.raises(ValueError, match="max_steps"):
        LiveProactiveInflectionAgent(max_steps=0)

    with pytest.raises(ValueError, match="max_words"):
        LiveProactiveInflectionAgent(max_words=0)

    assert compress_for_speak_gate("Hello world", max_words=0) == ""

    with pytest.raises(ValueError, match="max_steps"):
        TemporalDriftSupersessionAgent(max_steps=0)

    with pytest.raises(ValueError, match="min_supersession_days"):
        TemporalDriftSupersessionAgent(min_supersession_days=0)

    td = TemporalDriftSupersessionAgent()
    with pytest.raises(FileNotFoundError):
        td.scan_temporal_drift(db_path=tmp_path / "missing.sqlite")


def test_invariant_bypass_live_transcript_read_and_auto_apply_blocked() -> None:
    """Category 2: Direct read of *_live_transcript.md and auto_apply=True raise PermissionError."""
    inf_agent = LiveProactiveInflectionAgent()
    with pytest.raises(PermissionError, match="LIVE_TRANSCRIPT_DIRECT_READ_PROHIBITED"):
        inf_agent.evaluate_live_window(
            turns=[{"speaker": "Alice", "text": "Delay migration"}],
            historical_assertions=[{"assertion_id": "a1", "claim_text": "Migration on track"}],
            transcript_file_path="output/session_01_live_transcript.md",
        )

    td_agent = TemporalDriftSupersessionAgent()
    with pytest.raises(PermissionError, match="PROPOSE_ONLY_INVARIANT_VIOLATION"):
        td_agent.scan_temporal_drift(assertions=[], auto_apply=True)

    with pytest.raises(PermissionError, match="PROPOSE_ONLY_INVARIANT_VIOLATION"):
        td_agent.scan_temporal_drift(assertions=[], approval_required=False)


def test_c19_firewall_and_low_confidence_abstention() -> None:
    """Category 3: C-19 career-leverage / INTERNAL_ADVISORY payloads and low-confidence turns are blocked."""
    inf_agent = LiveProactiveInflectionAgent(min_confidence=0.85)

    # Turn containing C-19 forbidden pattern + INTERNAL_ADVISORY assertion + low confidence turn
    turns = [
        {"speaker": "Operator", "text": "Targeting executive compensation band and career leverage on Project_X delay.", "confidence": 0.95},
        {"speaker": "Guest", "text": "We might delay Project_X delivery.", "confidence": 0.62},
    ]
    assertions = [
        {
            "assertion_id": "asrt-shadow-1",
            "claim_text": "Project_X delivery is on schedule.",
            "namespace": "INTERNAL_ADVISORY",
            "is_quarantined": 1,
        }
    ]

    res = inf_agent.evaluate_live_window(turns=turns, historical_assertions=assertions)
    assert res.status == "ABSTAINED"
    assert res.insights == []
    assert res.c19_blocked_count >= 1
    assert res.abstain_reason == "C19_QUARANTINED"
