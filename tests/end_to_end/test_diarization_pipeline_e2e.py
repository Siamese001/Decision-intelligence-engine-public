"""End-to-End Test: Diarization Pipeline Lifecycle.

Exercises the full lifecycle from audio capture / segment ingestion,
diarization status detection, normalization of acoustic labels, and stage U0 gate clearance.
"""


from dual_pipeline.audio_ingestion_core.base import BaseAudioTranscriber
from dual_pipeline.speaker_disambiguation_core.disambiguator import SpeakerDisambiguator
from dual_pipeline.speaker_disambiguation_core.models import AttendeeContext, RoomMicConfig
from dual_pipeline.transcript_pipeline_core.source_modality import (
    SourceModality,
    classify_source_modality,
)
from dual_pipeline.transcript_stages_core.attendee_gate import extract_candidate_attendees
from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary


class E2EAudioTranscriber(BaseAudioTranscriber):
    def _live_transcribe(self, audio_file_path: str, metadata=None) -> str:
        return "dummy transcript"


def test_diarization_formatting_pipeline():
    """Verifies formatting and normalization steps with simulated segment inputs."""
    # 1. Simulate acoustic model output with raw SPEAKER_00 / SPEAKER_01 IDs
    raw_acoustic_segments = [
        {"speaker": "SPEAKER_00", "start": 0.0, "end": 4.0, "text": "Good morning everyone, let's review the system architecture."},
        {"speaker": "SPEAKER_01", "start": 5.0, "end": 9.0, "text": "Thanks Amit, I will walk through the transmission system numbers."},
        {"speaker": "SPEAKER_00", "start": 10.0, "end": 14.0, "text": "Sounds great Daniel, please go ahead."},
    ]

    # 2. Formatter converts raw SPEAKER_XX to clean sequential Speaker N
    transcriber = E2EAudioTranscriber()
    formatted_transcript = transcriber._format_diarized_json({"segments": raw_acoustic_segments})

    assert "SPEAKER_00" not in formatted_transcript
    assert "SPEAKER_01" not in formatted_transcript
    assert "**Speaker 1** [00:00:00]: Good morning everyone" in formatted_transcript
    assert "**Speaker 2** [00:00:05]: Thanks Amit" in formatted_transcript

    # 3. Stage U0 diarization detection
    metadata = {
        "platform": "Microsoft Teams",
        "has_acoustic_diarization": True,
        "_from_audio_transcription": True,
        "source_type": "AUDIO_DIARIZED",
        "roster": [
            {"name": "Amit Ayer", "role": "Lead Enterprise Architect", "org": "PartnerAdvisory"},
            {"name": "Daniel Brankin", "role": "Engineering Lead", "org": "Apex Global"},
        ],
    }
    diar_status = U0IngestionBoundary._detect_diarization_status(formatted_transcript, metadata)
    assert diar_status == "DIARIZED_ACOUSTIC"

    # 4. Modality classification
    modality = classify_source_modality(
        transcript_text=formatted_transcript,
        metadata=metadata,
        input_ext=".wav",
    )
    assert modality in (SourceModality.AUDIO_ONLY_UNDIARIZED, SourceModality.AUDIO_DIARIZED)

    # 5. Candidate attendee extraction ensures no leaked Speaker tags
    candidates = extract_candidate_attendees(metadata, formatted_transcript)
    assert "Speaker 1" not in candidates
    assert "Speaker 2" not in candidates
    assert "SPEAKER_00" not in candidates
    assert "Amit Ayer" in candidates
    assert "Daniel Brankin" in candidates

    # 6. Speaker disambiguation resolves generic speakers to confirmed attendees
    in_room_attendees = [
        AttendeeContext(
            name="Amit Ayer",
            person_id="person:amit-ayer",
            company="PartnerAdvisory",
            role="Lead Enterprise Architect",
            domain_topics=["system architecture", "standards"],
        ),
        AttendeeContext(
            name="Daniel Brankin",
            person_id="person:daniel-brankin",
            company="Apex Global",
            role="Engineering Lead",
            domain_topics=["transmission system", "solar 40"],
        ),
    ]
    room_config = RoomMicConfig(
        mic_label="Speaker 1",
        display_label="Conference Room",
        in_room_attendees=in_room_attendees,
    )
    disambiguator = SpeakerDisambiguator(mock=True)
    result = disambiguator.disambiguate(formatted_transcript, room_config)
    assert result.directly_attributed_turns >= 1
    assert "SPEAKER_00" not in result.resolved_transcript


