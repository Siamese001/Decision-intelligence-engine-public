"""Unit tests for Wave 2: PyAnnote label normalization, regex matching, and status propagation."""


from dual_pipeline.audio_ingestion_core.base import BaseAudioTranscriber
from dual_pipeline.transcript_pipeline_core.source_modality import (
    _has_unmapped_generic_speakers,
)
from dual_pipeline.transcript_stages_core.attendee_gate import extract_candidate_attendees
from dual_pipeline.transcript_stages_core.constants import (
    _GENERIC_SPEAKER_PATTERN,
    _PYANNOTE_SPEAKER_PATTERN,
)
from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary


class DummyTranscriber(BaseAudioTranscriber):
    def _live_transcribe(self, audio_file_path: str, metadata=None) -> str:
        return "dummy transcript"


def test_generic_speaker_pattern_matches_pyannote_labels():
    """Verify that _GENERIC_SPEAKER_PATTERN matches SPEAKER_00 and other acoustic variations."""
    test_strings = [
        "SPEAKER_00",
        "SPEAKER_01",
        "speaker_02",
        "speaker-03",
        "Speaker 1",
        "Speaker A",
        "Unknown Speaker",
        "Unattributed",
    ]
    for s in test_strings:
        matches = _GENERIC_SPEAKER_PATTERN.findall(s)
        assert len(matches) > 0, f"Failed to match generic pattern on: {s}"

    assert _PYANNOTE_SPEAKER_PATTERN.search("SPEAKER_00") is not None
    assert _PYANNOTE_SPEAKER_PATTERN.search("SPEAKER_99") is not None
    assert _PYANNOTE_SPEAKER_PATTERN.search("Speaker 1") is None


def test_u0_detect_diarization_status_acoustic():
    """Verify that U0 detects DIARIZED_ACOUSTIC when metadata indicates acoustic diarization."""
    transcript = "**Speaker 1**: Hello.\n\n**Speaker 2**: Hi there."
    metadata = {
        "has_acoustic_diarization": True,
        "_from_audio_transcription": True,
        "source_type": "AUDIO_DIARIZED",
    }
    status = U0IngestionBoundary._detect_diarization_status(transcript, metadata)
    assert status == "DIARIZED_ACOUSTIC"


def test_format_diarized_json_normalizes_pyannote_ids():
    """Verify that _format_diarized_json converts SPEAKER_00 to clean sequential Speaker N."""
    transcriber = DummyTranscriber()
    payload = {
        "segments": [
            {"speaker": "SPEAKER_00", "start": 0.0, "text": "First turn."},
            {"speaker": "SPEAKER_01", "start": 5.0, "text": "Second turn."},
            {"speaker": "SPEAKER_00", "start": 10.0, "text": "Third turn."},
        ]
    }
    formatted = transcriber._format_diarized_json(payload)
    assert "SPEAKER_00" not in formatted, "Raw SPEAKER_00 should be normalized"
    assert "SPEAKER_01" not in formatted, "Raw SPEAKER_01 should be normalized"
    assert "**Speaker 1** [00:00:00]: First turn." in formatted
    assert "**Speaker 2** [00:00:05]: Second turn." in formatted
    assert "**Speaker 1** [00:00:10]: Third turn." in formatted


def test_format_diarized_json_preserves_unknown_labels():
    """Verify that _format_diarized_json retains Unknown rather than inventing Speaker N."""
    transcriber = DummyTranscriber()
    payload = {
        "segments": [
            {"speaker": "Unknown", "start": 0.0, "text": "First turn from unknown speaker."},
            {"speaker": "speaker unknown", "start": 5.0, "text": "Second turn from speaker unknown."},
            {"speaker": "SPEAKER_00", "start": 10.0, "text": "Acoustic turn."},
        ]
    }
    formatted = transcriber._format_diarized_json(payload)
    assert "**Unknown** [00:00:00]: First turn from unknown speaker." in formatted
    assert "**Unknown** [00:00:05]: Second turn from speaker unknown." in formatted
    assert "**Speaker 1** [00:00:10]: Acoustic turn." in formatted


def test_source_modality_catches_unmapped_pyannote_speaker():
    """Verify that _has_unmapped_generic_speakers identifies unmapped pyannote or generic labels."""
    transcript_with_speaker_00 = "SPEAKER_00: Hello team, let's start."
    assert _has_unmapped_generic_speakers(transcript_with_speaker_00, speaker_map={}) is True
    assert _has_unmapped_generic_speakers(transcript_with_speaker_00, speaker_map={"SPEAKER_00": "Dan"}) is False

    transcript_with_speaker_1 = "Speaker 1: Hello team."
    assert _has_unmapped_generic_speakers(transcript_with_speaker_1, speaker_map={}) is True
    assert _has_unmapped_generic_speakers(transcript_with_speaker_1, speaker_map={"Speaker 1": "Dan"}) is False


def test_candidate_attendees_ignores_generic_and_pyannote_speakers():
    """Verify that candidate attendee extraction never extracts SPEAKER_00 or Speaker 1 as attendee names."""
    transcript = (
        "SPEAKER_00: Good morning everyone.\n"
        "Speaker 1: Thanks for joining.\n"
        "Alice: Let's review the charter.\n"
        "Bob: Sounds good."
    )
    candidates = extract_candidate_attendees(metadata={}, transcript_text=transcript)
    assert "SPEAKER_00" not in candidates
    assert "Speaker 1" not in candidates
    assert "Alice" in candidates
    assert "Bob" in candidates
