"""
tests/end_to_end/test_pre_gate_screener_e2e.py

End-to-end integration tests for Wave 1: Deterministic Pre-Gate Screener (Agent 3).
Verifies that CanonicalAgenticPipeline enforces pre-gate screening prior to L2 synthesis,
immediately aborting doomed runs with zero outbound LLM provider calls.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.canonical_pipeline import CanonicalAgenticPipeline
from dual_pipeline.token_optimization.pre_gate_screener import (
    DEFAULT_AVERTED_TOKENS_PER_RUN,
    PreGateValidationError,
)


class PreGateScreenerE2ETests(unittest.TestCase):
    def test_01_doomed_pipeline_run_aborts_before_l2(self) -> None:
        """Verify doomed run with invalid project halts prior to L2 synthesis."""
        with tempfile.TemporaryDirectory(prefix="pre_gate_e2e_") as tmp_dir:
            tmp_path = Path(tmp_dir)
            source_file = tmp_path / "doomed_session.txt"
            source_file.write_text(
                "**Speaker A:** [09:00:00]: This session has missing project metadata.\n\n"
                "**Speaker B:** [09:01:00]: It should be halted by the pre-gate screener.\n",
                encoding="utf-8",
            )
            out_dir = tmp_path / "output"
            out_dir.mkdir(parents=True, exist_ok=True)

            # Doomed metadata missing required 'project' and invalid attendee format
            meta = {
                "project": "",
                "meeting_name": "Doomed Session",
                "date": "10-07-2026",
                "time": "09:00 AM - 09:30 AM ET",
                "attendees": "A",
                "enforce_pre_gate": True,
                "strict_pre_gate": True,
            }

            pipeline = CanonicalAgenticPipeline(provider_type="offline")

            with self.assertRaises(PreGateValidationError) as ctx:
                pipeline.run(
                    input_path=source_file,
                    metadata=meta,
                    output_dir=str(out_dir),
                    package_only=True,
                )

            self.assertIn("Pre-gate validation failed", str(ctx.exception))
            self.assertIn("pre_gate_screener", meta)
            self.assertFalse(meta["pre_gate_screener"]["is_valid"])
            self.assertEqual(
                meta["pre_gate_screener"]["tokens_saved_estimate"],
                DEFAULT_AVERTED_TOKENS_PER_RUN,
            )

    def test_02_valid_pipeline_run_passes_pre_gate_cleanly(self) -> None:
        """Verify valid meeting passes pre-gate screening cleanly and completes."""
        with tempfile.TemporaryDirectory(prefix="pre_gate_e2e_") as tmp_dir:
            tmp_path = Path(tmp_dir)
            source_file = tmp_path / "valid_session.txt"
            source_file.write_text(
                "**harper Hoffman:** [09:00:00]: Valid session starting for LENS project.\n\n"
                "**Amit Ayer:** [09:01:00]: Pre-gate screener will validate all invariants.\n\n"
                "**harper Hoffman:** [09:02:00]: Completing session.\n",
                encoding="utf-8",
            )
            out_dir = tmp_path / "output"
            out_dir.mkdir(parents=True, exist_ok=True)

            meta = {
                "project": "project_lens",
                "meeting_name": "Valid LENS Session",
                "date": "10-07-2026",
                "time": "09:00 AM - 09:30 AM ET",
                "attendees": "harper Hoffman, Amit Ayer",
                "enforce_pre_gate": True,
                "strict_pre_gate": True,
            }

            pipeline = CanonicalAgenticPipeline(provider_type="offline")
            result = pipeline.run(
                input_path=source_file,
                metadata=meta,
                output_dir=str(out_dir),
                package_only=True,
            )

            self.assertEqual(result["status"], "PACKAGE_SEALED")
            self.assertIn("pre_gate_screener", meta)
            self.assertTrue(meta["pre_gate_screener"]["is_valid"])
            self.assertEqual(len(meta["pre_gate_screener"]["errors"]), 0)


if __name__ == "__main__":
    unittest.main()
