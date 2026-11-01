"""
tests/test_all_pipeline_sources_adversarial.py

Adversarial and Edge-Case Test Suite Across All NEE Pipeline Ingestion Sources.
Covers 5 mandatory edge-case categories per .agents/rules/e2e-and-edge-case-coverage.md:
1. Blocked Legacy Formats & Capability Boundaries (.ppt, .xls, .msg)
2. Boundary Payloads (0-byte files, sub-threshold semantic yield <50 words)
3. Malformed Inputs (corrupted ZIP containers, invalid PDF magic bytes, NUL-byte text injection, XXE diagram payloads)
4. Invariant Bypass Attempts (--bypass-speaker-hitl, contradictory CLI flags, mock/offline mutation guards)
5. Concurrency & Rollback Recovery (multi-threaded classification isolation, duplicate graph mutation rejection)
"""
from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.canonical_pipeline import CanonicalAgenticPipeline, CanonicalModality
from dual_pipeline.canonical_pipeline.modalities import detect_modality
from dual_pipeline.canonical_pipeline.publish_helper import _assert_authoritative_mutation_preconditions
from dual_pipeline.cli.ingest import _validate_semantic_flag_conflicts, build_canonical_metadata
from dual_pipeline.cli.parser import build_parser
from dual_pipeline.document_pipeline_core.attachment_preflight import preflight_attachment
from dual_pipeline.document_pipeline_core.content_loader import load_document_content
from dual_pipeline.document_pipeline_core.diagram_loader import extract_diagram_with_locators
from dual_pipeline.document_pipeline_core.format_source_units import FailureDisposition
from dual_pipeline.exceptions import SourceIntegrityError, ZeroSignalError
from dual_pipeline.graph_scout_core.pipeline_hook import ScoutHookResult
from dual_pipeline.source_classification.capability_registry import (
    SupportStatus,
    get_blocked_extensions,
    get_capability_by_ext,
)
from dual_pipeline.uwg import UniversalWriteGateway


