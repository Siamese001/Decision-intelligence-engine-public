"""
tests/unit/test_diarization_integration_hardening.py

Wave 2 Integration Hardening & Verification Suite:
1. Cache Invalidation Hardening (diarizer model, revision, roster bounds, alignment version).
2. Central Acoustic Diarization Validation & Collapse Gating.
3. Overlap-Aware Word and Segment Alignment (multi-speaker preservation).
4. Short-Turn & Unknown Retention (no silent pruning of audible acknowledgments).
5. Anti-Placeholder Self-Resolution Firewall (rejection of {"PartnerAdvisory Speaker": "PartnerAdvisory Speaker"}).
6. Calibrated Bijective Deduction (prior hypothesis bounded below 1.0).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.audio_ingestion_core.base import BaseAudioTranscriber
from dual_pipeline.audio_ingestion_core.diarization_engine import (
    validate_acoustic_diarization,
)
from dual_pipeline.audio_ingestion_core.mlx_transcriber import (
    LocalMLXAudioTranscriber,
    _align_segments_with_diarization,
)
from dual_pipeline.exceptions import DiarizationCollapseError
from dual_pipeline.speaker_attribution_core.attribution_engine import (
    AudioSpeakerAttributionEngine,
)
from dual_pipeline.transcript_pipeline_core.source_modality import (
    SourceModality,
    _classify_audio_modality,
    _has_unmapped_generic_speakers,
)


class DummyTurn:
    def __init__(self, start: float, end: float):
        self.start = start
        self.end = end


class DummyDiarization:
    def __init__(self, tracks: List[tuple[float, float, str]]):
        self.tracks = tracks

    def itertracks(self, yield_label: bool = True):
        for start, end, spk in self.tracks:
            yield DummyTurn(start, end), None, spk


class DummyTranscriber(BaseAudioTranscriber):
    def transcribe(self, audio_file_path: str, metadata: dict | None = None) -> str:
        return ""


# ---------------------------------------------------------------------------
# Test 1: Cache Invalidation Hardening
# ---------------------------------------------------------------------------
def test_cache_key_incorporates_diarizer_model_revision_and_roster():
    """Verifies that altering diarizer model, revision, or roster count yields distinct cache keys."""
    tx = LocalMLXAudioTranscriber(mock=True)
    tx.model_name = "test-model"
    tx.language = "en"
    tx.without_timestamps = False

    engine1 = MagicMock()
    engine1.__class__.__name__ = "DiarizationEngine"
    engine1.model_id = "pyannote/speaker-diarization-3.1"
    engine1.revision = "rev_31"

    engine2 = MagicMock()
    engine2.__class__.__name__ = "DiarizationEngine"
    engine2.model_id = "pyannote/speaker-diarization-community-1"
    engine2.revision = "rev_comm1"

    audio_hash = "abc12345"
    prompt = "Test prompt"

    def build_key(engine, roster_count):
        d_cls = engine.__class__.__name__
        d_model = getattr(engine, "model_id", "pinned")
        d_rev = getattr(engine, "revision", "default")
        diar_meta = f":{d_cls}:{d_model}:{d_rev}:roster_{roster_count}:align_v2"
        data = f"{audio_hash}:{tx.model_name}:{tx.language}:{prompt}:{tx.without_timestamps}:{tx.mock}{diar_meta}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    key_31_4 = build_key(engine1, 4)
    key_31_3 = build_key(engine1, 3)
    key_comm1_4 = build_key(engine2, 4)

    assert key_31_4 != key_31_3, "Cache key must vary when expected roster count changes"
    assert key_31_4 != key_comm1_4, "Cache key must vary when diarizer model/revision changes"


# ---------------------------------------------------------------------------
# Test 2: Central Acoustic Validation & Collapse Gating
# ---------------------------------------------------------------------------
def test_validate_acoustic_diarization_detects_single_speaker_collapse():
    """Fails closed when one speaker occupies >85% of multi-speaker meeting time."""
    # 90s total: Speaker 1 has 85s (94.4%), Speaker 2 has 5s
    tracks = [
        (0.0, 85.0, "SPEAKER_00"),
        (85.0, 90.0, "SPEAKER_01"),
    ]
    diar = DummyDiarization(tracks)

    with pytest.raises(DiarizationCollapseError, match="Single speaker collapse detected"):
        validate_acoustic_diarization(diar, expected_roster_count=2, max_single_speaker_collapse_pct=0.85)


def test_validate_acoustic_diarization_detects_cluster_deficit():
    """Fails closed when acoustic cluster count is less than expected roster count."""
    tracks = [
        (0.0, 30.0, "SPEAKER_00"),
        (30.0, 60.0, "SPEAKER_01"),
    ]
    diar = DummyDiarization(tracks)

    with pytest.raises(DiarizationCollapseError, match="Found only 2 acoustic clusters, but expected 4"):
        validate_acoustic_diarization(diar, expected_roster_count=4)


def test_validate_acoustic_diarization_passes_balanced_multi_speaker():
    """Passes when clusters match roster and speech is distributed."""
    tracks = [
        (0.0, 25.0, "SPEAKER_00"),
        (25.0, 50.0, "SPEAKER_01"),
        (50.0, 75.0, "SPEAKER_02"),
        (75.0, 100.0, "SPEAKER_03"),
    ]
    diar = DummyDiarization(tracks)
    turns = validate_acoustic_diarization(diar, expected_roster_count=4)
    assert len(turns) == 4
    assert {t["speaker"] for t in turns} == {"SPEAKER_00", "SPEAKER_01", "SPEAKER_02", "SPEAKER_03"}


# ---------------------------------------------------------------------------
# Test 3: Overlap-Aware Word and Segment Alignment
# ---------------------------------------------------------------------------
def test_align_segments_preserves_overlap_and_covering_speakers():
    """Verifies that words and segments in crosstalk capture all overlapping speakers."""
    # Turn 1: 0.0 to 5.0 (Speaker 1)
    # Turn 2: 3.0 to 7.0 (Speaker 2) - overlaps between 3.0 and 5.0
    tracks = [
        (0.0, 5.0, "Speaker 1"),
        (3.0, 7.0, "Speaker 2"),
    ]
    diar = DummyDiarization(tracks)

    sanitized_segments = [
        {
            "start": 0.0,
            "end": 6.0,
            "text": "Hello world overlapping speech",
            "words": [
                {"word": "Hello", "start": 0.5, "end": 1.0},
                {"word": "world", "start": 1.5, "end": 2.5},
                {"word": "overlapping", "start": 3.5, "end": 4.5},  # In overlap zone!
                {"word": "speech", "start": 5.5, "end": 6.0},
            ],
        }
    ]

    aligned = _align_segments_with_diarization(sanitized_segments, diar)
    assert len(aligned) >= 1

    # Find the segment containing 'overlapping'
    overlap_seg = next(s for s in aligned if any("overlapping" in w.get("word", "") for w in s.get("words", [])))
    assert overlap_seg.get("overlap") is True
    assert set(overlap_seg.get("active_speakers", [])) == {"Speaker 1", "Speaker 2"}

    # Word-level check
    overlap_word = next(w for w in overlap_seg["words"] if "overlapping" in w.get("word", ""))
    assert "Speaker 1" in overlap_word.get("overlapping_speakers", [])
    assert "Speaker 2" in overlap_word.get("overlapping_speakers", [])


def test_transcriber_calls_restored_alignment_with_two_speakers():
    """Exercise the normal diarization-to-alignment call, not only its helper."""
    tx = LocalMLXAudioTranscriber(mock=True)
    tx.diarization_engine = MagicMock()
    tx.diarization_engine.diarize.return_value = DummyDiarization(
        [(0.0, 1.0, "Speaker 1"), (1.0, 2.0, "Speaker 2")]
    )
    segments = [{
        "start": 0.0,
        "end": 2.0,
        "text": "Hello world",
        "words": [
            {"word": "Hello", "start": 0.1, "end": 0.9},
            {"word": " world", "start": 1.1, "end": 1.9},
        ],
    }]

    aligned = tx._diarize_and_align(segments, "two_speakers.wav", metadata=None)

    assert [(part["speaker"], part["text"]) for part in aligned] == [
        ("Speaker 1", "Hello"),
        ("Speaker 2", "world"),
    ]


# ---------------------------------------------------------------------------
# Test 4: Short-Turn & Unknown Retention
# ---------------------------------------------------------------------------
def test_short_unknown_acknowledgments_retained_in_formatted_and_structured():
    """Verifies that 'thank you', 'thanks', and 'okay' with Unknown speaker are preserved."""
    tx = DummyTranscriber()
    segments = [
        {"start": 0.0, "end": 1.2, "speaker": "Unknown", "text": "Thank you."},
        {"start": 1.5, "end": 2.0, "speaker": "Unknown", "text": "Okay."},
        {"start": 2.5, "end": 5.0, "speaker": "Speaker 1", "text": "Let us review the plan."},
    ]

    formatted = tx._format_diarized_json({"segments": segments})
    assert "Thank you." in formatted, "Short Unknown 'Thank you.' must NOT be pruned"
    assert "Okay." in formatted, "Short Unknown 'Okay.' must NOT be pruned"
    assert "Let us review the plan." in formatted

    structured = tx._build_structured_turns(segments)
    assert len(structured) == 3
    assert structured[0]["text"] == "Thank you."
    assert structured[0]["speaker"] == "Unknown"
    assert structured[0]["duration"] == 1.2


# ---------------------------------------------------------------------------
# Test 5: Anti-Placeholder Self-Resolution Firewall
# ---------------------------------------------------------------------------
def test_partner_advisory_speaker_self_mapping_rejected_as_undiarized():
    """Verifies that speaker_map={'PartnerAdvisory Speaker': 'PartnerAdvisory Speaker'} does NOT qualify as diarized."""
    transcript = (
        "**Amit Ayer** [00:00:00]: Welcome everyone.\n\n"
        "**PartnerAdvisory Speaker** [00:00:10]: Yes, I agree.\n"
    )
    # Generic speaker detected: 'PartnerAdvisory Speaker'
    # Self-mapped:
    speaker_map = {"PartnerAdvisory Speaker": "PartnerAdvisory Speaker"}

    has_unmapped = _has_unmapped_generic_speakers(transcript, speaker_map)
    assert has_unmapped is True, "Self-mapping to 'PartnerAdvisory Speaker' must be classified as unmapped generic speaker"

    modality = _classify_audio_modality(
        transcript_text=transcript,
        diar_status="NATIVE",
        generic_matches=1,
        speaker_map=speaker_map,
        roster_count=4,
        unique_speakers={"Amit Ayer", "PartnerAdvisory Speaker"},
    )
    assert modality == SourceModality.AUDIO_ONLY_UNDIARIZED, (
        "Self-mapped placeholder must fail closed to AUDIO_ONLY_UNDIARIZED even if diar_status is NATIVE"
    )


def test_attribution_engine_find_unmapped_rejects_placeholder_self_mapping():
    """Verifies that AttributionEngine._find_unmapped_generic_speakers excludes self-mapped placeholders."""
    transcript = "**Speaker 1**: Hello.\n**PartnerAdvisory Speaker**: Hi."
    existing_map = {"PartnerAdvisory Speaker": "PartnerAdvisory Speaker"}

    unmapped = AudioSpeakerAttributionEngine._find_unmapped_generic_speakers(transcript, existing_map)
    assert "PartnerAdvisory Speaker" in unmapped, "PartnerAdvisory Speaker mapped to itself must remain in the unmapped queue"


# ---------------------------------------------------------------------------
# Test 6: Calibrated Bijective Deduction
# ---------------------------------------------------------------------------
def test_bijective_deduction_bounded_below_unity():
    """Verifies that bijective roster deduction assigns calibrated 0.88, not ungrounded 1.0."""
    store = MagicMock()
    engine = AudioSpeakerAttributionEngine(decision_store=store)

    roster = [("Amit Ayer", "PartnerAdvisory"), ("Jake Stark", "PartnerAdvisory")]
    assigned = {"Amit Ayer"}  # Jake Stark is the sole remaining candidate

    candidates = engine._score_candidates_for_speaker(
        spk_id="2",
        transcript="**Speaker 2**: I am working on the UI.",
        roster=roster,
        metadata={},
        assigned_attendees=assigned,
        total_clusters=2,
    )

    assert len(candidates) == 1
    cand = candidates[0]
    assert cand.attendee_name == "Jake Stark"
    assert cand.confidence == 0.88, "Bijective deduction confidence must be calibrated at 0.88, never ungrounded 1.0"
    assert cand.confidence_tier == "HIGH"
    assert cand.is_recommended is True
