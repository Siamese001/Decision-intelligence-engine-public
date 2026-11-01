"""
tests/adversarial/test_asr_filter_false_positive_adversarial.py

Adversarial Verification Suite for Wave 7 ASR Filter Hardening.
Tests:
1. Legitimate business speech containing adversarial trigger terms ('subscribe', 'grid.example.com',
   'www.', 'discussing', roster names, 'sigh') is strictly retained.
2. Genuine credit hallucinations, YouTube CTAs, subtitle watermarks, and silence artifacts
   are cleanly rejected.
3. 100% of drops are recorded in asr_filter_log.jsonl with failure reasons.
4. Segment-level telemetry gating (no_speech_prob, compression_ratio, logprob).
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.asr_decode_config import DEFAULT_ASR_DECODE_CONFIG
from dual_pipeline.audio_postprocess import (
    filter_segments_by_telemetry,
    sanitize_transcript_text,
)


class TestASRFilterFalsePositiveAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_path = Path(self.temp_dir.name) / "asr_filter_log.jsonl"
        self.whisper_prompt = (
            "Meeting participants: Drew Oakley, Amit Ayer, Cameron Mercer. "
            "Project: APEX_TRANS. Topic: Grid Modernization. "
            "Domain terms: ROCC, SCADA, EMS, Apex Global Enterprises, APEX, Pano AI."
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _read_filter_log_records(self) -> list[dict]:
        if not self.log_path.exists():
            return []
        records = []
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
        return records

    # -------------------------------------------------------------------------
    # 1. False Positive Protection: Real business speech containing trigger terms
    # -------------------------------------------------------------------------
    def test_adversarial_phrases_with_trigger_words_retained(self) -> None:
        """Legitimate multi-word speech containing trigger keywords must NEVER be dropped."""
        legitimate_utterances = [
            "Please subscribe to our newsletter and visit our grid.example.com portal for updates.",
            "You can subscribe to the automated outage notification service.",
            "Please make sure the team subscribes to the incident response distribution list.",
            "Visit our secure customer portal at portal.io today.",
            "Ensure you bookmark the Apex Global grid.example.com resource center.",
            "You can review the updated API endpoints at www.consulting.example.com/cloud-docs.",
            "We were discussing the budget decisions for next quarter with the leadership team.",
            "Drew Oakley and Amit Ayer discussed the SCADA telemetry and EMS cutover schedule.",
            "We let out a collective sigh of relief when the secondary database failed over seamlessly.",
            "The background music at the conference hall made it difficult to hear the presenter.",
            "Our documentation portal is hosted on https://docs.enterprise.example.com.",
            "Thank you Dan for sharing the architecture slides with the committee.",
            "Thanks everyone for joining today's sprint retrospective.",
        ]

        for text in legitimate_utterances:
            sanitized = sanitize_transcript_text(
                text,
                whisper_prompt=self.whisper_prompt,
                filter_log_path=self.log_path,
                chunk_idx=1,
                chunk_file="chunk_00001.wav",
            )
            self.assertEqual(
                sanitized,
                text,
                f"Falsely dropped legitimate utterance: '{text}' -> got '{sanitized}'",
            )

        # Confirm zero drops were logged for legitimate utterances
        records = self._read_filter_log_records()
        self.assertEqual(len(records), 0, f"Expected 0 drops logged, but found: {records}")

    # -------------------------------------------------------------------------
    # 2. Genuine Credit Hallucinations on Silence Rejected
    # -------------------------------------------------------------------------
    def test_genuine_credit_and_cta_hallucinations_rejected(self) -> None:
        """Standalone YouTube endcards, CTAs, subtitle watermarks, and silence artifacts must be dropped."""
        hallucinations = [
            ("Thank you for watching.", "CREDIT_HALLUCINATION"),
            ("Thanks for watching!", "CREDIT_HALLUCINATION"),
            ("Thank you for listening.", "CREDIT_HALLUCINATION"),
            ("Subtitles by the Amara.org community", "CREDIT_HALLUCINATION"),
            ("Closed captioning provided by StreamText.Net", "CREDIT_HALLUCINATION"),
            ("English subtitles provided by OpenSubtitles", "CREDIT_HALLUCINATION"),
            ("Transcribed by rev.com", "CREDIT_HALLUCINATION"),
            ("Please subscribe.", "CREDIT_HALLUCINATION"),
            ("Subscribe.", "CREDIT_HALLUCINATION"),
            ("Please subscribe to our channel.", "CREDIT_HALLUCINATION"),
            ("Like and subscribe!", "CREDIT_HALLUCINATION"),
            ("See you in the next video.", "CREDIT_HALLUCINATION"),
            ("The end.", "SILENCE_HALLUCINATION"),
            ("You're welcome.", "SILENCE_HALLUCINATION"),
            ("Goodbye.", "SILENCE_HALLUCINATION"),
            ("www.grid.example.com", "STANDALONE_URL"),
            ("https://portal.io", "STANDALONE_URL"),
            ("consulting.example.com", "STANDALONE_URL"),
            ("[applause]", "NON_SPEECH_VOCALIZATION"),
            ("[music]", "NON_SPEECH_VOCALIZATION"),
            ("(laughter)", "NON_SPEECH_VOCALIZATION"),
            ("sigh", "NON_SPEECH_VOCALIZATION"),
            ("Apex GlobalPlay", "PROMPT_BRANCH"),
            ("Apex GlobalVictory.com", "STANDALONE_URL"),
        ]

        for text, expected_reason in hallucinations:
            sanitized = sanitize_transcript_text(
                text,
                whisper_prompt=self.whisper_prompt,
                filter_log_path=self.log_path,
                chunk_idx=2,
                chunk_file="chunk_00002.wav",
            )
            self.assertEqual(
                sanitized,
                "",
                f"Failed to filter genuine artifact '{text}' (expected reason: {expected_reason})",
            )

        # Verify 100% of drops were logged to asr_filter_log.jsonl
        records = self._read_filter_log_records()
        self.assertEqual(len(records), len(hallucinations))
        for r in records:
            self.assertIn("timestamp", r)
            self.assertIn("raw_text", r)
            self.assertIn("reason", r)
            self.assertEqual(r["chunk_idx"], 2)
            self.assertEqual(r["chunk_file"], "chunk_00002.wav")

    # -------------------------------------------------------------------------
    # 3. Conditioning Prompt Echo & Repetition Loops
    # -------------------------------------------------------------------------
    def test_prompt_echo_and_repetition_loop_filtering(self) -> None:
        """Verbatim prompt echos and runaway n-gram loops must be suppressed and logged."""
        # 1. Full prompt verbatim echo
        echo_text = self.whisper_prompt
        sanitized_echo = sanitize_transcript_text(
            echo_text,
            whisper_prompt=self.whisper_prompt,
            filter_log_path=self.log_path,
            chunk_idx=3,
            chunk_file="chunk_00003.wav",
        )
        self.assertEqual(sanitized_echo, "")

        # 2. Runaway loop
        loop_text = "we need to check the data we need to check the data we need to check the data we need to check the data"
        sanitized_loop = sanitize_transcript_text(
            loop_text,
            whisper_prompt=self.whisper_prompt,
            filter_log_path=self.log_path,
            chunk_idx=4,
            chunk_file="chunk_00004.wav",
        )
        self.assertEqual(sanitized_loop, "")

        records = self._read_filter_log_records()
        reasons = [r["reason"] for r in records]
        self.assertIn("PROMPT_ECHO", reasons)
        self.assertIn("REPETITION_LOOP", reasons)

    # -------------------------------------------------------------------------
    # 4. Segment-Level Telemetry Gating
    # -------------------------------------------------------------------------
    def test_segment_telemetry_gating(self) -> None:
        """Low-confidence, silent, and compressed segments must be filtered and logged."""
        segments = [
            {"text": "Valid business utterance.", "no_speech_prob": 0.05, "compression_ratio": 1.2, "avg_logprob": -0.2},
            {"text": "Dead air silence segment.", "no_speech_prob": 0.85, "compression_ratio": 1.1, "avg_logprob": -0.3},
            {"text": "Loop loop loop loop loop.", "no_speech_prob": 0.10, "compression_ratio": 2.8, "avg_logprob": -0.4},
            {"text": "Muffled inaudible noise.", "no_speech_prob": 0.20, "compression_ratio": 1.3, "avg_logprob": -1.5},
            {"text": "Second valid sentence.", "no_speech_prob": 0.02, "compression_ratio": 1.1, "avg_logprob": -0.15},
        ]

        text = filter_segments_by_telemetry(
            segments,
            decode_config=DEFAULT_ASR_DECODE_CONFIG,
            filter_log_path=self.log_path,
            chunk_idx=5,
            chunk_file="chunk_00005.wav",
        )

        self.assertEqual(text, "Valid business utterance. Second valid sentence.")

        records = self._read_filter_log_records()
        self.assertEqual(len(records), 3)
        reasons = [r["reason"] for r in records]
        self.assertIn("HIGH_NO_SPEECH_PROB", reasons)
        self.assertIn("HIGH_COMPRESSION_RATIO", reasons)
        self.assertIn("LOW_LOGPROB", reasons)


if __name__ == "__main__":
    unittest.main()
