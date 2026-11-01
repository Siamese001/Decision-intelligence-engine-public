"""
tests/adversarial/test_mp4_speaker_cluster_preservation_adversarial.py

Wave 1 Adversarial & Edge-Case Suite covering:
1. Boundary payloads (empty diarization intervals, zero-turn segments, empty transcript strings).
2. Malformed inputs (body markdown tables with '**Participants**' and '**Attendees**' headers).
3. Exact production regression (3-cluster / 8-roster deficit where strict 1.0s aligner fails >50% Unknown).
"""
from __future__ import annotations

from unittest.mock import MagicMock

from dual_pipeline.audio_ingestion_core.mlx_transcriber import LocalMLXAudioTranscriber
from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import (
    extract_attendees_from_header_block,
)


class _Turn:
    def __init__(self, start: float, end: float) -> None:
        self.start = start
        self.end = end


class _Diarization:
    def __init__(self, tracks: list[tuple[_Turn, None, str]]) -> None:
        self._tracks = tracks

    def itertracks(self, yield_label: bool = False):
        return list(self._tracks) if yield_label else [(t[0], t[1]) for t in self._tracks]


def test_boundary_empty_diarization_intervals_graceful_fallback() -> None:
    """Boundary payload: Zero diarization turns fails closed in validate_acoustic_diarization, and falls back to 'Speaker' if collapse error is raised with empty intervals."""
    from unittest.mock import patch
    import pytest
    from dual_pipeline.exceptions import DiarizationCollapseError

    transcriber = LocalMLXAudioTranscriber(mock=True)
    mock_engine = MagicMock()
    mock_engine.diarize.return_value = _Diarization([])
    transcriber.diarization_engine = mock_engine

    segments = [{"start": 0.0, "end": 1.0, "text": "Short utterance"}]
    meta = {"attendees": ["logan Miller", "David Childers"]}

    with pytest.raises(RuntimeError, match="0 speaker turns"):
        transcriber._diarize_and_align(segments, "empty_tracks.m4a", meta)

    with patch(
        "dual_pipeline.audio_ingestion_core.diarization_engine.validate_acoustic_diarization",
        side_effect=DiarizationCollapseError("empty collapse"),
    ):
        res = transcriber._diarize_and_align(segments, "empty_tracks.m4a", meta)
    assert len(res) == 1
    assert res[0]["speaker"] == "Speaker"
    assert meta["diarization_status"] == "HITL_REQUIRED"


def test_boundary_empty_segments_list_returns_empty() -> None:
    """Boundary payload: Zero segments passed into _diarize_and_align with collapse error."""
    transcriber = LocalMLXAudioTranscriber(mock=True)
    mock_engine = MagicMock()
    mock_engine.diarize.return_value = _Diarization([(_Turn(0.0, 2.0), None, "SPEAKER_00")])
    transcriber.diarization_engine = mock_engine

    meta = {"attendees": ["logan Miller", "David Childers", "Speaker_Alpha Muschett"]}
    res = transcriber._diarize_and_align([], "zero_segments.m4a", meta)
    assert res == []
    assert meta["diarization_status"] == "HITL_REQUIRED"


def test_malformed_body_table_without_header_participants_returns_none() -> None:
    """Malformed input: Transcript with no header participants block but a body table containing '| **Participants** |'."""
    body_only_table = """# Technical Readout

**Date:** 09/09/2026

---

[00:01:00] **David Childers:** Reviewing table metrics.

| Category | Details |
| :--- | :--- |
| **Participants** | 10-20 engineers |
| **Attendees** | 100% completion rate |
"""
    assert extract_attendees_from_header_block(body_only_table) is None


def test_production_regression_skew_and_deficit_preserves_clusters_without_words() -> None:
    """
    Exact production regression: Segment-only ASR output (no word-level array) where
    >50% of segments are >1.0s away from diarization intervals during a roster-count deficit.
    """
    transcriber = LocalMLXAudioTranscriber(mock=True)
    mock_engine = MagicMock()
    tracks = [
        (_Turn(0.0, 5.0), None, "SPEAKER_00"),
        (_Turn(100.0, 105.0), None, "SPEAKER_01"),
    ]
    mock_engine.diarize.return_value = _Diarization(tracks)
    transcriber.diarization_engine = mock_engine

    segments = [
        {"start": 0.5, "end": 2.0, "text": "Opening statement"},
        {"start": 10.0, "end": 12.0, "text": "Delayed turn 1 (>1.0s gap)"},
        {"start": 20.0, "end": 22.0, "text": "Delayed turn 2 (>1.0s gap)"},
        {"start": 101.0, "end": 103.0, "text": "Response from second speaker"},
    ]
    meta = {"attendees": ["logan Miller", "David Childers", "Brandon Que", "Speaker_Alpha Muschett"]}

    res = transcriber._diarize_and_align(segments, "regression.m4a", meta)
    speakers = [s["speaker"] for s in res]
    assert speakers == ["SPEAKER_00", "SPEAKER_00", "SPEAKER_00", "SPEAKER_01"]
