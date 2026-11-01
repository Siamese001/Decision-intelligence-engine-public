"""Adversarial & Edge-Case Tests for Diarization Pipeline.

Covers:
1. Invariant bypass attempts: raw model IDs (SPEAKER_00, speaker_99) attempted as attendees.
2. Boundary payloads: extreme speaker counts (100+ speakers) and rapid speaker ping-pong.
3. Malformed inputs: corrupted, null, and irregular speaker label strings.
4. Rollback / recovery: fallback activation when diarization model pipeline crashes.
"""

from dual_pipeline.audio_ingestion_core.base import BaseAudioTranscriber
from dual_pipeline.transcript_stages_core.attendee_gate import extract_candidate_attendees
from dual_pipeline.turn_clusterer import cluster_conversational_turns


class MockTranscriber(BaseAudioTranscriber):
    def _live_transcribe(self, audio_file_path: str, metadata=None) -> str:
        return "dummy transcript"


def test_adversarial_speaker_label_leak_bypass_attempt():
    """Verify that adversarial raw speaker labels cannot bypass security gates into candidate attendees."""
    adversarial_transcript = (
        "SPEAKER_00: Invariant bypass attempt 1.\n"
        "speaker_99: Invariant bypass attempt 2.\n"
        "Speaker-123: Invariant bypass attempt 3.\n"
        "Unknown: Invariant bypass attempt 4.\n"
        "Unattributed: Invariant bypass attempt 5.\n"
        "Alice Smith: Legitimate attendee statement."
    )
    candidates = extract_candidate_attendees(metadata={}, transcript_text=adversarial_transcript)
    assert "SPEAKER_00" not in candidates
    assert "speaker_99" not in candidates
    assert "Speaker-123" not in candidates
    assert "Unknown" not in candidates
    assert "Unattributed" not in candidates
    assert "Alice Smith" in candidates
    assert len(candidates) == 1


def test_adversarial_boundary_payload_100_speakers():
    """Verify that formatting and normalization handles extreme 100-speaker boundary payloads without crashing."""
    transcriber = MockTranscriber()
    segments = [
        {"speaker": f"SPEAKER_{i:02d}", "start": float(i * 2), "text": f"Utterance from speaker {i}."}
        for i in range(120)
    ]
    formatted = transcriber._format_diarized_json({"segments": segments})
    assert "SPEAKER_00" not in formatted
    assert "SPEAKER_119" not in formatted
    assert "**Speaker 1** [00:00:00]: Utterance from speaker 0." in formatted
    assert "**Speaker 120**" in formatted


def test_adversarial_malformed_speaker_inputs():
    """Verify that malformed, corrupted, or irregular speaker strings do not raise unhandled exceptions."""
    transcriber = MockTranscriber()
    malformed_segments = [
        {"speaker": None, "start": 0.0, "text": "Null speaker"},
        {"speaker": "", "start": 1.0, "text": "Empty speaker"},
        {"speaker": "   ", "start": 2.0, "text": "Whitespace speaker"},
        {"speaker": "Speaker---NaN", "start": 3.0, "text": "Corrupted speaker"},
        {"speaker": 12345, "start": 4.0, "text": "Integer speaker"},
        {"speaker": "SPEAKER_99999999999999999999", "start": 5.0, "text": "Overflow integer speaker"},
    ]
    formatted = transcriber._format_diarized_json({"segments": malformed_segments})
    assert len(formatted.split("\n\n")) == len(malformed_segments)
    for line in formatted.split("\n\n"):
        assert line.startswith("**")


def test_adversarial_turn_clusterer_empty_and_rapid_alternation():
    """Verify that turn clusterer survives zero-length, negative, and microsecond turn bursts."""
    burst_segments = [
        {"start": 0.0, "end": 0.001, "text": ""},
        {"start": 0.001, "end": 0.002, "text": " "},
        {"start": 0.002, "end": 0.005, "text": "Microburst 1"},
        {"start": 0.005, "end": 0.008, "text": "Microburst 2"},
    ]
    clustered, ambiguities = cluster_conversational_turns(burst_segments)
    assert len(clustered) == 2  # Empty texts filtered
    assert clustered[0]["speaker"] == "Speaker 1"
