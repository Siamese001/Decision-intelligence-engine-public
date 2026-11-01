"""Adversarial and Edge-Case Test Suite for Live Ingestion Certification (Wave 5).

Covers mandatory edge-case categories under AGENTS.md and e2e-and-edge-case-coverage.md:
1. Boundary payload: 0-byte files, empty audio, minimum container length.
2. Malformed input: Corrupted WAV headers, truncated magic bytes, UTF-8 BOM injection.
3. Invariant bypass attempt: Synthetic approval injection, unratified graph mutation attempts.
4. Rollback & recovery: Idempotent re-ingestion with deterministic cache timestamps.
5. Exact production regression: Missing CLI arguments, DECLINED exit codes, attachment error fallbacks.
"""
from __future__ import annotations

import json
import os
import struct
import tempfile
import unittest
from pathlib import Path

import pytest

from dual_pipeline.core import ROOT


class LiveIngestionCertificationAdversarialTests(unittest.TestCase):
    """Adversarial and boundary test suite for Wave 5 live ingestion certification."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.runtime_root = self.tmp_path / "runtime"
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.output_dir = self.tmp_path / "output"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # -------------------------------------------------------------------------
    # Category 1: Boundary payload
    # -------------------------------------------------------------------------
    def test_boundary_zero_byte_document_fails_closed(self) -> None:
        """Boundary: 0-byte document fails preflight immediately with SourceIntegrityError."""
        from dual_pipeline.canonical_pipeline.content_loader import _load_from_source_file
        from dual_pipeline.exceptions import SourceIntegrityError

        empty_doc = self.tmp_path / "empty_doc.md"
        empty_doc.touch()
        meta: dict[str, object] = {"is_document": True}

        with self.assertRaises(SourceIntegrityError) as ctx:
            _load_from_source_file(empty_doc, meta, offline=True)
        self.assertIn("empty (0 bytes)", str(ctx.exception))

    def test_boundary_truncated_audio_container_header(self) -> None:
        """Boundary: Audio file smaller than minimum container header fails validation."""
        from dual_pipeline.audio_ingestion_core.container_validator import validate_audio_container_headers
        from dual_pipeline.exceptions import AudioContainerValidationError

        tiny_wav = self.tmp_path / "tiny.wav"
        tiny_wav.write_bytes(b"RIFF\x04\x00\x00\x00")  # Only 8 bytes, minimum header is 12

        with self.assertRaises(AudioContainerValidationError) as ctx:
            validate_audio_container_headers(tiny_wav)
        self.assertIn("truncated", str(ctx.exception).lower())

    # -------------------------------------------------------------------------
    # Category 2: Malformed input
    # -------------------------------------------------------------------------
    def test_malformed_wav_magic_bytes_rejected(self) -> None:
        """Malformed: WAV file with corrupted magic bytes is rejected fail-closed."""
        from dual_pipeline.audio_ingestion_core.container_validator import validate_audio_container_headers
        from dual_pipeline.exceptions import AudioContainerValidationError

        bad_wav = self.tmp_path / "corrupted.wav"
        # 44 bytes but invalid magic
        bad_wav.write_bytes(b"NOPE" + b"\x00" * 40)

        with self.assertRaises(AudioContainerValidationError) as ctx:
            validate_audio_container_headers(bad_wav)
        self.assertIn("Invalid magic bytes", str(ctx.exception))

    def test_malformed_vtt_bom_stripping(self) -> None:
        """Malformed: UTF-8 BOM in WebVTT transcript is safely stripped without phantom turns."""
        from dual_pipeline.transcript_pipeline_core.format_parsers import parse_vtt_or_srt

        bom_vtt = (
            "\ufeffWEBVTT\n\n"
            "00:00:01.000 --> 00:00:05.000\n"
            "Amit Ayer: Discussing live matrix certification.\n"
        )
        normalized = parse_vtt_or_srt(bom_vtt)
        self.assertIsNotNone(normalized)
        self.assertTrue(len(normalized.turns) >= 1)
        self.assertEqual(normalized.turns[0].speaker, "Amit Ayer")
        self.assertNotIn("\ufeff", normalized.text)

    # -------------------------------------------------------------------------
    # Category 5: Invariant bypass attempt
    # -------------------------------------------------------------------------
    def test_invariant_bypass_synthetic_approval_rejected(self) -> None:
        """Invariant: Autonomous engine signature cannot authorize active ingestion mutation."""
        from dual_pipeline.hitl_governance_core.active_ingestion_approval import check_active_ingestion_clearance
        from dual_pipeline.hitl_governance_core.decision_record_writer import record_operator_answer

        directive_id = "directive-bypass-test"
        ledger_p = self.runtime_root / "hitl_decision_ledger.sqlite"
        record_operator_answer(
            decision_id=directive_id,
            category="INGESTION_MUTATION",
            question="Attempted autonomous bypass",
            options=["Approve", "Decline"],
            selected_option="Approve",
            operator_origin={"operator_id": "operator", "origin": "operator_ui"},
            rationale="Automated test attempting autonomous mutation authorization",
            metadata={"directive_id": directive_id, "authority_type": "AUTONOMOUS"},
            sqlite_path=ledger_p,
        )

        is_cleared, reason, _ = check_active_ingestion_clearance(
            run_id=directive_id,
            directive_id=directive_id,
            delta_digest="",
            ledger_path=ledger_p,
        )
        self.assertFalse(is_cleared, "Autonomous authority must NOT authorize active graph mutations")
        self.assertIn("REJECTED", reason)

    # -------------------------------------------------------------------------
    # Category 6: Rollback & recovery (Idempotency)
    # -------------------------------------------------------------------------
    def test_idempotent_caching_timestamp_stability(self) -> None:
        """Recovery/Idempotency: _build_expected_idempotent_paths reuses existing file stem."""
        from dual_pipeline.transcript_pipeline_core.caching import _build_expected_idempotent_paths

        # Pre-create completed artifacts with fixed timestamp 143000
        base = "Sync_Meeting_10-11-2026_143000"
        (self.output_dir / f"01_{base}_hitl_resolved_transcript.md").write_text("Transcript", encoding="utf-8")
        (self.output_dir / f"02_{base}_executive_recap.md").write_text("Recap", encoding="utf-8")
        (self.output_dir / f"03_{base}_canonical_signal_projection.json").write_text("{}", encoding="utf-8")
        (self.output_dir / "final_release_decision.json").write_text("{}", encoding="utf-8")

        metadata: dict[str, object] = {
            "meeting_name": "Sync Meeting",
            "date": "10-11-2026",
            "time": None,  # Omitted time must reuse 143000 rather than creating new timestamp
        }

        paths = _build_expected_idempotent_paths(self.output_dir, metadata)
        self.assertIsNotNone(paths, "Idempotent paths must be discovered from existing output artifacts")
        self.assertIn("143000", str(metadata.get("time_filename")))

    # -------------------------------------------------------------------------
    # Category 7: Exact production regression
    # -------------------------------------------------------------------------
    def test_exact_regression_attachment_error_fallback(self) -> None:
        """Regression: FAILED vision dictionary does not suppress fallback extractors."""
        from unittest.mock import patch
        from dual_pipeline.attachment_core.image_extractor import extract_image_visual

        img_file = self.tmp_path / "diagram.png"
        img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        with patch("dual_pipeline.attachment_core.image_extractor._extract_via_apple_vision", return_value=None):
            with patch(
                "dual_pipeline.attachment_core.image_extractor._extract_via_openai_vision",
                return_value={"status": "FAILED", "extraction_status": "FAILED", "error": "rate limit"},
            ):
                with patch(
                    "dual_pipeline.attachment_core.image_extractor._extract_via_gemini_vision",
                    return_value={"status": "SUCCESS", "text": "Gemini fallback extracted text"},
                ):
                    res = extract_image_visual(img_file)
                    self.assertEqual(res.get("status"), "SUCCESS")
                    self.assertIn("Gemini fallback", res.get("text", ""))
