"""
tests/test_speaking_habits_and_elimination_lockout.py

Unit tests for Wave 2:
- Speaking speed (WPM) calculation and tempo alignment
- Meeting habits (call opener, inquiry/question asker)
- Whole-call roster elimination lockout across multiple clusters
"""
import pytest
from dual_pipeline.speaker_memory import SpeakerMemory, SpeakerProfile
from dual_pipeline.speaker_attribution_core.models import CandidateAttribution
from dual_pipeline.speaker_attribution_core.perspective_scorer import (
    apply_roster_elimination_lockout,
    extract_speaker_wpm,
    score_speaking_speed_and_habits,
)


def test_speaking_wpm_calculation():
    """Verifies that words per minute are calculated from turn timestamps."""
    transcript = """
[00:01:00 - 00:01:30] Speaker 1: This is a thirty second utterance containing exactly twenty words to test the calculation logic properly today.
[00:01:35 - 00:01:45] Speaker 2: Understood.
"""
    # 20 words in 30 seconds = 40 WPM
    wpm = extract_speaker_wpm("1", transcript)
    assert 35.0 <= wpm <= 45.0

    # No timestamps or insufficient duration
    assert extract_speaker_wpm("2", transcript) == 0.0


def test_speaking_speed_and_habits_scoring(tmp_path):
    """Verifies speed matching, opener habit, and question asking habit boosts."""
    mem_file = tmp_path / "speaker_mem.json"
    mem = SpeakerMemory(memory_path=mem_file)

    p = mem.record_attribution(
        name="Stefan",
        utterance="Welcome everyone to the readout.",
        persist=False,
    )
    p.avg_words_per_minute = 160.0
    p.call_opener_count = 5

    ordered_turns = [
        ("Speaker 1", "Welcome to the meeting, everyone. Let's get started."),
        ("Speaker 2", "Thanks Stefan."),
    ]
    turns_1 = [ordered_turns[0][1], "Any questions on the survey results? What about the timeline?"]

    metadata = {"speaker_memory": mem}

    # Fast talker matching candidate speed (155 vs 160 WPM) + Opener habit
    sc, pts = score_speaking_speed_and_habits(
        metadata=metadata,
        candidate_name="Stefan",
        role="Managing Director",
        full_text=" ".join(turns_1),
        full_text_lower=" ".join(turns_1).lower(),
        turns=turns_1,
        ordered_turns=ordered_turns,
        spk_id="1",
        observed_wpm=155.0,
    )

    assert sc >= 0.20  # WPM (+0.10) + opener (+0.10)
    assert any("Speaking speed match" in p for p in pts)
    assert any("Learned meeting host / call opener habit" in p for p in pts)

    # Slow talker mismatch (60 WPM vs 160 WPM)
    sc_slow, pts_slow = score_speaking_speed_and_habits(
        metadata=metadata,
        candidate_name="Stefan",
        role="Managing Director",
        full_text=turns_1[0],
        full_text_lower=turns_1[0].lower(),
        turns=[turns_1[0]],
        ordered_turns=ordered_turns,
        spk_id="2",
        observed_wpm=60.0,
    )
    assert sc_slow < 0.0
    assert any("Speaking speed mismatch" in p for p in pts_slow)


def test_roster_elimination_lockout_across_clusters():
    """Verifies that high-confidence clusters lock attendees, resolving remaining clusters by elimination."""
    cand_a1 = CandidateAttribution(attendee_name="Amit Ayer", organization="PartnerAdvisory", confidence=0.95, confidence_tier="HIGH", evidence_rationale="Voiceprint match")
    cand_b1 = CandidateAttribution(attendee_name="Daniel", organization="Apex Global", confidence=0.55, confidence_tier="MEDIUM", evidence_rationale="Prior")
    cand_c1 = CandidateAttribution(attendee_name="Sharon", organization="Apex Global", confidence=0.50, confidence_tier="LOW", evidence_rationale="Prior")

    cand_b2 = CandidateAttribution(attendee_name="Daniel", organization="Apex Global", confidence=0.92, confidence_tier="HIGH", evidence_rationale="Quote hint match")
    cand_c2 = CandidateAttribution(attendee_name="Sharon", organization="Apex Global", confidence=0.50, confidence_tier="LOW", evidence_rationale="Prior")

    # Cluster 3 has low talk time and ambiguous scores for all 3
    cand_a3 = CandidateAttribution(attendee_name="Amit Ayer", organization="PartnerAdvisory", confidence=0.50, confidence_tier="MEDIUM", evidence_rationale="Baseline")
    cand_b3 = CandidateAttribution(attendee_name="Daniel", organization="Apex Global", confidence=0.50, confidence_tier="MEDIUM", evidence_rationale="Baseline")
    cand_c3 = CandidateAttribution(attendee_name="Sharon", organization="Apex Global", confidence=0.50, confidence_tier="MEDIUM", evidence_rationale="Baseline")

    cluster_map = {
        "Speaker 1": [cand_a1, cand_b1, cand_c1],
        "Speaker 2": [cand_b2, cand_c2],
        "Speaker 3": [cand_a3, cand_b3, cand_c3],
    }

    resolved = apply_roster_elimination_lockout(cluster_map)

    # Cluster 1 remains Amit Ayer
    assert resolved["Speaker 1"][0].attendee_name == "Amit Ayer"
    # Cluster 2 remains Daniel
    assert resolved["Speaker 2"][0].attendee_name == "Daniel"

    # Cluster 3 is resolved by deduction to Sharon with confidence 0.90 HIGH
    assert len(resolved["Speaker 3"]) == 1
    c3_top = resolved["Speaker 3"][0]
    assert c3_top.attendee_name == "Sharon"
    assert c3_top.confidence == 0.90
    assert c3_top.confidence_tier == "HIGH"
    assert "Process of elimination lockout" in c3_top.evidence_rationale
