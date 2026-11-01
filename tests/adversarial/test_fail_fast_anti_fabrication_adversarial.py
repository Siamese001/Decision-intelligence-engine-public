"""
tests/adversarial/test_fail_fast_anti_fabrication_adversarial.py

Adversarial edge-case and regression tests verifying Wave 1 fail-fast anti-fabrication invariants:
1. Boundary payloads: empty audio bytes, empty/whitespace LLM responses.
2. Malformed inputs: non-existent audio files, unsupported audio types.
3. Invariant bypass attempts:
   - Whisper transcription engine must raise ImportError rather than silently falling back to mock_mode.
   - Substring match on "Neal" must not falsely attribute to Apex Global Enterprises (NEA).
   - Audio decoding failures must raise FileNotFoundError / RuntimeError rather than fabricating 1s of digital silence (np.zeros).
   - Empty/None LLM responses in synthesizers must raise RuntimeError rather than fabricating empty JSON "{}" or returning empty strings.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from dual_pipeline.transcript_metadata_core.entity_resolver import (
    resolve_company_for_person,
)
from dual_pipeline.transcript_stages_core.synthesizers import (
    LLMTranscriptSynthesizer,
)
from pipeline_a_intake.local_stt_core.audio_processor import (
    _decode_bytes,
    _decode_str,
    normalize_audio_target,
    resolve_model_path,
    transcribe_audio_window,
)
from pipeline_a_intake.local_stt_core.dual_tier_engine import DualTierTranscriber


class TestAudioFailFastAntiFabrication(unittest.TestCase):
    """Verifies that audio normalization and STT components fail fast without silence fabrication."""

    def test_empty_audio_bytes_raises_value_error(self):
        with pytest.raises(ValueError, match="Audio target bytes cannot be empty"):
            _decode_bytes(b"", {})

    def test_nonexistent_audio_file_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError, match="does not exist"):
            _decode_str("/tmp/nonexistent_audio_file_12345.wav")

    def test_unsupported_audio_type_raises_type_error(self):
        with pytest.raises(TypeError, match="Unsupported audio_target type"):
            normalize_audio_target(12345)  # type: ignore

    def test_empty_model_name_raises_value_error(self):
        with pytest.raises(ValueError, match="model_name cannot be empty"):
            resolve_model_path("")

    def test_corrupt_audio_file_raises_runtime_error(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
            tmp.write(b"NOT_A_VALID_WAV_HEADER_DATA_12345")
            tmp.flush()
            with pytest.raises(RuntimeError, match="Failed to decode audio file"):
                _decode_str(tmp.name)

    def test_whisper_transcription_exception_raises_runtime_error(self):
        mock_mlx = MagicMock()
        mock_mlx.transcribe.side_effect = RuntimeError("Metal GPU out of memory")
        dummy_audio = np.zeros(16000, dtype=np.float32)

        with pytest.raises(RuntimeError, match="Metal Whisper transcription failed"):
            transcribe_audio_window(
                audio_data=dummy_audio,
                start_ts=0.0,
                model_name="dummy-model",
                mock_mode=False,
                metadata={},
                mlx_whisper=mock_mlx,
            )

    def test_dual_tier_engine_raises_importerror_without_mock_fallback(self):
        with (
            patch.dict("sys.modules", {"mlx_whisper": None}),
            patch("builtins.__import__", side_effect=ImportError("No module named 'mlx_whisper'")),
            pytest.raises(ImportError, match="mlx_whisper is required"),
        ):
            DualTierTranscriber(mock_mode=False, mlx_whisper=None)


class TestEntityResolverFailFastAntiFabrication(unittest.TestCase):
    """Verifies entity resolution fails fast without substring fabrication."""

    def test_neal_does_not_falsely_match_nea(self):
        # "Neal" contains "nea" as substring, but must not be resolved to Apex Global Enterprises (NEA)
        with pytest.raises(ValueError, match="Cannot resolve corporate affiliation"):
            resolve_company_for_person("Neal", allow_default=False)

    def test_explicit_nea_matches_word_boundary(self):
        res = resolve_company_for_person("Neal (NEA)", allow_default=False)
        assert res == "Apex Global Enterprises (NEA)"

    def test_explicit_partner_advisory_matches_word_boundary(self):
        res = resolve_company_for_person("PartnerAdvisory Lead", allow_default=False)
        assert res == "PartnerAdvisory"


class TestSynthesizerFailFastAntiFabrication(unittest.TestCase):
    """Verifies that synthesis stages crash on empty responses without fabricating '{}' or empty text."""

    def setUp(self):
        self.synth = LLMTranscriptSynthesizer(_client=MagicMock())

    def test_forensic_empty_response_raises_runtime_error(self):
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = ""
        mock_resp.choices[0].finish_reason = "stop"

        with (
            patch.object(self.synth, "_create_completion_with_effort", return_value=mock_resp),
            pytest.raises(RuntimeError, match="OpenAI API returned empty content for forensic normalization"),
        ):
            self.synth.synthesize_forensic("system", "user")

    def test_recap_empty_response_raises_runtime_error(self):
        self.synth.d02_model = "gpt-5.6-luna"
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "   "
        mock_resp.choices[0].finish_reason = "stop"

        with (
            patch.object(self.synth, "_create_completion_with_effort", return_value=mock_resp),
            pytest.raises(RuntimeError, match="OpenAI API returned empty content for executive recap"),
        ):
            self.synth.synthesize_recap("system", "user")

    def test_metadata_empty_response_raises_runtime_error_no_fabricated_json(self):
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = None  # None response from API
        mock_resp.choices[0].finish_reason = "stop"

        with (
            patch.object(self.synth, "_create_completion_with_effort", return_value=mock_resp),
            pytest.raises(RuntimeError, match="OpenAI API returned empty content for metadata extraction"),
        ):
            self.synth.synthesize_metadata("system", "user")

    def test_agentic_leverage_empty_response_raises_runtime_error(self):
        self.synth.agentic_model = "gpt-5.4"
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = ""
        mock_resp.choices[0].finish_reason = "stop"

        with (
            patch.object(self.synth, "_create_completion_with_effort", return_value=mock_resp),
            pytest.raises(RuntimeError, match="OpenAI agentic leverage synthesis failed"),
        ):
            self.synth.synthesize_agentic_leverage("system", "user")

    def test_agentic_leverage_gemini_empty_response_raises_runtime_error(self):
        self.synth.agentic_model = "gemini-3.8-flash"
        with (
            patch("dual_pipeline.transcript_stages_core.external_synthesizer.call_gemini_generate_content", return_value=""),
            pytest.raises(RuntimeError, match="Google Gemini returned empty content for agentic leverage extraction"),
        ):
            self.synth.synthesize_agentic_leverage("system", "user")

