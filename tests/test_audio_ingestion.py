import os

import pytest

from dual_pipeline.audio_ingestion import (
    LocalFasterWhisperTranscriber,
    LocalMLXAudioTranscriber,
    OpenAIAudioTranscriber,
    WhisperXAudioTranscriber,
    get_default_transcriber,
)


def test_transcriber_initialization():
    transcriber = LocalFasterWhisperTranscriber()
    assert transcriber.model_size == "large-v3"
    assert transcriber.device == "cpu"
    assert transcriber.compute_type == "int8"


def test_get_default_transcriber():
    # Explicit backend requests
    fw = get_default_transcriber(backend="faster-whisper")
    assert isinstance(fw, LocalFasterWhisperTranscriber)

    mlx = get_default_transcriber(backend="mlx")
    assert isinstance(mlx, LocalMLXAudioTranscriber)

    # Auto resolution
    auto_transcriber = get_default_transcriber(backend="auto")
    from dual_pipeline.audio_ingestion import is_mlx_available
    if is_mlx_available():
        assert isinstance(auto_transcriber, LocalMLXAudioTranscriber)
    else:
        assert isinstance(auto_transcriber, LocalFasterWhisperTranscriber)


def test_get_default_transcriber_fallback_when_mlx_unavailable(monkeypatch):
    import dual_pipeline.audio_ingestion as ai
    monkeypatch.setattr(ai, "is_mlx_available", lambda: False)
    transcriber = ai.get_default_transcriber(backend="auto")
    assert isinstance(transcriber, ai.LocalFasterWhisperTranscriber)


def test_format_diarized_json():
    transcriber = LocalFasterWhisperTranscriber()

    # Mock response data conforming to what we expect from diarized_json
    response_data = {
        "segments": [
            {
                "speaker": "Speaker A",
                "start": 10.5,
                "text": "Hello, how are you?"
            },
            {
                "speaker": "Speaker B",
                "start": 15.0,
                "text": "I am doing well, thank you."
            },
            {
                "speaker": "Speaker A",
                "start": 3615.0,  # 1 hour, 15 seconds
                "text": "That's good."
            }
        ]
    }

    formatted_transcript = transcriber._format_diarized_json(response_data)

    assert "**Speaker A** [00:00:10]: Hello, how are you?" in formatted_transcript
    assert "**Speaker B** [00:00:15]: I am doing well, thank you." in formatted_transcript
    assert "**Speaker A** [01:00:15]: That's good." in formatted_transcript


def test_mock_transcribe_without_api_key():
    transcriber = LocalFasterWhisperTranscriber(mock=True)

    # Create a temporary file for the mock to read
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(b"fake audio")
        tmp_path = f.name

    try:
        transcript = transcriber.transcribe(tmp_path)
        assert "mock audio transcript" in transcript
    finally:
        os.unlink(tmp_path)


def test_detect_silence_cutpoints_nominal(monkeypatch):
    """Verify that detect_silence_cutpoints returns target cutpoints when no silence is found."""
    import subprocess
    from unittest.mock import MagicMock

    from dual_pipeline.audio_ingestion import detect_silence_cutpoints

    mock_run = MagicMock(return_value=MagicMock(stderr="No silence detected here"))
    monkeypatch.setattr(subprocess, "run", mock_run)

    cuts = detect_silence_cutpoints("/fake/audio.mp3", total_seconds=650.0, chunk_duration=300.0)
    assert cuts == [0.0, 300.0, 600.0, 650.0]


def test_detect_silence_cutpoints_with_silence(monkeypatch):
    """Verify that detect_silence_cutpoints picks natural silence intervals within search window."""
    import subprocess
    from unittest.mock import MagicMock

    from dual_pipeline.audio_ingestion import detect_silence_cutpoints

    sample_stderr = """
    [silencedetect @ 0x123] silence_start: 294.5
    [silencedetect @ 0x123] silence_end: 297.5 | silence_duration: 3.0
    [silencedetect @ 0x123] silence_start: 593.0
    [silencedetect @ 0x123] silence_end: 595.0 | silence_duration: 2.0
    """
    mock_run = MagicMock(return_value=MagicMock(stderr=sample_stderr))
    monkeypatch.setattr(subprocess, "run", mock_run)

    cuts = detect_silence_cutpoints("/fake/audio.mp3", total_seconds=850.0, chunk_duration=300.0, search_window=15.0)
    assert cuts == [0.0, 296.0, 594.0, 850.0]


