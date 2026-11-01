"""
Tests candidate scoring style, vocabulary, pace, and conversation partner boosts.
Wave 2 verification suite.
"""

from typing import Any, Dict
from dual_pipeline.speaker_attribution_core.candidate_scorer import score_candidates_for_speaker
from dual_pipeline.speaker_memory import SpeakerMemory, SpeakerProfile


def test_candidate_scorer_opener_phrase_style_boost() -> None:
    """Verifies that a candidate with matching learned opener phrase receives a score boost."""
    memory = SpeakerMemory()
    memory._profiles["alice smith"] = SpeakerProfile(
        name="Alice Smith",
        person_id="person:alice_smith",
        opener_phrases=["let's dive right in"],
    )

    transcript = (
        "**Speaker 1** [00:00:01]: Let's dive right in team. We need to review the weekly metrics."
    )
    roster = [("Alice Smith", "PartnerAdvisory"), ("Bob Jones", "PartnerAdvisory")]
    metadata: Dict[str, Any] = {
        "speaker_memory": memory,
        "attendees": ["Alice Smith", "Bob Jones"],
    }

    candidates = score_candidates_for_speaker("Speaker 1", transcript, roster, metadata, total_clusters=2)
    alice = next(c for c in candidates if c.attendee_name == "Alice Smith")
    bob = next(c for c in candidates if c.attendee_name == "Bob Jones")

    assert alice.confidence > bob.confidence
    assert alice.is_recommended is True
    assert "Learned opener phrase match (+0.15)" in alice.evidence_rationale


def test_candidate_scorer_vocabulary_and_pace_boost() -> None:
    """Verifies that distinctive vocabulary and speaking turn length elevate candidate attribution."""
    memory = SpeakerMemory()
    memory._profiles["bob jones"] = SpeakerProfile(
        name="Bob Jones",
        person_id="person:bob_jones",
        common_vocabulary={"throughput": 5, "latency": 4, "vector": 3},
        avg_words_per_turn=12.0,
    )

    transcript = (
        "**Speaker 1** [00:00:01]: Our throughput and latency in the vector database look solid."
    )
    roster = [("Alice Smith", "PartnerAdvisory"), ("Bob Jones", "PartnerAdvisory")]
    metadata: Dict[str, Any] = {
        "speaker_memory": memory,
        "attendees": ["Alice Smith", "Bob Jones"],
    }

    candidates = score_candidates_for_speaker("Speaker 1", transcript, roster, metadata, total_clusters=2)
    bob = next(c for c in candidates if c.attendee_name == "Bob Jones")

    assert bob.confidence >= 0.70
    assert "Learned vocabulary alignment" in bob.evidence_rationale
    assert "Learned speaking pace/turn length match" in bob.evidence_rationale


def test_candidate_scorer_interlocutor_pattern_boost() -> None:
    """Verifies that speaking after a frequent conversational partner boosts candidate confidence."""
    memory = SpeakerMemory()
    memory._profiles["bob jones"] = SpeakerProfile(
        name="Bob Jones",
        person_id="person:bob_jones",
        frequent_interlocutors={"charlie": 10},
    )

    transcript = (
        "**Charlie** [00:00:01]: What do you think about the proposed timeline?\n\n"
        "**Speaker 1** [00:00:05]: I think the proposed timeline makes complete sense."
    )
    roster = [("Alice Smith", "PartnerAdvisory"), ("Bob Jones", "PartnerAdvisory")]
    metadata: Dict[str, Any] = {
        "speaker_memory": memory,
        "attendees": ["Alice Smith", "Bob Jones"],
    }

    candidates = score_candidates_for_speaker("Speaker 1", transcript, roster, metadata, total_clusters=2)
    bob = next(c for c in candidates if c.attendee_name == "Bob Jones")

    assert bob.confidence >= 0.65
    assert "Learned conversation partner pattern with Charlie (+0.15)" in bob.evidence_rationale


def test_candidate_scorer_graceful_fallback_without_style_data() -> None:
    """Verifies that candidate scoring operates gracefully with zero prior style profiles."""
    transcript = (
        "**Speaker 1** [00:00:01]: Good morning everyone, let us get started with today's sync."
    )
    roster = [("Alice Smith", "PartnerAdvisory"), ("Bob Jones", "PartnerAdvisory")]
    metadata: Dict[str, Any] = {
        "speaker_memory": SpeakerMemory(),
        "attendees": ["Alice Smith", "Bob Jones"],
    }

    candidates = score_candidates_for_speaker("Speaker 1", transcript, roster, metadata, total_clusters=2)
    assert len(candidates) == 2
    assert candidates[0].confidence == 0.50
    assert candidates[1].confidence == 0.50
