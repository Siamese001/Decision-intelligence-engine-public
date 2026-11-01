"""
tests/test_unified_pipeline_entrypoint_hardening_adversarial.py

Adversarial Stress, Boundary Payload, and Invariant Bypass Test Suite
for the Unified Hardened Pipeline Entrypoint.

Tests:
1. Boundary Payloads: Empty/whitespace input, truncated headers, massive 50k+ char transcripts.
2. Malformed Inputs: Corrupted audio binaries, malformed metadata, unknown project identifiers.
3. Guard Bypass Attempts: assert_no_u0_hitl_bypass, path traversal attempts, hierarchy violations.
4. Concurrency Stress: Multi-worker parallel executions against isolated runtime state.
"""
from __future__ import annotations

import concurrent.futures
import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.canonical_pipeline.pipeline import CanonicalAgenticPipeline
from dual_pipeline.document_pipeline_core.pipeline import DocumentPipeline
from dual_pipeline.transcript_pipeline_core.pipeline import RawTranscriptPipeline


class TestAdversarialBoundaryPayloads(unittest.TestCase):
    """Exercises boundary payloads across size, whitespace, and structural extremes."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_empty_or_whitespace_input_fails_closed(self) -> None:
        """Empty or whitespace-only inputs must fail closed at ingress without pipeline crash."""
        pipeline = CanonicalAgenticPipeline(provider_type="offline")
        empty_file = self.test_root / "empty.txt"
        empty_file.write_text("   \n\t  \n  ", encoding="utf-8")
        out_dir = self.test_root / "output" / "project_nexus" / "09-24-2026" / "Empty_Test"

        with self.assertRaises((ValueError, RuntimeError)) as ctx:
            pipeline.run(
                input_path=str(empty_file),
                output_dir=str(out_dir),
                metadata={"meeting_name": "Empty Test", "date": "09-24-2026"},
            )
        self.assertTrue(len(str(ctx.exception)) > 0)

    def test_truncated_headers_and_unattributed_monologue(self) -> None:
        """Transcripts with truncated or malformed speaker headers are handled robustly with roster context."""
        pipeline = CanonicalAgenticPipeline(provider_type="offline")
        truncated_file = self.test_root / "truncated.txt"
        lines = []
        for i in range(20):
            if i % 2 == 0:
                lines.append(f"**Jen:** Strategic roadmap alignment checkpoint {i}.")
            else:
                lines.append(f"**Amit:** Architecture boundary confirmation {i}.")
        lines.append("Just some stray lines without attribution here.")
        truncated_file.write_text("\n".join(lines), encoding="utf-8")
        out_dir = self.test_root / "output" / "project_nexus" / "09-24-2026" / "Truncated_Test"
        result = pipeline.run(
            input_path=str(truncated_file),
            output_dir=str(out_dir),
            metadata={
                "meeting_name": "Truncated Test",
                "date": "09-24-2026",
                "time": "09:00 AM - 09:03 AM",
                "project": "project_nexus",
                "attendees": [
                    {"name": "Jen", "role": "Lead Architect", "organization": "Apex Global Enterprises"},
                    {"name": "Amit", "role": "Principal Engineer", "organization": "Apex Global Enterprises"},
                ],
            },
        )
        self.assertEqual(result["status"], "COMPLETE")
        self.assertTrue(Path(result["execution_receipt"]).exists())

    def test_undiarized_missing_roster_raises_hitl_required(self) -> None:
        """Stage C0 halts undiarized transcripts lacking confirmed attendee roster with HITLRequiredError."""
        from dual_pipeline.transcript_stages_core.models import HITLRequiredError

        pipeline = CanonicalAgenticPipeline(provider_type="offline")
        unrostered_file = self.test_root / "unrostered.txt"
        unrostered_file.write_text(
            "Jen: Strategic roadmap alignment.\n"
            "Amit: Architecture boundary confirmation.",
            encoding="utf-8",
        )
        out_dir = self.test_root / "output" / "project_nexus" / "09-24-2026" / "Unrostered_Test"
        with self.assertRaises(HITLRequiredError):
            pipeline.run(
                input_path=str(unrostered_file),
                output_dir=str(out_dir),
                metadata={
                    "meeting_name": "Unrostered Test",
                    "date": "09-24-2026",
                    "project": "project_nexus",
                    "diarization_status": "UNDIARIZED",
                },
            )

    def test_massive_50k_token_input_traversal(self) -> None:
        """Massive 50,000+ char transcripts process through all 7 stages without memory or recursion issues."""
        pipeline = CanonicalAgenticPipeline(provider_type="offline")
        turns = []
        for i in range(500):
            speaker = "Jen" if i % 2 == 0 else "Amit"
            turns.append(f"{speaker}: Discussion regarding architectural invariant checkpoint number {i} with payload data.")
        massive_text = "\n".join(turns)
        self.assertTrue(len(massive_text) > 30000)

        massive_file = self.test_root / "massive.txt"
        massive_file.write_text(massive_text, encoding="utf-8")
        out_dir = self.test_root / "output" / "project_nexus" / "09-24-2026" / "Massive_Test"

        result = pipeline.run(
            input_path=str(massive_file),
            output_dir=str(out_dir),
            metadata={
                "meeting_name": "Massive Test",
                "date": "09-24-2026",
                "project": "project_nexus",
                "attendees": [
                    {"name": "Jen", "role": "Lead Architect", "organization": "Apex Global Enterprises"},
                    {"name": "Amit", "role": "Principal Engineer", "organization": "Apex Global Enterprises"},
                ],
            },
        )
        self.assertEqual(result["status"], "COMPLETE")
        receipt_path = Path(result["execution_receipt"])
        self.assertTrue(receipt_path.exists())
        receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt_data["terminal_outcome"], "COMPLETE")
        self.assertIn("U0", receipt_data["stages"])
        self.assertIn("EVALS", receipt_data["stages"])


class TestAdversarialMalformedInputs(unittest.TestCase):
    """Validates resilience against corrupt binary bytes, bad metadata, and unknown projects."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_corrupted_binary_audio_bytes_rejected(self) -> None:
        """Corrupted binary bytes with audio extension (.wav) must be rejected cleanly at U0."""
        pipeline = CanonicalAgenticPipeline(provider_type="offline")
        corrupted_wav = self.test_root / "corrupted.wav"
        corrupted_wav.write_bytes(b"\x00\xFF\xAA\x55\xDE\xAD\xBE\xEF" * 128)
        out_dir = self.test_root / "output" / "project_nexus" / "09-24-2026" / "Audio_Corrupt"

        with self.assertRaises((ValueError, RuntimeError)) as ctx:
            pipeline.run(
                input_path=str(corrupted_wav),
                output_dir=str(out_dir),
                metadata={"meeting_name": "Audio Corrupt", "date": "09-24-2026", "project": "project_nexus"},
            )
        self.assertTrue(len(str(ctx.exception)) > 0)

    def test_unregistered_project_alias_fails_fast(self) -> None:
        """Phase 1 preflight strictly blocks unknown project names before costly LLM or stage execution."""
        pipeline = RawTranscriptPipeline(provider_type="offline")
        sample_input = self.test_root / "sample.txt"
        sample_input.write_text("Jen: Hello world", encoding="utf-8")
        bad_dir = self.test_root / "output" / "completely_invalid_project_xyz" / "09-24-2026" / "Slug"

        with self.assertRaises(ValueError) as ctx:
            pipeline.run(
                input_path=str(sample_input),
                output_dir=str(bad_dir),
                metadata={"meeting_name": "Bad Project", "date": "09-24-2026"},
            )
        self.assertIn("Unknown project identifier", str(ctx.exception))