def test_stitch_and_align_segments():
    """Verify that stitch_and_align_segments reconciles speaker labels across chunks and deduplicates."""
    from dual_pipeline.audio_ingestion import stitch_and_align_segments

    c0 = {
        "chunk_index": 0,
        "slice_start": 0.0,
        "slice_end": 296.0,
        "authoritative_start": 0.0,
        "authoritative_end": 296.0,
        "raw_segments": [
            {"start": 10.0, "end": 20.0, "speaker": "Speaker 1", "text": "Hello team."},
            {"start": 285.0, "end": 294.0, "speaker": "Speaker 2", "text": "Signing off chunk 0."},
        ]
    }
    c1 = {
        "chunk_index": 1,
        "slice_start": 281.0,
        "slice_end": 594.0,
        "authoritative_start": 296.0,
        "authoritative_end": 594.0,
        "raw_segments": [
            {"start": 4.0, "end": 13.0, "speaker": "Speaker B", "text": "Signing off chunk 0."},  # global 285.0 - 294.0
            {"start": 16.0, "end": 25.0, "speaker": "Speaker B", "text": "Starting chunk 1."},    # global 297.0 - 306.0
            {"start": 30.0, "end": 40.0, "speaker": "Speaker C", "text": "New speaker here."},    # global 311.0 - 321.0
        ]
    }

    stitched = stitch_and_align_segments([c0, c1], overlap_seconds=15.0)
    assert len(stitched) == 4
    assert stitched[0]["speaker"] == "Speaker 1"
    assert stitched[1]["speaker"] == "Speaker 2"
    assert stitched[1]["text"] == "Signing off chunk 0."
    assert stitched[2]["speaker"] == "Speaker 2"
    assert stitched[2]["text"] == "Starting chunk 1."
    assert stitched[3]["speaker"] == "Speaker 3"
    assert stitched[3]["text"] == "New speaker here."


def test_live_transcribe_with_mocked_mlx_whisper(monkeypatch, tmp_path):
    """Verify live transcription flow with mocked mlx_whisper."""
    import os
    import sys
    from pathlib import Path
    from unittest.mock import MagicMock

    monkeypatch.setattr(os, "environ", {"HF_TOKEN": "dummy-token"})

    transcriber = LocalMLXAudioTranscriber()

    fake_audio = tmp_path / "meeting.mp3"
    fake_audio.write_bytes(b"RIFFdummybytes")

    # Mock pyannote.audio
    mock_pipeline = MagicMock()
    mock_pipeline_instance = MagicMock()

    class MockTurn:
        def __init__(self, start, end):
            self.start = start
            self.end = end

    mock_pipeline_instance.return_value.itertracks.return_value = [
        (MockTurn(1.0, 5.0), None, "Speaker 1")
    ]
    mock_pipeline.from_pretrained.return_value = mock_pipeline_instance

    sys.modules["pyannote"] = MagicMock()
    sys.modules["pyannote.audio"] = MagicMock(Pipeline=mock_pipeline)

    # Mock mlx_whisper
    mock_mlx_whisper = MagicMock()
    mock_mlx_whisper.transcribe.return_value = {
        "segments": [
            {"start": 1.0, "end": 5.0, "text": "Welcome everyone."}
        ]
    }
    sys.modules["mlx_whisper"] = mock_mlx_whisper

    cache_file = Path(__file__).resolve().parents[1] / "output" / "transcript_cache" / f"{fake_audio.stem}.transcript_cache.txt"
    try:
        transcript = transcriber.transcribe(str(fake_audio))
        assert "**Speaker 1** [00:00:01]: Welcome everyone." in transcript
    finally:
        if cache_file.exists():
            cache_file.unlink()


def test_file_not_found_raises_error():
    transcriber = LocalFasterWhisperTranscriber()
    with pytest.raises(FileNotFoundError, match="Audio file not found"):
        transcriber.transcribe("/nonexistent/path/recording.mp3")


