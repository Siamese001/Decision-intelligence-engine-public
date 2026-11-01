"""
tests/adversarial/test_fail_fast_normalizer_adversarial.py

Adversarial edge-case and regression tests verifying Wave 2 fail-fast normalizer invariants:
1. Normalizer must never return unparsed raw_text when turns cannot be extracted.
2. Empty inputs, unparsed dialogue, and pre-resolved headers without turns must raise ValueError.
3. WebVTT and Copilot JSON parsers must raise ValueError on zero extracted dialogue turns.
4. WordFloorError must be raised on retry exhaustion in recap modular DAG (no best-effort fallback).
5. Ingress must raise ValueError on corrupted speaker map sidecar.
6. Temporal resolution must fail fast when audio container duration cannot be derived (no 1800s guess).
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from dual_pipeline.temporal_resolution import (
    resolve_temporal_window,
)
from dual_pipeline.transcript_pipeline_core.format_parsers import (
    parse_copilot_json,
    parse_vtt_or_srt,
)
from dual_pipeline.transcript_pipeline_core.ingress import (
    process_u0_ingress,
)
from dual_pipeline.transcript_pipeline_core.normalizer import (
    normalize_raw_input_text,
)
from dual_pipeline.transcript_stages_core.recap_modular_dag import (
    WordFloorError,
    execute_pass_2a,
    execute_pass_2c,
)


class TestNormalizerFailFast(unittest.TestCase):
    """Verifies that normalizer raises on invalid input rather than returning raw_text."""

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError, match="Cannot normalize empty transcript input text"):
            normalize_raw_input_text("")

    def test_whitespace_only_raises_value_error(self):
        with pytest.raises(ValueError, match="Cannot normalize empty transcript input text"):
            normalize_raw_input_text("    \n\t  ")

    def test_unparseable_prose_raises_value_error(self):
        # Arbitrary unstructured text with no dialogue turns must fail fast
        unstructured_prose = (
            "This is a random blog post about software architecture.\n"
            "There are no speakers here, just paragraphs of text.\n"
            "In conclusion, modular systems are superior."
        )
        with pytest.raises(ValueError, match="Normalizer failed to parse any dialogue turns"):
            normalize_raw_input_text(unstructured_prose)

    def test_pre_resolved_header_without_turns_raises_value_error(self):
        # A file with HITL header but zero dialogue turns must fail fast
        fake_hitl_file = (
            "# Forensic Attribution Notice\n"
            "## Ambiguity Lock Register\n"
            "| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |\n"
            "| :--- | :--- | :--- | :--- | :--- | :--- |\n"
        )
        with pytest.raises(ValueError, match="Pre-resolved transcript metadata found, but file contains zero dialogue turns"):
            normalize_raw_input_text(fake_hitl_file)


class TestFormatParsersFailFast(unittest.TestCase):
    """Verifies that format parsers raise on failure rather than returning raw_text."""

    def test_vtt_without_turns_raises_value_error(self):
        vtt_no_turns = (
            "WEBVTT\n\n"
            "NOTE This is just a comment block with no cues or dialogue\n\n"
        )
        with pytest.raises(ValueError, match="Failed to parse any dialogue turns from WebVTT/SRT source"):
            parse_vtt_or_srt(vtt_no_turns)

    def test_copilot_json_empty_texts_raises_value_error(self):
        # JSON with messages array but all text fields empty
        json_empty_text = (
            '{"turns": [{"speaker": "Alice", "text": "", "timestamp": "00:00:10"}, '
            '{"speaker": "Bob", "text": "   ", "timestamp": "00:00:20"}]}'
        )
        with pytest.raises(ValueError, match="Parsed JSON payload contains no text dialogue turns"):
            parse_copilot_json(json_empty_text)


class TestIngressFailFast(unittest.TestCase):
    """Verifies that ingress raises on corrupted sidecars rather than silently swallowing."""

    def test_corrupted_speaker_map_sidecar_raises_value_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_p = Path(tmp_dir)
            transcript_p = tmp_p / "meeting.txt"
            transcript_p.write_text("[00:00:01] Alice: Hello team\n", encoding="utf-8")
            sidecar_p = tmp_p / "meeting_speaker_map.json"
            sidecar_p.write_text("{NOT_VALID_JSON: true", encoding="utf-8")

            with pytest.raises(ValueError, match="Corrupted speaker map sidecar"):
                process_u0_ingress(str(transcript_p), {
                    "project": "test_proj",
                    "meeting_name": "Test",
                    "date": "09/30/2026",
                    "time": "09:00 AM - 10:00 AM ET",
                    "classification": "restricted",
                })


class TestRecapModularDagFailFast(unittest.TestCase):
    """Verifies that recap modular DAG raises WordFloorError on retry exhaustion."""

    def test_pass_2a_retry_exhaustion_raises_word_floor_error(self):
        # Simulate LLM always returning short text below word floor
        short_response = "Short summary that is under word floor."
        with (
            patch("dual_pipeline.transcript_stages_core.recap_modular_dag.call_gemini_generate_content", return_value=short_response),
            pytest.raises(WordFloorError, match="Pass 2A failed word floor after 2 retries"),
        ):
            execute_pass_2a(model="gemini-3.8-flash", user_content="input", min_words=500, max_retries=2)

    def test_pass_2c_retry_exhaustion_raises_word_floor_error(self):
        short_response = "Short perspectives section."
        with (
            patch("dual_pipeline.transcript_stages_core.recap_modular_dag.call_gemini_generate_content", return_value=short_response),
            pytest.raises(WordFloorError, match="Pass 2C failed word floor after 2 retries"),
        ):
            execute_pass_2c(model="gemini-3.8-flash", user_content="input", min_words=600, max_retries=2)


class TestTemporalResolutionFailFast(unittest.TestCase):
    """Verifies that temporal resolution fails fast without fabricating a 1800s duration."""

    def test_audio_file_without_duration_raises_value_error_no_1800s_guess(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", prefix="meeting_090000_", delete=True) as tmp:
            tmp.write(b"NOT_A_VALID_AUDIO_CONTAINER")
            tmp.flush()

            # When audio container validator fails, resolve_temporal_window must raise RuntimeError / ValueError
            with pytest.raises((ValueError, RuntimeError)):
                resolve_temporal_window(input_path=tmp.name, meta={}, raw_text="")
