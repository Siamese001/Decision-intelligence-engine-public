"""
tests/test_speaker_task_and_company_matching.py

Unit and integration tests for Wave 1:
- Task Ownership memory and first-person claim detection
- Company perspective ("we/our" vs "you/your") alignment and penalty
- Candidate scoring with combined ownership and perspective signals
"""
import pytest
from dual_pipeline.speaker_memory import SpeakerMemory, SpeakerProfile
from dual_pipeline.speaker_attribution_core.perspective_scorer import (
    score_company_perspective,
    score_task_ownership,
)
from dual_pipeline.speaker_attribution_core.candidate_scorer import (
    score_candidates_for_speaker,
)


def test_task_ownership_first_person_boost(tmp_path):
    """Verifies that first-person claims of owned deliverables boost true owner by +0.25."""
    mem_file = tmp_path / "speaker_mem.json"
    mem = SpeakerMemory(memory_path=mem_file)

    mem.record_attribution(
        name="Daniel",
        utterance="The battery integration is on track.",
        owned_tasks=["battery integration", "solar database"],
        persist=False,
    )

    # First person claim on owned task
    utterance_1 = "I finished the battery integration yesterday and tested the feed."
    sc_1, pts_1 = mem.match_task_ownership("Daniel", utterance_1)
    assert sc_1 == 0.25
    assert len(pts_1) == 1
    assert "battery integration" in pts_1[0]
    assert "+0.25" in pts_1[0]

    # Third person or inquiry on same task -> zero boost
    utterance_2 = "Can you check if the battery integration is working?"
    sc_2, pts_2 = mem.match_task_ownership("Daniel", utterance_2)
    assert sc_2 == 0.0
    assert pts_2 == []

    # First person claim on unowned task -> zero boost
    utterance_3 = "I completed the executive slide deck for the board."
    sc_3, pts_3 = mem.match_task_ownership("Daniel", utterance_3)
    assert sc_3 == 0.0
    assert pts_3 == []


def test_company_perspective_scoring():
    """Verifies that 'we/our' boosts matching org and 'you/your' penalizes opposing org."""
    partner_advisory_turn = "On our side at PartnerAdvisory, our team finished reviewing the architecture."
    sc_partner_advisory, pts_partner_advisory = score_company_perspective("PartnerAdvisory", partner_advisory_turn.lower())
    assert sc_partner_advisory == 0.20
    assert any("First-person organizational affiliation" in p for p in pts_partner_advisory)

    apex_global_pen, pts_apex_global = score_company_perspective("Apex Global", partner_advisory_turn.lower())
    assert apex_global_pen == 0.0

    # Turn addressing Apex Global in second person
    client_turn = "In your team at Apex Global, how do you guys handle cluster authentication?"
    sc_apex_global_pen, pts_apex_global_pen = score_company_perspective("Apex Global", client_turn.lower())
    assert sc_apex_global_pen == -0.25
    assert any("second-person" in p for p in pts_apex_global_pen)


def test_candidate_scorer_integrates_task_and_company_clues(tmp_path):
    """Verifies that candidate_scorer combines task and company clues to rank the correct speaker."""
    mem_file = tmp_path / "speaker_mem.json"
    mem = SpeakerMemory(memory_path=mem_file)
    mem.record_attribution(
        name="Amit Ayer",
        utterance="Let's look at the standards.",
        owned_tasks=["enterprise architecture", "technical screening"],
        persist=False,
    )

    roster = [
        ("Amit Ayer", "PartnerAdvisory"),
        ("Daniel", "Apex Global"),
    ]

    transcript = """
[00:01:10] Speaker 1: On our side at PartnerAdvisory, I finished the enterprise architecture blueprint yesterday.
[00:01:25] Speaker 2: Looks great.
"""
    metadata = {
        "speaker_memory": mem,
        "task_ownership": {"Amit Ayer": ["enterprise architecture"]},
    }

    candidates = score_candidates_for_speaker(
        spk_id="1",
        transcript=transcript,
        roster=roster,
        metadata=metadata,
    )

    top_candidate = candidates[0]
    assert top_candidate.attendee_name == "Amit Ayer"
    assert top_candidate.confidence >= 0.85
    assert top_candidate.confidence_tier == "HIGH"
    assert "First-person task ownership claim" in top_candidate.evidence_rationale
    assert "First-person organizational affiliation with PartnerAdvisory" in top_candidate.evidence_rationale
