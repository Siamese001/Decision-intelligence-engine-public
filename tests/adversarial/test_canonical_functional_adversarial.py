"""
tests/adversarial/test_canonical_functional_adversarial.py

Wave 5 Adversarial & Edge-Case Qualification Suite:
Covers three mandatory edge-case categories:
1. Boundary payload: whitespace-only transcripts, missing required time metadata.
2. Invariant bypass: empty reviewer token rejection, mock synthesis mutation block, tampered decision seal.
3. Rollback & recovery: corrupted attempt-1.json recovery, safe completion without dirty state leakage.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.canonical_pipeline import CanonicalAgenticPipeline, CanonicalModality
from dual_pipeline.canonical_pipeline.publish_helper import (
    _assert_authoritative_mutation_preconditions,
    check_mutation_reviewer_and_preflight,
)
from dual_pipeline.release_policy import verify_decision_seal


class TestCanonicalFunctionalAdversarial(unittest.TestCase):
    """Adversarial stress and edge-case qualification suite for Wave 5."""

    def test_boundary_empty_transcript_fails_closed(self) -> None:
        """Category 1 (Boundary): Empty transcript content must fail closed gracefully."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            empty_file = tmp_path / "empty_transcript.txt"
            empty_file.write_text("   \n\n\t  \n", encoding="utf-8")
            out_dir = tmp_path / "output" / "project_lens" / "10-07-2026" / "Empty_Run"
            out_dir.mkdir(parents=True, exist_ok=True)

            meta = {
                "project": "project_lens",
                "meeting_name": "Empty Transcript Boundary",
                "date": "10/07/2026",
                "time": "09:00 AM - 10:00 AM ET",
                "attendees": "Amit Ayer",
                "classification": "internal",
            }
            pipeline = CanonicalAgenticPipeline(provider_type="offline")
            with self.assertRaises(ValueError):
                pipeline.run(input_path=empty_file, metadata=meta, output_dir=str(out_dir), package_only=True)

    def test_boundary_missing_time_metadata_fails_closed(self) -> None:
        """Category 1 (Boundary): Missing required time metadata must fail closed at U0."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            source_file = tmp_path / "source.txt"
            source_file.write_text("**Amit Ayer:** [09:00:00]: Content\n", encoding="utf-8")
            out_dir = tmp_path / "output" / "project_lens" / "10-07-2026" / "Missing_Time"
            out_dir.mkdir(parents=True, exist_ok=True)

            meta = {
                "project": "project_lens",
                "meeting_name": "Missing Time Metadata",
                "date": "10/07/2026",
                # "time" explicitly omitted
                "attendees": "Amit Ayer",
                "classification": "internal",
            }
            pipeline = CanonicalAgenticPipeline(provider_type="offline")
            with self.assertRaises(ValueError):
                pipeline.run(input_path=source_file, metadata=meta, output_dir=str(out_dir), package_only=True)

    def test_invariant_bypass_empty_reviewer_rejected(self) -> None:
        """Category 2 (Invariant Bypass): Empty or whitespace reviewer tokens fail closed."""
        meta = {"project": "project_lens", "classification": "internal"}
        for invalid_reviewer in ["", "   ", None]:
            with self.subTest(reviewer=invalid_reviewer):
                with self.assertRaises(ValueError):
                    check_mutation_reviewer_and_preflight(
                        modality=CanonicalModality.DOCUMENT,
                        should_mutate=True,
                        meta=meta,
                        reviewer=invalid_reviewer,
                    )

    def test_invariant_bypass_mock_mutation_blocked(self) -> None:
        """Category 2 (Invariant Bypass): Mock or offline synthesis is strictly barred from authoritative mutation."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            out_p = tmp_path / "out"
            out_p.mkdir(parents=True, exist_ok=True)

            meta = {"is_mock": True, "provider": "offline"}
            with self.assertRaises(RuntimeError):
                _assert_authoritative_mutation_preconditions(out_p, meta)

    def test_invariant_bypass_tampered_decision_seal_rejected(self) -> None:
        """Category 2 (Invariant Bypass): Altered artifact content or forged seal is rejected."""
        decision_data = {
            "mode": "document",
            "artifact_manifest": {
                "output_1_sha256": "hash1",
                "output_2_sha256": "hash2",
                "output_3_sha256": "hash3",
                "decision_seal_sha256": "forged_seal_value",
            },
        }
        valid, errors = verify_decision_seal(decision_data, f1_text="text1", f2_text="text2", f3_text="text3")
        self.assertFalse(valid)
        self.assertTrue(any("Decision seal mismatch" in err or "content drifted" in err for err in errors))

    def test_recovery_corrupted_attempt_json_recovers_cleanly(self) -> None:
        """Category 3 (Rollback & Recovery): Malformed attempt JSON does not crash pipeline."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            source_file = tmp_path / "recovery_test.txt"
            source_file.write_text(
                "**harper Hoffman:** [10:00:00]: Recovery test start.\n\n"
                "**Amit Ayer:** [10:01:00]: Resilient ingestion handles corrupt metadata.\n\n"
                "**harper Hoffman:** [10:02:00]: Recovery test end.\n",
                encoding="utf-8",
            )
            out_dir = tmp_path / "output" / "project_lens" / "10-07-2026" / "Corrupt_Recovery"
            attempts_dir = out_dir / "attempts"
            attempts_dir.mkdir(parents=True, exist_ok=True)

            # Write corrupt attempt JSON
            (attempts_dir / "attempt-1.json").write_text("{malformed json truncated...", encoding="utf-8")

            meta = {
                "project": "project_lens",
                "meeting_name": "Corrupted Checkpoint Recovery",
                "date": "10/07/2026",
                "time": "10:00 AM - 10:30 AM ET",
                "attendees": "harper Hoffman, Amit Ayer",
                "classification": "internal",
                "force_rerun": False,
            }
            pipeline = CanonicalAgenticPipeline(provider_type="offline")
            res = pipeline.run(input_path=source_file, metadata=meta, output_dir=str(out_dir), package_only=True)

            self.assertEqual(res["status"], "PACKAGE_SEALED")
            self.assertTrue(Path(res["01_transcript"]).exists())
            self.assertTrue(Path(res["02_recap"]).exists())


if __name__ == "__main__":
    unittest.main()
