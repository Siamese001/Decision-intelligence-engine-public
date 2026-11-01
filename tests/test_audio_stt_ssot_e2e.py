#!/usr/bin/env python3
"""
tests/test_audio_stt_ssot_e2e.py

End-to-End Integration and Contract Validation Suite for:
1. Acoustic Speech-to-Text and Diarization SSOT Model Registry Binding.
2. Dynamic Environment Variable Overrides for on-prem and cloud audio backends.
3. Unification of gpt-5.6-luna (high) and gpt-5.6-luna (medium) synthesis execution.
4. Zero Hardcoding Verification across Transcribers, Synthesizers, and Agents.
"""
import os
import unittest
from unittest import mock

from dual_pipeline.audio_ingestion import (
    LocalFasterWhisperTranscriber,
    LocalMLXAudioTranscriber,
    OpenAIAudioTranscriber,
    WhisperXAudioTranscriber,
)
from dual_pipeline.model_registry import (
    ModelTier,
    get_tier_spec,
    reload_registry,
    resolve_tier_model,
)
from dual_pipeline.transcript_pipeline_core.pipeline import RawTranscriptPipeline
from dual_pipeline.transcript_stages_core.synthesizers import LLMTranscriptSynthesizer
from pipeline_a_intake.agents.audio_forensics_agent import (
    AudioForensicsAgent,
    get_audio_forensics_card,
)


