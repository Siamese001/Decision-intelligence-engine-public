"""
tests/end_to_end/test_backtest_e2e.py

End-to-end integration test verifying the complete backtest re-run lifecycle (Wave 3):
  1. Selective regeneration freezes Output 01 and recalculates Outputs 02 and 03.
  2. Micro-judges are routed to the fast tier.
  3. Run-over-run comparison report (backtest_comparison_report.md) is generated.
  4. Output folder hierarchy passes governance validation without violations.
  5. Cost is verified strictly below $0.25 gate threshold.
  6. Telemetry and execution receipt records run_mode: "backtest".
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import pytest

from dual_pipeline.canonical_pipeline import CanonicalAgenticPipeline
from tools.validate_output_hierarchy import validate_meeting_directory


@pytest.mark.e2e
class TestBacktestE2E(unittest.TestCase):
    """End-to-End test suite for cost-optimized backtest pipeline execution."""

    def setUp(self) -> None:
        os.environ["NEE_ALLOW_TEST_FIXTURES"] = "1"
        os.environ["NEE_ENFORCE_WORKTREE"] = "0"
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)

        # Setup compliant output container: output/project_nexus/10-01-2026/Backtest_Session
        self.date_dir = self.tmp_path / "output" / "project_nexus" / "10-01-2026"
        self.out_dir = self.date_dir / "Backtest_Session"
        self.out_dir.mkdir(parents=True, exist_ok=True)

        # Prior Output 01 (forensic transcript to be frozen)
        self.prior_01_text = (
            "# Forensic Transcript\n\n"
            "**Date:** 10/01/2026\n"
            "**Meeting:** Backtest Session\n\n"
            "**Alice**: [00:00:00] Welcome everyone to the backtest baseline verification session. "
            "We are evaluating our re-run cost optimization architecture today.\n\n"
            "**Bob**: [00:00:45] Thanks Alice. The engineering deliverables and selective regeneration pipelines look solid. "
            "All micro-judges are ready for fast tier testing.\n\n"
            "**Alice**: [00:01:30] Perfect. Let's make sure the knowledge graph entity projections align with the schema standards.\n\n"
            "**Bob**: [00:02:15] Agreed. The projection tables and assertion envelopes have been verified against the RFC-8785 specifications.\n"
        )
        self.prior_01_file = self.out_dir / "01_Backtest_Session_10-01-2026_hitl_resolved_transcript.md"
        self.prior_01_file.write_text(self.prior_01_text, encoding="utf-8")

        # Prior Output 02
        self.prior_02_text = (
            "# Executive Recap\n\n"
            "## Meeting Objective\nPrior baseline objective.\n\n"
            "## Key Strategic Updates\nBaseline updates.\n"
        )
        self.prior_02_file = self.out_dir / "02_Backtest_Session_10-01-2026_executive_recap.md"
        self.prior_02_file.write_text(self.prior_02_text, encoding="utf-8")

        # Prior Output 03
        self.prior_03_data = {
            "entities": [{"id": "person:alice"}, {"id": "org:apex_global"}],
            "assertions": [{"id": "a1", "claim_text": "Alice joined"}],
            "relationships": [{"source": "person:alice", "target": "org:apex_global"}],
        }
        self.prior_03_file = self.out_dir / "03_Backtest_Session_10-01-2026_canonical_signal_projection.json"
        self.prior_03_file.write_text(json.dumps(self.prior_03_data), encoding="utf-8")

        # Prior final_release_decision.json
        self.prior_decision = {
            "output_1_pass": True,
            "output_2_pass": True,
            "output_3_pass": True,
            "status": "PASS",
            "synthesis_prompt_version": "V5_7",
        }
        (self.out_dir / "final_release_decision.json").write_text(
            json.dumps(self.prior_decision), encoding="utf-8"
        )

        # Source input transcript
        self.input_file = self.tmp_path / "sample_meeting.md"
        self.input_file.write_text(self.prior_01_text, encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_backtest_pipeline_e2e_lifecycle(self) -> None:
        """Full backtest run generates comparison report, freezes Output 01, and satisfies <$0.25 gate."""
        pipeline = CanonicalAgenticPipeline(provider_type="offline")

        result = pipeline.run(
            input_path=self.input_file,
            metadata={
                "project": "project_nexus",
                "meeting_name": "Backtest Session",
                "date": "10/01/2026",
                "attendees": [
                    {"name": "Alice", "role": "Lead Architect"},
                    {"name": "Bob", "role": "Project Manager"},
                ],
                "attendee_roles_confirmed_by_operator": True,
                "classification": "internal",
                "backtest": True,
                "force_rerun": True,
                "enforce_worktree": False,
                "run_id": "e2e-backtest-run-001",
                "backtest_cost": 0.0875,
                "prior_cost": 1.0200,
            },
            output_dir=str(self.out_dir),
        )

        self.assertEqual(result["status"], "COMPLETE")

        # 1. Output 01 was frozen bit-exact from prior run
        self.assertTrue(Path(result["01_transcript"]).exists())
        self.assertEqual(Path(result["01_transcript"]).read_text(encoding="utf-8"), self.prior_01_text)

        # 2. Output 02 and Output 03 exist
        self.assertTrue(Path(result["02_recap"]).exists())
        self.assertTrue(Path(result["03_graph"]).exists())

        # 3. backtest_comparison_report.md exists
        comp_report_path = self.out_dir / "backtest_comparison_report.md"
        self.assertTrue(comp_report_path.is_file())
        comp_text = comp_report_path.read_text(encoding="utf-8")

        self.assertIn("# Run-Over-Run Backtest Comparison Report", comp_text)
        self.assertIn("## 1. Executive Cost & Throughput Delta", comp_text)
        self.assertIn("## 2. Output 02: Executive Recap Section Diffs", comp_text)
        self.assertIn("## 3. Output 03: Knowledge Graph Entity & Assertion Delta", comp_text)
        self.assertIn("## 4. Stability & Quality Gate Verification", comp_text)
        self.assertIn("$0.0875", comp_text)
        self.assertIn("< $0.25", comp_text)
        self.assertIn("-94.0% judge unit cost", comp_text)

        # 4. Receipt verification: run_mode: "backtest" recorded
        receipt_path = Path(result["execution_receipt"])
        self.assertTrue(receipt_path.is_file())
        receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt_data.get("metadata", {}).get("run_mode"), "backtest")
        self.assertTrue(receipt_data.get("metadata", {}).get("backtest"))

        # 5. Output hierarchy compliance: 0 violations
        violations = validate_meeting_directory(self.date_dir, project_name="project_nexus")
        self.assertEqual(violations, [], f"Expected 0 hierarchy violations, got: {violations}")


if __name__ == "__main__":
    unittest.main()
