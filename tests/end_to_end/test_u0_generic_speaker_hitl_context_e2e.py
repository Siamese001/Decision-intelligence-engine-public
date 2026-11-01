"""
tests/end_to_end/test_u0_generic_speaker_hitl_context_e2e.py

Wave 2 E2E Acceptance Suite:
1. Standalone .m4a/.mp4 ingestion without pre-supplied speaker_map or speaker_hints
   preserves 3 acoustic clusters (SPEAKER_00, SPEAKER_01, SPEAKER_02) across 8 roster attendees
   and raises UnifiedPreflightCascadeError populated with 5-turn review_contexts and scored
   roster candidate options.
2. DLQ classify_failure classifies UnifiedPreflightCascadeError and SpeakerAttributionCollisionError
   as FailureCategory.HUMAN_WAIT rather than FailureCategory.UNKNOWN.
"""
from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from dual_pipeline.audio_ingestion_core.mlx_transcriber import LocalMLXAudioTranscriber
from dual_pipeline.canonical_pipeline.u0_ingress import (
    UnifiedPreflightCascadeError,
    batch_validate_preflight_checks,
    raise_if_preflight_failed,
)
from dual_pipeline.dlq.lifecycle import FailureCategory, classify_failure
from dual_pipeline.speaker_attribution_core.review_context_render import (
    validate_visible_speaker_context,
)
from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary


class _Turn:
    def __init__(self, start: float, end: float) -> None:
        self.start = start
        self.end = end


class _Diarization:
    def __init__(self, tracks: list[tuple[_Turn, None, str]]) -> None:
        self._tracks = tracks

    def itertracks(self, yield_label: bool = False):
        return list(self._tracks) if yield_label else [(t[0], t[1]) for t in self._tracks]


def test_standalone_mp4_without_speaker_map_triggers_hitl_with_5turn_context_e2e() -> None:
    """
    End-to-end verification from standalone .m4a/.mp4 diarization through U0IngestionBoundary:
    - 3 Pyannote speakers (SPEAKER_00, SPEAKER_01, SPEAKER_02) vs 8 roster participants.
    - Zero pre-supplied speaker_map and zero speaker_hints.
    - Verifies UnifiedPreflightCascadeError carries 5-turn review_contexts passing
      validate_visible_speaker_context, scored roster candidate options, and classifies
      as FailureCategory.HUMAN_WAIT in DLQ lifecycle.
    """
    transcriber = LocalMLXAudioTranscriber(mock=True)
    mock_engine = MagicMock()
    tracks = [
        (_Turn(0.0, 5.0), None, "SPEAKER_00"),
        (_Turn(50.0, 55.0), None, "SPEAKER_01"),
        (_Turn(100.0, 105.0), None, "SPEAKER_02"),
    ]
    mock_engine.diarize.return_value = _Diarization(tracks)
    transcriber.diarization_engine = mock_engine

    raw_segments = [
        {"start": 0.5, "end": 2.0, "text": "Welcome everyone to the Project Beacon demo for Josh."},
        {"start": 10.0, "end": 12.0, "text": "Today we are walking through the Scrum delivery milestones."},
        {"start": 20.0, "end": 22.0, "text": "Let me hand over to Brandon for the live technical demo."},
        {"start": 51.0, "end": 54.0, "text": "Thanks David, I am sharing my screen to show the Sherpa agent workflow."},
        {"start": 101.0, "end": 104.0, "text": "From our AI and data perspective, how do we measure developer adoption?"},
    ]

    metadata = {
        "project": "project_beacon",
        "meeting_name": "Sherpa Demo Josh",
        "date": "09/09/2026",
        "time": "10:00 AM - 10:30 AM ET",
        "classification": "internal",
        "source_type": "AUDIO",
        "input_ext": ".m4a",
        "attendees": [
            {"name": "logan Miller", "role": "Chief AI and Data Officer", "org": "Apex Global Enterprises"},
            {"name": "David Childers", "role": "Scrum Master", "org": "Apex Global Enterprises"},
            {"name": "Brandon Que", "role": "Project Lead", "org": "Apex Global Enterprises"},
            {"name": "Speaker_Alpha Muschett", "role": "Enterprise Architect", "org": "Apex Global Enterprises"},
            {"name": "Marcus Balk", "role": "Enterprise Architect", "org": "Apex Global Enterprises"},
            {"name": "Catalina Orostegui", "role": "Engineer", "org": "Apex Global Enterprises"},
            {"name": "Egle Tamasevice", "role": "Tech Lead", "org": "Apex Global Enterprises"},
            {"name": "Amit Ayer", "role": "Lead Enterprise Architect", "org": "PartnerAdvisory"},
        ],
        "attendees_confirmed_by_operator": True,
        "attendee_roles_confirmed_by_operator": True,
        "attendee_confirmation_receipt": {
            "status": "RESOLVED_HITL",
            "subject_sha256": "sherpa_demo_josh_sha256",
        },
        "source_file_hash": "sherpa_demo_josh_sha256",
    }

    aligned_segments = transcriber._diarize_and_align(
        raw_segments,
        "Sherpa demo Josh 9.9.2026.m4a",
        metadata,
    )
    metadata["structured_utterances"] = aligned_segments
    raw_transcript = "\n".join(
        f"[{int(s['start'] // 60):02d}:{int(s['start'] % 60):02d}] **{s['speaker']}:** {s['text']}"
        for s in aligned_segments
    )

    u0 = U0IngestionBoundary()
    with pytest.raises(UnifiedPreflightCascadeError) as exc_info:
        u0.execute(raw_transcript, metadata)

    err = exc_info.value
    assert "speaker_map" in err.missing_fields
    assert len(err.review_contexts) == 3, f"Expected 3 speaker review contexts, got {len(err.review_contexts)}"

    clusters_in_contexts = {ctx.get("speaker_cluster") for ctx in err.review_contexts}
    assert clusters_in_contexts == {"SPEAKER_00", "SPEAKER_01", "SPEAKER_02"}

    for ctx in err.review_contexts:
        prompt_text = ctx["prompt"]
        validate_visible_speaker_context(prompt_text)
        assert "TARGET" in prompt_text
        assert "**UNRESOLVED —" in prompt_text
        assert len(ctx.get("candidates", [])) >= 3

    speaker_opts = [opt for opt in err.options if str(opt.get("field_name", "")).startswith("speaker_map:")]
    assert len(speaker_opts) >= 3

    assert classify_failure(err) == FailureCategory.HUMAN_WAIT
