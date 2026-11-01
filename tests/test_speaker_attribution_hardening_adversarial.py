"""
tests/test_speaker_attribution_hardening_adversarial.py

Adversarial stress and edge-case tests covering:
1. Boundary payloads: empty transcripts, zero-length dialogue, single-word turns
2. Malformed input: missing metadata, corrupt speaker memory, unrostered speakers
3. Contradictory claims: mentioning another person's task without first-person claim
4. Tie-breaking and roster stability under ambiguous scores
"""
import pytest
from dual_pipeline.speaker_memory import SpeakerMemory
from dual_pipeline.speaker_attribution_core.models import CandidateAttribution
from dual_pipeline.speaker_attribution_core.candidate_scorer import score_candidates_for_speaker
from dual_pipeline.speaker_attribution_core.perspective_scorer import (
    apply_roster_elimination_lockout,
    extract_speaker_wpm,
    score_company_perspective,
    score_speaking_speed_and_habits,
    score_task_ownership,
)


def test_adversarial_boundary_empty_and_zero_duration():
    """Verifies that empty transcript, zero-duration turns, and null inputs handle gracefully."""
    assert extract_speaker_wpm("1", "") == 0.0
    assert extract_speaker_wpm("1", "   \n  \n") == 0.0

    # Malformed single turn with identical start and end timestamps
    transcript = "[00:01:00 - 00:01:00] Speaker 1: Hello."
    assert extract_speaker_wpm("1", transcript) == 0.0

    # Empty metadata and empty text scoring
    sc_task, pts_task = score_task_ownership({}, "Daniel", "")
    assert sc_task == 0.0
    assert pts_task == []

    sc_comp, pts_comp = score_company_perspective("", "")
    assert sc_comp == 0.0
    assert pts_comp == []


def test_adversarial_contradictory_task_attribution(tmp_path):
    """Verifies that a speaker inquiring about another person's task does NOT receive a false ownership boost."""
    mem_file = tmp_path / "speaker_mem.json"
    mem = SpeakerMemory(memory_path=mem_file)
    mem.record_attribution("Daniel", "Solar database update.", owned_tasks=["solar database"], persist=False)

    metadata = {
        "speaker_memory": mem,
        "task_ownership": {"Daniel": ["solar database"]},
    }

    # Third person question mentioning Daniel's task
    query_turn = "Hey Daniel, did you finish the solar database update yesterday?"
    sc, pts = score_task_ownership(metadata, "Daniel", query_turn.lower())

    # Daniel is addressed ("did you finish"), so Daniel is NOT the speaker of this query
    assert sc == 0.0
    assert pts == []


def test_adversarial_unrostered_guest_speaker():
    """Verifies that an unknown guest speaker cluster does not cause a crash and degrades gracefully."""
    roster = [
        ("Stefan", "PartnerAdvisory"),
        ("Daniel", "Apex Global"),
    ]
    transcript = """
[00:01:00 - 00:01:15] Speaker 99: Hello from the third-party security audit team.
"""
    metadata = {
        "roster": [
            {"name": "Stefan", "org": "PartnerAdvisory"},
            {"name": "Daniel", "org": "Apex Global"},
        ]
    }

    candidates = score_candidates_for_speaker(
        spk_id="99",
        transcript=transcript,
        roster=roster,
        metadata=metadata,
        total_clusters=3,
    )

    # All candidates should remain at baseline or low confidence without false high boosts
    assert len(candidates) == 2
    for cand in candidates:
        assert cand.confidence < 0.85
        assert cand.confidence_tier in ("LOW", "MEDIUM")


def test_adversarial_elimination_lockout_stability_with_ties():
    """Verifies that roster elimination lockout handles dead-heat ties without infinite loops or dropping candidates."""
    cand_1a = CandidateAttribution(attendee_name="Stefan", organization="PartnerAdvisory", confidence=0.50, confidence_tier="MEDIUM", evidence_rationale="Tie")
    cand_1b = CandidateAttribution(attendee_name="Daniel", organization="Apex Global", confidence=0.50, confidence_tier="MEDIUM", evidence_rationale="Tie")

    cand_2a = CandidateAttribution(attendee_name="Stefan", organization="PartnerAdvisory", confidence=0.50, confidence_tier="MEDIUM", evidence_rationale="Tie")
    cand_2b = CandidateAttribution(attendee_name="Daniel", organization="Apex Global", confidence=0.50, confidence_tier="MEDIUM", evidence_rationale="Tie")

    cluster_map = {
        "Speaker 1": [cand_1a, cand_1b],
        "Speaker 2": [cand_2a, cand_2b],
    }

    # Should exit cleanly without changing when neither cluster is decisive
    resolved = apply_roster_elimination_lockout(cluster_map)
    assert len(resolved["Speaker 1"]) == 2
    assert len(resolved["Speaker 2"]) == 2
    assert resolved["Speaker 1"][0].confidence == 0.50