def test_real_audio_diarization_lifecycle():
    """Verifies complete end-to-end diarization pipeline using real audio waveforms through PyAnnote."""
    from dual_pipeline.audio_ingestion_core.diarization_engine import get_diarization_engine
    from tests.fixtures.generate_diarization_fixture import generate_or_extract_fixture

    fixture_path = generate_or_extract_fixture()
    assert fixture_path.is_file(), f"Fixture missing: {fixture_path}"

    engine = get_diarization_engine()
    turns = engine.extract_speaker_turns(fixture_path)
    assert len(turns) >= 2, f"Expected >=2 turns from real audio fixture, got {len(turns)}"

    distinct_speakers = set(t["speaker"] for t in turns)
    assert len(distinct_speakers) >= 2, f"Expected >=2 distinct acoustic speakers, got {distinct_speakers}"

    # Align extracted turns into segments
    segments = []
    for idx, t in enumerate(turns):
        segments.append({
            "speaker": t["speaker"],
            "start": t["start"],
            "end": t["end"],
            "text": f"Utterance segment {idx} discussing project requirements and architecture.",
        })

    # Format through BaseAudioTranscriber
    transcriber = E2EAudioTranscriber()
    formatted = transcriber._format_diarized_json({"segments": segments})

    # Verify normalization of raw acoustic IDs
    assert "SPEAKER_00" not in formatted
    assert "SPEAKER_01" not in formatted
    assert "**Speaker 1**" in formatted
    assert "**Speaker 2**" in formatted

    metadata = {
        "platform": "Microsoft Teams",
        "has_acoustic_diarization": True,
        "_from_audio_transcription": True,
        "source_type": "AUDIO_DIARIZED",
        "roster": [
            {"name": "Taylor Mercer", "role": "Senior Consultant", "org": "PartnerAdvisory"},
            {"name": "Daniel Brankin", "role": "Engineering Lead", "org": "Apex Global"},
        ],
    }

    # Stage U0 detection
    diar_status = U0IngestionBoundary._detect_diarization_status(formatted, metadata)
    assert diar_status == "DIARIZED_ACOUSTIC"

    # Modality classification
    modality = classify_source_modality(
        transcript_text=formatted,
        metadata=metadata,
        input_ext=".wav",
    )
    assert modality in (SourceModality.AUDIO_ONLY_UNDIARIZED, SourceModality.AUDIO_DIARIZED)

    # Candidate attendees resolution
    candidates = extract_candidate_attendees(metadata, formatted)
    assert "Speaker 1" not in candidates
    assert "Speaker 2" not in candidates
    assert "Taylor Mercer" in candidates
    assert "Daniel Brankin" in candidates


def test_pinned_diarization_engine_initialization_on_transcription_start():
    """Verifies that the pinned pyannote model is initialized on startup and binds to Apple Silicon MPS."""
    import torch

    from dual_pipeline.audio_ingestion_core.diarization_engine import (
        PINNED_DIARIZATION_MODEL,
        get_diarization_engine,
    )
    from dual_pipeline.audio_ingestion_core.mlx_transcriber import LocalMLXAudioTranscriber

    engine = get_diarization_engine()
    assert engine.model_id == PINNED_DIARIZATION_MODEL
    assert engine.model_id == "pyannote/speaker-diarization-3.1"
    assert engine._initialized is True
    assert engine.pipeline is not None

    if torch.backends.mps.is_available():
        assert str(engine.pipeline.device).startswith("mps")

    # LocalMLXAudioTranscriber pre-initializes diarization engine on startup
    transcriber = LocalMLXAudioTranscriber(mock=False)
    assert transcriber.diarization_engine is not None
    assert transcriber.diarization_engine.model_id == "pyannote/speaker-diarization-3.1"


def test_diarization_engine_fails_fast_without_token(monkeypatch):
    """Verifies that missing credentials immediately raise DiarizationInitializationError with zero silent fallback."""
    import pytest

    from dual_pipeline.audio_ingestion_core.diarization_engine import (
        DiarizationEngine,
        DiarizationInitializationError,
    )

    monkeypatch.setenv("HF_TOKEN", "")
    from config.credentials import CredentialManager
    monkeypatch.setattr(CredentialManager, "get_credential", lambda key, default=None: "" if key == "HF_TOKEN" else default)

    engine = DiarizationEngine(token="", lazy=True)
    with pytest.raises(DiarizationInitializationError, match="HF_TOKEN is missing"):
        engine.initialize()

