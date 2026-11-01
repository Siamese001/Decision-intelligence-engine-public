import struct
from pathlib import Path

import pytest

from dual_pipeline.exceptions import AudioContainerValidationError, TranscriptEmptyError
from dual_pipeline.transcript_pipeline_core.pipeline import RawTranscriptPipeline


@pytest.fixture
def temp_output_dir(tmp_path: Path) -> str:
    out = tmp_path / "output"
    out.mkdir()
    return str(out)


def test_e2e_pipeline_rejects_zero_byte_wav(tmp_path: Path, temp_output_dir: str):
    """
    Adversarial test: Zero byte WAV file should fail the pre-flight guardrail instantly
    with an AudioContainerValidationError.
    """
    audio_path = tmp_path / "zero_byte.wav"
    audio_path.write_bytes(b"")

    pipeline = RawTranscriptPipeline(provider_type="offline")

    with pytest.raises(AudioContainerValidationError, match="is truncated \\(0 bytes\\)"):
        pipeline.run(
            input_path=str(audio_path),
            metadata={"meeting_name": "Test", "date": "01/01/2023"},
            output_dir=temp_output_dir,
            mutate_graph=False,
        )


def test_e2e_pipeline_rejects_butchered_44_byte_wav(tmp_path: Path, temp_output_dir: str, monkeypatch: pytest.MonkeyPatch):
    """
    Adversarial test: A WAV file containing exactly 44 bytes (just the canonical header)
    with zero audio data should fail transcription validation cleanly before hitting LLMs.
    """
    audio_path = tmp_path / "header_only.wav"
    # Construct a valid but empty 44-byte WAV header
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36, b"WAVE", b"fmt ", 16, 1, 1, 16000, 32000, 2, 16, b"data", 0
    )
    audio_path.write_bytes(header)

    import dual_pipeline.audio_ingestion_core.base as base
    monkeypatch.setattr(base.BaseAudioTranscriber, "_mock_transcribe", lambda self, audio_file_path: "")

    pipeline = RawTranscriptPipeline(provider_type="offline")

    with pytest.raises(TranscriptEmptyError, match="Transcriber returned empty text"):
        pipeline.run(
            input_path=str(audio_path),
            metadata={"meeting_name": "Test", "date": "01/01/2023", "time": "09:00 AM - 10:00 AM ET"},
            output_dir=temp_output_dir,
            mutate_graph=False,
        )