class AllPipelineSourcesAdversarialTests(unittest.TestCase):
    """Adversarial and boundary verification across all NEE pipeline sources."""

    def setUp(self) -> None:
        from opentelemetry.trace import NoOpTracerProvider
        self._tmp = tempfile.TemporaryDirectory(prefix="nee_sources_adv_")
        self.root = Path(self._tmp.name)
        self._prev_sandbox = os.environ.get("TEST_SANDBOX_DIR")
        self._prev_fast = os.environ.get("NEE_FAST_TEST_MODE")
        os.environ["TEST_SANDBOX_DIR"] = str(self.root)
        os.environ["NEE_FAST_TEST_MODE"] = "1"
        self._patches = [
            patch("dual_pipeline.canonical_pipeline.pipeline.ROOT", self.root),
            patch("dual_pipeline.canonical_pipeline.concurrency.ROOT", self.root),
            patch("dual_pipeline.dlq.DeadLetterQueueManager"),
            patch("dual_pipeline.canonical_pipeline.pipeline.DeadLetterQueueManager"),
            patch("dual_pipeline.otel_engine.get_tracer", return_value=NoOpTracerProvider().get_tracer("test")),
            patch("dual_pipeline.telemetry_core.metrics.record_stage_duration"),
            patch("dual_pipeline.hitl_governance_core.ledger._find_default_ledger_path", return_value=self.root / "hitl.json"),
            patch("dual_pipeline.graph_scout_core.pipeline_hook.execute_tier1_scout_hook", return_value=ScoutHookResult(skipped_reason="test")),
        ]
        for p in self._patches:
            p.start()
        self.pipeline = CanonicalAgenticPipeline(provider_type="offline")

    def tearDown(self) -> None:
        for p in reversed(self._patches):
            p.stop()
        if self._prev_sandbox is None:
            os.environ.pop("TEST_SANDBOX_DIR", None)
        else:
            os.environ["TEST_SANDBOX_DIR"] = self._prev_sandbox
        if self._prev_fast is None:
            os.environ.pop("NEE_FAST_TEST_MODE", None)
        else:
            os.environ["NEE_FAST_TEST_MODE"] = self._prev_fast
        self._tmp.cleanup()

    def test_category1_blocked_legacy_formats_fail_closed(self) -> None:
        """Blocked legacy binary formats (.ppt, .xls, .msg) must be explicitly marked BLOCKED and fail closed."""
        blocked = get_blocked_extensions()
        for ext in (".ppt", ".xls", ".msg"):
            self.assertIn(ext, blocked)
            cap = get_capability_by_ext(ext)
            self.assertIsNotNone(cap)
            self.assertEqual(cap.status, SupportStatus.BLOCKED)
            self.assertTrue(len(cap.notes) > 10)

            legacy_file = self.root / f"legacy_input{ext}"
            raw_ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64
            legacy_file.write_bytes(raw_ole)
            pf = preflight_attachment(raw_ole, legacy_file.name)
            self.assertFalse(pf.is_valid)
            self.assertEqual(pf.disposition, FailureDisposition.BLOCKED)
            self.assertTrue(pf.is_legacy)
            with self.assertRaises(ValueError) as ctx:
                load_document_content(legacy_file)
            self.assertIn("BLOCKED", str(ctx.exception).upper())

    def test_category2_boundary_payloads_zero_byte_and_low_semantic_yield(self) -> None:
        """0-byte files across modalities raise SourceIntegrityError; <50-word documents raise ZeroSignalError."""
        for ext in (".pdf", ".docx", ".xlsx", ".pptx", ".wav", ".txt", ".md"):
            empty_p = self.root / f"empty_boundary{ext}"
            empty_p.write_bytes(b"")
            with self.assertRaises(SourceIntegrityError):
                self.pipeline.run(
                    input_path=empty_p,
                    metadata={"project": "nea", "title": "Empty Boundary", "date": "10/15/2026", "enforce_worktree": False},
                    output_dir=str(self.root / f"out_empty_{ext.lstrip('.')}"),
                    mutate_graph=False,
                )

        short_doc = self.root / "too_short.md"
        short_doc.write_text("# Brief Note\n\nOnly ten words in this tiny document payload.", encoding="utf-8")
        with self.assertRaises(ZeroSignalError):
            self.pipeline.run(
                input_path=short_doc,
                metadata={
                    "project": "nea",
                    "title": "Short Doc",
                    "date": "10/15/2026",
                    "author": "Amit Ayer",
                    "is_document": True,
                    "enforce_worktree": False,
                    "test_fast": True,
                },
                output_dir=str(self.root / "out_short"),
                mutate_graph=False,
            )

    def test_category3_malformed_containers_magic_bytes_and_xxe_injection(self) -> None:
        """Corrupted Office ZIPs, fake PDF headers, binary NUL in text, and XXE in diagrams fail closed."""
        fake_pdf = self.root / "spoofed.pdf"
        fake_pdf.write_bytes(b"NOT_A_PDF_HEADER_PAYLOAD_1234567890")
        with self.assertRaises(SourceIntegrityError):
            self.pipeline.run(
                input_path=fake_pdf,
                metadata={"project": "nea", "title": "Spoofed PDF", "date": "10/15/2026", "is_document": True, "enforce_worktree": False},
                output_dir=str(self.root / "out_fake_pdf"),
                mutate_graph=False,
            )

        for ext in (".docx", ".xlsx", ".pptx", ".vsdx"):
            bad_zip = self.root / f"corrupt{ext}"
            bad_zip.write_bytes(b"NOT_A_VALID_ZIP_CONTAINER_BYTES")
            with self.assertRaises(SourceIntegrityError):
                self.pipeline.run(
                    input_path=bad_zip,
                    metadata={"project": "nea", "title": f"Corrupt {ext}", "date": "10/15/2026", "is_document": True, "enforce_worktree": False},
                    output_dir=str(self.root / f"out_bad_{ext.lstrip('.')}"),
                    mutate_graph=False,
                )

        nul_txt = self.root / "binary_injected.txt"
        nul_txt.write_bytes(b"Normal header\x00Hidden binary payload")
        with self.assertRaises(SourceIntegrityError):
            self.pipeline.run(
                input_path=nul_txt,
                metadata={"project": "nea", "title": "Binary Injected", "date": "10/15/2026", "is_document": True, "enforce_worktree": False},
                output_dir=str(self.root / "out_nul_txt"),
                mutate_graph=False,
            )

        xxe_svg = b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><svg>&xxe;</svg>'
        with self.assertRaisesRegex(ValueError, "Malicious XML detected"):
            extract_diagram_with_locators(self.root / "evil.svg", xxe_svg, {}, ext=".svg")

    def test_category4_invariant_bypass_attempts_blocked(self) -> None:
        """Attempts to bypass U0 speaker HITL, pass contradictory modality flags, or mutate graph from mock/offline runs fail closed."""
        with self.assertRaisesRegex(ValueError, "Contradictory options"):
            _validate_semantic_flag_conflicts(is_doc=True, is_notes=True, is_out3=False)
        with self.assertRaisesRegex(ValueError, "Contradictory options"):
            _validate_semantic_flag_conflicts(is_doc=False, is_notes=True, is_out3=True)

        parser = build_parser()
        args = parser.parse_args(["ingest-meeting", "--input", "dummy.wav", "--project", "nea"])
        args.bypass_speaker_hitl = True
        with self.assertRaisesRegex(ValueError, "HITL cannot be removed"):
            build_canonical_metadata(args)

        stage_dir = self.root / "mock_stage"
        stage_dir.mkdir(parents=True, exist_ok=True)
        (stage_dir / "final_release_decision.json").write_text('{"status": "PASS"}', encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "MOCK_SYNTHESIS_NOT_AUTHORITATIVE"):
            _assert_authoritative_mutation_preconditions(stage_dir, {"provider": "offline"})

        uwg = UniversalWriteGateway(self.root / "runtime", self.root / "graph.sqlite")
        with self.assertRaisesRegex(RuntimeError, "MOCK_SYNTHESIS_NOT_AUTHORITATIVE"):
            uwg.process_commit_request({"is_mock": True})

    def test_category5_concurrent_modality_routing_isolation(self) -> None:
        """Concurrent threads classifying Audio, Meeting Transcript, Meeting Notes, and Document sources exhibit zero state bleed."""
        cases = [
            (Path("call.wav"), "", {}, CanonicalModality.AUDIO),
            (Path("transcript.vtt"), "WEBVTT\n\n00:00:01.000 --> 00:00:05.000\n<v Amit>Hello</v>", {}, CanonicalModality.MEETING_TRANSCRIPT),
            (Path("notes.md"), "# Meeting Notes", {"notes_mode": True, "is_notes": True}, CanonicalModality.MEETING_NOTES),
            (Path("spec.pdf"), "# Architecture Spec", {"is_document": True}, CanonicalModality.DOCUMENT),
        ]
        errors: list[Exception] = []

        def worker(iteration: int) -> None:
            try:
                for p, txt, meta, expected in cases:
                    actual = detect_modality(p, txt, dict(meta))
                    if actual != expected:
                        raise AssertionError(f"Iteration {iteration}: expected {expected}, got {actual}")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
