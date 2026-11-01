"""Adversarial and edge-case tests for CounterfactualDevilAdvocateAgent and CrossArtifactReconciliationAgent.

Covers mandatory edge-case categories:
1. Boundary & malformed input (empty options list, empty Output 01 transcript, invalid max_steps).
2. Invariant bypass attempts (candidate-lane promotion blocked; Output 01 speaker mutation blocked).
3. Diarization speaker collapse (>85% single speaker share escalates to HITL and blocks autonomous parity certification).
"""
from __future__ import annotations

import pytest

from dual_pipeline.decision_engine_core.devil_advocate_agent import (
    CounterfactualDevilAdvocateAgent,
)
from dual_pipeline.transcript_qa_core.reconciliation_agent import (
    CrossArtifactReconciliationAgent,
)


def test_boundary_and_malformed_inputs_fail_closed() -> None:
    """Category 1: Boundary & malformed input rejection."""
    with pytest.raises(ValueError, match="max_steps"):
        CounterfactualDevilAdvocateAgent(max_steps=0)

    with pytest.raises(ValueError, match="max_tool_calls"):
        CounterfactualDevilAdvocateAgent(max_tool_calls=0)

    da = CounterfactualDevilAdvocateAgent()
    with pytest.raises(ValueError, match="At least one candidate option"):
        da.challenge(options=[], evidence_records=[])

    with pytest.raises(ValueError, match="max_steps"):
        CrossArtifactReconciliationAgent(max_steps=0)

    rec = CrossArtifactReconciliationAgent()
    with pytest.raises(ValueError, match="non-empty"):
        rec.reconcile(output_01_text="   ", output_02_text="notes", output_03_text="summary")


def test_invariant_bypass_candidate_lane_and_output_01_mutation_blocked() -> None:
    """Category 2: Candidate-lane promotion and Output 01 speaker mutation raise PermissionError."""
    da = CounterfactualDevilAdvocateAgent()
    with pytest.raises(PermissionError, match="CANDIDATE_LANE_VIOLATION"):
        da.challenge(
            options=[{"option_id": "opt_1", "label": "Option 1"}],
            evidence_records=[],
            allow_candidate_as_fact=True,
        )

    rec = CrossArtifactReconciliationAgent()
    with pytest.raises(PermissionError, match="OUTPUT_01_SPEAKER_ATTRIBUTION_FROZEN"):
        rec.reconcile(
            output_01_text="Alice Rivera: Hello team.",
            output_02_text="Notes",
            output_03_text="Summary",
            allow_output_01_mutation=True,
        )


def test_diarization_speaker_collapse_escalates_to_hitl() -> None:
    """Category 3: Single-speaker turn share > 85% flags speaker collapse and requires HITL escalation."""
    rec = CrossArtifactReconciliationAgent()
    result = rec.reconcile(
        output_01_text="Speaker_1: We discussed the quarterly roadmap and budget.",
        output_02_text="## Action Items\n**Owner:** Speaker_1",
        output_03_text="## Action Items\nOwner: Speaker_1",
        speaker_turn_shares={"Speaker_1": 0.92, "Speaker_2": 0.08},
    )
    assert result.speaker_collapse_detected is True
    assert result.requires_hitl_escalation is True
    assert result.parity_passed is False
    assert result.output_01_immutable_verified is True