class TestAudioSTTSSOTE2E(unittest.TestCase):
    def setUp(self):
        reload_registry()

    def test_acoustic_model_tier_ssot_declarations(self):
        """Verify all acoustic and transcription tiers exist in ModelRegistry with valid specs."""
        for tier in (
            ModelTier.ACOUSTIC_MLX_WHISPER,
            ModelTier.ACOUSTIC_FASTER_WHISPER,
            ModelTier.ACOUSTIC_WHISPERX,
            ModelTier.ACOUSTIC_DIARIZATION,
            ModelTier.CLOUD_AUDIO_TRANSCRIBE,
        ):
            spec = get_tier_spec(tier)
            self.assertIn("default_model", spec)
            self.assertIn("provider", spec)
            self.assertTrue(len(spec["default_model"]) > 0)

    def test_acoustic_transcriber_defaults_and_env_overrides(self):
        """Verify transcribers default to ModelRegistry and honor environment overrides."""
        # 1. Defaults
        fw = LocalFasterWhisperTranscriber()
        self.assertEqual(fw.model_size, "large-v3")

        mlx = LocalMLXAudioTranscriber()
        self.assertEqual(mlx.model_name, "mlx-community/whisper-large-v3-mlx")

        openai_stt = OpenAIAudioTranscriber(mock=True)
        self.assertEqual(openai_stt.model, "blocked_by_security_policy")

        wx = WhisperXAudioTranscriber(mock=True)
        self.assertEqual(wx.model_size, "large-v3")

        # 2. Overrides via environment
        env_overrides = {
            "ACOUSTIC_FASTER_WHISPER_MODEL": "distil-large-v3",
            "ACOUSTIC_MLX_WHISPER_MODEL": "mlx-community/whisper-large-v3-turbo",
            "CLOUD_AUDIO_TRANSCRIBE_MODEL": "whisper-1",
            "ACOUSTIC_WHISPERX_MODEL": "medium.en",
        }
        with mock.patch.dict(os.environ, env_overrides):
            fw_over = LocalFasterWhisperTranscriber()
            self.assertEqual(fw_over.model_size, "distil-large-v3")

            mlx_over = LocalMLXAudioTranscriber()
            self.assertEqual(mlx_over.model_name, "mlx-community/whisper-large-v3-turbo")

            openai_over = OpenAIAudioTranscriber(mock=True)
            self.assertEqual(openai_over.model, "whisper-1")

            wx_over = WhisperXAudioTranscriber(mock=True)
            self.assertEqual(wx_over.model_size, "medium.en")

    def test_audio_forensics_agent_card_binding(self):
        """Verify Agent-A1 dynamically binds model from ModelRegistry."""
        card = get_audio_forensics_card()
        self.assertEqual(card.model_binding.model_id, "mlx-community/whisper-large-v3-mlx")
        self.assertEqual(card.model_binding.provider, "mlx")

        with mock.patch.dict(os.environ, {"ACOUSTIC_MLX_WHISPER_MODEL": "mlx-community/whisper-base-mlx"}):
            reload_registry()
            agent = AudioForensicsAgent(mock_mode=True)
            self.assertIsNotNone(agent.card)

    def test_synthesis_under_gpt_5_6_luna_high(self):
        """Verify LLMTranscriptSynthesizer and TranscriptPipeline execute gpt-5.6-luna with high effort."""
        with mock.patch.dict(os.environ, {
            "OPENAI_MODEL": "gpt-5.6-luna",
            "OPENAI_REASONING_EFFORT": "high",
        }):
            model, effort = resolve_tier_model(ModelTier.FRONTIER_HIGH_REASONING)
            self.assertEqual(model, "gpt-5.6-luna")
            self.assertEqual(effort, "high")

            pipeline = RawTranscriptPipeline(provider_type="offline")
            self.assertIsNotNone(pipeline)

            mock_client = mock.MagicMock()
            mock_resp = mock.MagicMock()
            mock_resp.choices = [mock.MagicMock(message=mock.MagicMock(content="normalized forensic"), finish_reason="stop")]
            mock_client.chat.completions.create.return_value = mock_resp

            from config.credentials import OpenAICredentials
            test_creds = OpenAICredentials(
                api_key="sk-mock", is_mock=False, model="gpt-5.6-luna", reasoning_effort="high",
                masked_key="sk-mock...", source="test",
            )
            synth = LLMTranscriptSynthesizer(
                model="gpt-5.6-luna",
                reasoning_effort="high",
                _client=mock_client,
                _creds=test_creds,
            )
            self.assertEqual(synth.model, "gpt-5.6-luna")
            self.assertEqual(synth.reasoning_effort, "high")

            # Test forensic normalization
            out = synth.synthesize_forensic("sys prompt", "user turn")
            self.assertEqual(out, "normalized forensic")
            mock_client.chat.completions.create.assert_called_with(
                reasoning_effort="high",
                model="gpt-5.6-luna",
                messages=mock.ANY,
                max_completion_tokens=100000,
            )

    def test_synthesis_under_gpt_5_6_luna_medium(self):
        """Verify LLMTranscriptSynthesizer and TranscriptPipeline execute gpt-5.6-luna with medium effort."""
        with mock.patch.dict(os.environ, {
            "OPENAI_MODEL": "gpt-5.6-luna",
            "OPENAI_REASONING_EFFORT": "medium",
        }):
            model, effort = resolve_tier_model(ModelTier.FRONTIER_HIGH_REASONING)
            self.assertEqual(model, "gpt-5.6-luna")
            self.assertEqual(effort, "medium")

            mock_client = mock.MagicMock()
            mock_resp = mock.MagicMock()
            mock_resp.choices = [mock.MagicMock(message=mock.MagicMock(content='{"mutations": []}'), finish_reason="stop")]
            mock_client.chat.completions.create.return_value = mock_resp

            from config.credentials import OpenAICredentials
            test_creds = OpenAICredentials(
                api_key="sk-mock", is_mock=False, model="gpt-5.6-luna", reasoning_effort="medium",
                masked_key="sk-mock...", source="test",
            )
            synth = LLMTranscriptSynthesizer(
                model="gpt-5.6-luna",
                reasoning_effort="medium",
                _client=mock_client,
                _creds=test_creds,
            )
            self.assertEqual(synth.model, "gpt-5.6-luna")
            self.assertEqual(synth.reasoning_effort, "medium")

            # Test graph extraction with medium reasoning effort
            out = synth.synthesize_graph("sys prompt", "user content")
            self.assertEqual(out, '{"mutations": []}')
            mock_client.chat.completions.create.assert_called_with(
                reasoning_effort="medium",
                model="gpt-5.6-luna",
                messages=mock.ANY,
                max_completion_tokens=100000,
                response_format={"type": "json_object"},
            )


if __name__ == "__main__":
    unittest.main()