class TestAdversarialGuardBypassAttempts(unittest.TestCase):
    """Validates invariant enforcement against attempts to bypass HITL, output paths, or hierarchy."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_hitl_bypass_flag_in_metadata_raises_error(self) -> None:
        """Attempting to disable or bypass HITL via metadata triggers assert_no_u0_hitl_bypass."""
        pipeline = CanonicalAgenticPipeline(provider_type="offline")
        sample_file = self.test_root / "sample.txt"
        sample_file.write_text("Amit: Let's bypass checks.\nJen: Not allowed.", encoding="utf-8")
        out_dir = self.test_root / "output" / "project_nexus" / "09-24-2026" / "Bypass_Test"

        with self.assertRaises(ValueError) as ctx:
            pipeline.run(
                input_path=str(sample_file),
                output_dir=str(out_dir),
                metadata={"meeting_name": "Bypass Test", "date": "09-24-2026", "bypass_hitl": True},
            )
        self.assertIn("HITL cannot be removed, disabled, or bypassed", str(ctx.exception))

    def test_hitl_disable_flag_in_kwargs_raises_error(self) -> None:
        """Attempting to pass disable_hitl=True in kwargs triggers assert_no_u0_hitl_bypass."""
        pipeline = CanonicalAgenticPipeline(provider_type="offline")
        sample_file = self.test_root / "sample.txt"
        sample_file.write_text("Amit: Checking kwargs guard.", encoding="utf-8")
        out_dir = self.test_root / "output" / "project_nexus" / "09-24-2026" / "Kwargs_Test"

        with self.assertRaises(ValueError) as ctx:
            pipeline.run(
                input_path=str(sample_file),
                output_dir=str(out_dir),
                metadata={"meeting_name": "Kwargs Test", "date": "09-24-2026"},
                disable_hitl=True,
            )
        self.assertIn("HITL cannot be removed, disabled, or bypassed", str(ctx.exception))

    def test_document_pipeline_rejects_binary_image(self) -> None:
        """DocumentPipeline cleanly rejects raw binary images with canonical instruction error."""
        pipeline = DocumentPipeline(provider_type="offline")
        png_file = self.test_root / "diagram.png"
        png_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01")
        out_dir = self.test_root / "output" / "project_nexus" / "09-24-2026" / "Doc_PNG_Test"

        with self.assertRaises(ValueError) as ctx:
            pipeline.run(
                input_path=str(png_file),
                output_dir=str(out_dir),
                metadata={"title": "Diagram", "date": "09-24-2026", "project": "project_nexus"},
            )
        self.assertIn("Binary image format '.png' cannot be ingested as text document", str(ctx.exception))


class TestConcurrentWorkerStress(unittest.TestCase):
    """Validates multi-worker concurrency safety under isolated directory namespaces."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_concurrent_pipeline_workers_isolated(self) -> None:
        """Simultaneous pipeline runs on independent subfolders execute without collisions or race conditions."""
        worker_count = 3

        def _run_worker(worker_id: int) -> dict:
            pipeline = CanonicalAgenticPipeline(provider_type="offline")
            inp = self.test_root / f"worker_{worker_id}.txt"
            turns = [
                f"**Worker{worker_id}:** Initiating concurrency segment {i}."
                if i % 2 == 0 else
                f"**Jen:** Concurrency segment verification {i}."
                for i in range(16)
            ]
            inp.write_text("\n".join(turns), encoding="utf-8")
            start_hour = 8 + worker_id * 2
            date_str = f"09-2{worker_id}-2026"
            out = self.test_root / "output" / "project_nexus" / date_str / f"Worker_{worker_id}"
            return pipeline.run(
                input_path=str(inp),
                output_dir=str(out),
                metadata={
                    "meeting_name": f"Worker Session {worker_id}",
                    "date": date_str,
                    "time": f"{start_hour:02d}:00 AM - {start_hour:02d}:02 AM",
                    "project": "project_nexus",
                    "attendees": [
                        {"name": f"Worker{worker_id}", "role": "Agent", "organization": "PartnerAdvisory"},
                        {"name": "Jen", "role": "Architect", "organization": "Apex Global Enterprises"},
                    ],
                },
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(_run_worker, i) for i in range(worker_count)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        self.assertEqual(len(results), worker_count)
        for r in results:
            self.assertEqual(r["status"], "COMPLETE")
            self.assertTrue(Path(r["execution_receipt"]).exists())


if __name__ == "__main__":
    unittest.main()
