"""Unit tests for Wave 1: Diarization credentials propagation and silent fallback fixes."""

from unittest.mock import MagicMock, patch

from config.credentials import CredentialManager, ensure_ssot_env_loaded
from dual_pipeline.audio_ingestion_core.mlx_transcriber import LocalMLXAudioTranscriber
from dual_pipeline.model_registry import ModelRegistry, ModelTier


def test_ssot_env_loads_hf_token(monkeypatch):
    """Verifies that ensure_ssot_env_loaded correctly resolves HF_TOKEN if present in env_di."""
    from config.credentials import invalidate_credentials
    monkeypatch.delenv("HF_TOKEN", raising=False)
    invalidate_credentials()
    ensure_ssot_env_loaded()
    token = CredentialManager.get_credential("HF_TOKEN")
    assert token is not None, "HF_TOKEN should be resolved from env_di"
    assert token.startswith("hf_"), f"Unexpected HF_TOKEN format: {token[:6]}"


def test_model_registry_warns_on_missing_hf_token(monkeypatch, caplog):
    """Verifies that ModelRegistry logs a warning when ACOUSTIC_DIARIZATION tier is requested without HF_TOKEN."""
    monkeypatch.setenv("HF_TOKEN", "")
    with patch("config.credentials.CredentialManager.get_credential", return_value=None):
        spec = ModelRegistry.get_tier_spec(ModelTier.ACOUSTIC_DIARIZATION)
        assert spec["default_model"] == "pyannote/speaker-diarization-3.1"
        assert any("ACOUSTIC_DIARIZATION" in record.message and "HF_TOKEN" in record.message for record in caplog.records)


def test_mlx_transcriber_records_fallback_metadata_when_token_missing(monkeypatch, tmp_path):
    """Verifies that MLXTranscriber fails fast with DiarizationInitializationError when HF_TOKEN is missing."""
    import pytest

    import dual_pipeline.audio_ingestion_core.diarization_engine as de
    from dual_pipeline.audio_ingestion_core.diarization_engine import DiarizationInitializationError

    monkeypatch.setattr(de, "_GLOBAL_DIARIZATION_ENGINE", None)
    monkeypatch.setenv("HF_TOKEN", "")
    with (
        patch("config.credentials.CredentialManager.get_credential", return_value=None),
        pytest.raises(DiarizationInitializationError, match="HF_TOKEN is missing"),
    ):
        LocalMLXAudioTranscriber(mock=False)


def test_mlx_transcriber_records_structured_error_on_load_failure(monkeypatch, tmp_path):
    """Verifies that MLXTranscriber fails fast with DiarizationInitializationError on pipeline load failure."""
    import pytest

    import dual_pipeline.audio_ingestion_core.diarization_engine as de
    from dual_pipeline.audio_ingestion_core.diarization_engine import DiarizationInitializationError

    monkeypatch.setattr(de, "_GLOBAL_DIARIZATION_ENGINE", None)
    with (
        patch("config.credentials.CredentialManager.get_credential", return_value="hf_fake_token"),
        patch("pyannote.audio.Pipeline.from_pretrained", side_effect=RuntimeError("Mock pyannote CUDA OOM")),
        pytest.raises(DiarizationInitializationError, match="Mock pyannote CUDA OOM"),
    ):
        LocalMLXAudioTranscriber(mock=False)


def test_isolated_transcriber_start_calls_ensure_ssot_env_loaded():
    """Verifies that IsolatedMLXTranscriberClient calls ensure_ssot_env_loaded before spawning."""
    from dual_pipeline.live_meeting_recorder_core.isolated_transcriber import (
        IsolatedMLXTranscriberClient,
    )

    client = IsolatedMLXTranscriberClient(mock_mode=True)
    with (
        patch("config.credentials.ensure_ssot_env_loaded") as mock_ensure,
        patch.object(client._ctx, "Process"),
        patch.object(client._ctx, "Event"),
    ):
        mock_output_q = MagicMock()
        mock_output_q.get.return_value = {"type": "READY", "pid": 99999}
        with patch.object(client._ctx, "Queue", return_value=mock_output_q):
            client.start(timeout=1.0)
            mock_ensure.assert_called()


def test_torch_load_bridge_is_scoped():
    """Verifies that torch.load is restored to its original function after DiarizationEngine.initialize()."""
    import torch

    from dual_pipeline.audio_ingestion_core.diarization_engine import _scoped_torch_load_bridge

    orig_load = torch.load

    # Inside scoped context, torch.load is patched with weights_only=False
    with _scoped_torch_load_bridge():
        assert torch.load != orig_load

    # Outside scoped context, torch.load is strictly restored
    assert torch.load == orig_load