def test_openai_cloud_egress_guard(monkeypatch, tmp_path):
    """Cloud egress guard must raise PermissionError when ALLOW_CLOUD_AUDIO_EGRESS is not enabled."""
    monkeypatch.delenv("ALLOW_CLOUD_AUDIO_EGRESS", raising=False)
    transcriber = OpenAIAudioTranscriber(api_key="sk-test-key")

    audio_file = tmp_path / "recording.mp3"
    audio_file.write_bytes(b"dummy")

    with pytest.raises(PermissionError, match="Cloud audio transcription egress is forbidden"):
        transcriber.transcribe(str(audio_file))


def test_openai_oversized_file_raises_error_when_egress_allowed(monkeypatch, tmp_path):
    """OpenAI API enforces 25MB limit when cloud egress is explicitly authorized."""
    monkeypatch.setenv("ALLOW_CLOUD_AUDIO_EGRESS", "1")
    transcriber = OpenAIAudioTranscriber(api_key="sk-test-key")

    big_file = tmp_path / "huge_recording.mp3"
    big_file.write_bytes(b"\x00" * (26 * 1024 * 1024))

    with pytest.raises(ValueError, match="exceeds the OpenAI API limit"):
        transcriber.transcribe(str(big_file))


def test_local_transcriber_handles_oversized_file(tmp_path):
    """Local on-prem transcribers have NO 25MB limit."""
    transcriber = LocalFasterWhisperTranscriber(mock=True)

    big_file = tmp_path / "huge_recording.mp3"
    big_file.write_bytes(b"\x00" * (26 * 1024 * 1024))

    # Should NOT raise ValueError: local on-prem processing handles arbitrary sizes
    transcript = transcriber.transcribe(str(big_file))
    assert "mock audio transcript" in transcript


def test_transcribe_signal_emits_valid_envelope(tmp_path):
    transcriber = LocalFasterWhisperTranscriber(mock=True)
    audio_file = tmp_path / "sample_meeting.mp3"
    audio_file.write_bytes(b"dummy audio content for test")

    envelope = transcriber.transcribe_signal(str(audio_file), metadata={"no_cache": True})
    from dual_pipeline.audio_contracts import ForensicSignalEnvelope
    assert isinstance(envelope, ForensicSignalEnvelope)
    assert len(envelope.turns) > 0
    assert envelope.canonical_sample_rate_hz == 16000
    assert envelope.signal_digest is not None
    assert len(envelope.signal_digest) == 64
    rendered = envelope.render_verbatim_transcript()
    assert "Speaker" in rendered


def test_audio_transcribers_ssot_model_resolution(monkeypatch):
    """Verify all audio transcribers resolve models dynamically from ModelRegistry SSOT and honor env overrides."""
    # Defaults
    fw = LocalFasterWhisperTranscriber()
    assert fw.model_size == "large-v3"

    mlx = LocalMLXAudioTranscriber()
    assert mlx.model_name == "mlx-community/whisper-large-v3-mlx"

    openai_stt = OpenAIAudioTranscriber(mock=True)
    assert openai_stt.model == "blocked_by_security_policy"

    wx = WhisperXAudioTranscriber(mock=True)
    assert wx.model_size == "large-v3"

    # Overrides via environment
    monkeypatch.setenv("ACOUSTIC_FASTER_WHISPER_MODEL", "distil-large-v3")
    monkeypatch.setenv("ACOUSTIC_MLX_WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo")
    monkeypatch.setenv("CLOUD_AUDIO_TRANSCRIBE_MODEL", "whisper-1")
    monkeypatch.setenv("ACOUSTIC_WHISPERX_MODEL", "medium.en")

    fw_over = LocalFasterWhisperTranscriber()
    assert fw_over.model_size == "distil-large-v3"

    mlx_over = LocalMLXAudioTranscriber()
    assert mlx_over.model_name == "mlx-community/whisper-large-v3-turbo"

    openai_stt_over = OpenAIAudioTranscriber(mock=True)
    assert openai_stt_over.model == "whisper-1"

    wx_over = WhisperXAudioTranscriber(mock=True)
    assert wx_over.model_size == "medium.en"
