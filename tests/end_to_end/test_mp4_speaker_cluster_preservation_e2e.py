"""
tests/end_to_end/test_mp4_speaker_cluster_preservation_e2e.py

Wave 1 E2E Acceptance Suite:
1. Standalone MP4/M4A 3-cluster / 8-roster-participant diarization deficit with >50% >1.0s inter-turn gaps
   executes the real _align_segments_with_diarization (without mocks) and falls back to
   assign_speakers_with_whisperx(fill_nearest=True) to preserve SPEAKER_00, SPEAKER_01, and SPEAKER_02.
2. Transcript header attendee extraction isolates header participants from body markdown tables
   containing '**Participants**' columns after '---'.
"""
from __future__ import annotations

from collections import Counter
from unittest.mock import MagicMock, patch

from dual_pipeline.audio_ingestion_core.mlx_transcriber import LocalMLXAudioTranscriber
from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import (
    build_roster_from_attendees,
    extract_attendees_from_header_block,
)


class _RealTimelineTurn:
    def __init__(self, start: float, end: float) -> None:
        self.start = start
        self.end = end


class _RealPyannoteDiarization:
    def __init__(self, tracks: list[tuple[_RealTimelineTurn, None, str]]) -> None:
        self._tracks = tracks

    def itertracks(self, yield_label: bool = False):
        if yield_label:
            return list(self._tracks)
        return [(t[0], t[1]) for t in self._tracks]


def test_mp4_cluster_deficit_with_wide_gaps_preserves_three_speakers_e2e() -> None:
    """
    Replays the exact Sherpa demo Josh 9.9.2026.m4a failure:
    - 8 roster participants in metadata, 3 active Pyannote voice clusters (SPEAKER_00, SPEAKER_01, SPEAKER_02).
    - 436 segments where >50% (260/436) lie >1.0s away from the nearest Pyannote interval,
      causing real _align_segments_with_diarization to raise RuntimeError('Fail-Fast: ... >50% Unknown').
    - Verifies _diarize_and_align falls back to assign_speakers_with_whisperx(fill_nearest=True)
      and preserves all 3 SPEAKER_00..02 clusters with zero generic 'Speaker' overwrite.
    """
    transcriber = LocalMLXAudioTranscriber(mock=True)
    mock_engine = MagicMock()

    # 3 distinct acoustic intervals separated by wide gaps
    tracks = [
        (_RealTimelineTurn(0.0, 10.0), None, "SPEAKER_00"),
        (_RealTimelineTurn(500.0, 510.0), None, "SPEAKER_01"),
        (_RealTimelineTurn(1000.0, 1010.0), None, "SPEAKER_02"),
    ]
    diarization = _RealPyannoteDiarization(tracks)
    mock_engine.diarize.return_value = diarization
    transcriber.diarization_engine = mock_engine

    segments = []
    for idx in range(436):
        if idx < 90:
            # Inside or near SPEAKER_00
            start_t = float(idx) * 0.1
            end_t = start_t + 0.08
        elif idx < 176:
            # Inside or near SPEAKER_01
            start_t = 500.0 + float(idx - 90) * 0.1
            end_t = start_t + 0.08
        else:
            # 260 segments >1.0s after SPEAKER_01 or SPEAKER_02 (triggers >50% Unknown in strict 1.0s aligner)
            base = 520.0 if idx < 300 else 1020.0
            start_t = base + float(idx) * 0.2
            end_t = start_t + 0.15
        segments.append({
            "start": start_t,
            "end": end_t,
            "text": f"Segment {idx} discussion on Sherpa architecture.",
            "words": [
                {"word": "Segment", "start": start_t, "end": start_t + 0.04},
                {"word": str(idx), "start": start_t + 0.04, "end": end_t},
            ],
        })

    metadata = {
        "source_type": "AUDIO",
        "input_ext": ".m4a",
        "meeting_name": "Sherpa Demo Josh",
        "attendees": [
            "logan Miller",
            "David Childers",
            "Brandon Que",
            "Speaker_Alpha Muschett",
            "Marcus Balk",
            "Catalina Orostegui",
            "Egle Tamasevice",
            "Amit Ayer",
        ],
    }

    aligned = transcriber._diarize_and_align(
        segments,
        "Sherpa demo Josh 9.9.2026.m4a",
        metadata,
    )

    assert len(aligned) == 436
    counts = Counter(s.get("speaker") for s in aligned)
    assert "Speaker" not in counts, f"Generic 'Speaker' overwrite occurred: {counts}"
    assert "Unknown" not in counts, f"Unresolved 'Unknown' remained: {counts}"
    assert set(counts.keys()) == {"SPEAKER_00", "SPEAKER_01", "SPEAKER_02"}
    assert metadata.get("diarization_status") == "HITL_REQUIRED"
    assert metadata.get("single_speaker_collapse") is True


def test_header_attendees_isolated_from_body_participants_table_e2e() -> None:
    """
    Verifies that inline bold-colon company headers parse cleanly and markdown tables
    in the transcript body containing '**Participants**' do not bleed into the attendee roster.
    """
    transcript = """# Sherpa_Demo_Josh -- HITL-Resolved Transcript

**Date:** 09/09/2026  
**Time:** 10:00 AM - 10:30 AM ET  
**Platform Reference:** Microsoft Teams  
**Participants:**
- **Apex Global Enterprises / APEX:** logan Miller (Chief AI and Data Officer), David Childers (Scrum Master), Brandon Que (Project Lead), Speaker_Alpha Muschett (Enterprise Architect), Marcus Balk (Enterprise Architect), Catalina Orostegui (Engineering), Egle Tamasevice (Tech Lead)
- **PartnerAdvisory:** Amit Ayer (Lead Enterprise Architect)

---

### Section 1: Pilot Metrics
[00:00:01] **David Childers:** Welcome to the Sherpa demo.

| Metric | Target |
| :--- | :--- |
| **Participants** | 10-20 developers across 2-3 squads |
| **What we measure** | Time saved per workflow |
| **% of Sherpa findings** | Accepted or acted on by developers |
"""
    grouped = extract_attendees_from_header_block(transcript)
    assert grouped is not None
    roster = build_roster_from_attendees(grouped)
    names = [entry["name"] for entry in roster]
    assert len(names) == 8
    assert "logan Miller" in names
    assert "Amit Ayer" in names
    assert not any("What we measure" in n or "Sherpa findings" in n or "10-20" in n for n in names)
