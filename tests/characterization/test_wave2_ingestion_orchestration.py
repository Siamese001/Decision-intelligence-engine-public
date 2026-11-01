"""
tests/characterization/test_wave2_ingestion_orchestration.py

Wave 2 regression test suite verifying unified ingestion orchestration:
1. IngestionCoordinator module budget compliance (<=400 lines) and 25% HITL domain focus.
2. IngestionCoordinator direct DAG execution equivalence with broker task orchestration.
3. TranscriptIngestionPipelineRunner.run_agentic() end-to-end execution across full release lifecycle.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.pipeline_runner import TranscriptIngestionPipelineRunner
from pipeline_a_intake.agents import IngestionCoordinator, get_ingestion_coordinator_card
from tests.characterization.test_fixtures import create_sample_manifest


class TestWave2IngestionOrchestration(unittest.IsolatedAsyncioTestCase):
    """Verifies Wave 2 Ingestion Orchestration consolidations and invariants."""

    def test_ingestion_coordinator_line_budget_and_card_invariants(self) -> None:
        """IngestionCoordinator must satisfy <=400 lines budget and 25% HITL margin gating."""
        target_file = Path("pipeline_a_intake/agents/ingestion_coordinator.py")
        line_count = len(target_file.read_text(encoding="utf-8").splitlines())
        self.assertLessEqual(
            line_count,
            400,
            f"IngestionCoordinator exceeds 400-line budget: {line_count} lines",
        )
        self.assertLessEqual(line_count, 360, f"Expected clean de-duplicated implementation <=360 lines, got {line_count}")

        card = get_ingestion_coordinator_card()
        self.assertIn("25% HITL gating", card.domain_focus)
        self.assertNotIn("20% HITL gating", card.domain_focus)

    async def test_execute_direct_dag_delegation_equivalence(self) -> None:
        """execute_direct_dag must delegate cleanly through handle_task and return complete contract."""
        coordinator = IngestionCoordinator(mock_mode=True)
        res = await coordinator.execute_direct_dag(
            meeting_name="Wave 2 Architecture Sync",
            meeting_date="09/13/2026",
            raw_transcript_text="Alex Rivera: Validating unified ingestion runner.",
            roster_context=["Alex Rivera", "harper Hoffman"],
            model_id="gpt-5.6-luna",
            reasoning_effort="high",
        )
        self.assertEqual(res["status"], "COMPLETED")
        self.assertEqual(res["execution_mode"], "DIRECT_DAG")
        self.assertEqual(res["model_id"], "gpt-5.6-luna")
        self.assertEqual(res["reasoning_effort"], "high")
        self.assertGreaterEqual(res["turn_count"], 2)
        self.assertIn("Ambiguity Lock Register (ALR)", res["artifact_01_markdown"])
        self.assertIsNotNone(res["claim_check_pointer"])
        self.assertTrue(res["l1_plan"].sanitization_passed)

    def test_pipeline_runner_run_agentic_dry_run_qa(self) -> None:
        """TranscriptIngestionPipelineRunner.run_agentic dry-run must pass QA gates and write artifacts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            manifest = create_sample_manifest(base_dir)

            runner = TranscriptIngestionPipelineRunner()
            res = runner.run_agentic(manifest, dry_run_qa=True)

            self.assertEqual(res["status"], "QA_PASSED_DRY_RUN")
            self.assertEqual(res["execution_mode"], "AGENTIC_DAG")
            self.assertTrue(res["qa_passed"])
            self.assertTrue(manifest.f1_path.exists())
            self.assertTrue(manifest.f2_path.exists())
            self.assertTrue(manifest.f3_path.exists())
            self.assertTrue(manifest.qa_report_path.exists())

    def test_pipeline_runner_run_agentic_full_release(self) -> None:
        """TranscriptIngestionPipelineRunner.run_agentic must complete the full release lifecycle."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            manifest = create_sample_manifest(base_dir)

            runner = TranscriptIngestionPipelineRunner()
            res = runner.run_agentic(manifest, dry_run_qa=False)

            self.assertEqual(res["status"], "COMPLETE")
            self.assertEqual(res["execution_mode"], "AGENTIC_DAG")
            self.assertTrue(res["qa_passed"])
            self.assertIn(res["release_verdict"], ("PASS", "PASS_ADVISORY"))
            self.assertTrue(Path(res["pkg_path"]).exists())


if __name__ == "__main__":
    unittest.main()
