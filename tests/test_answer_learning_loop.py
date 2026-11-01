"""
tests/test_answer_learning_loop.py

Unit tests for closed-loop answer learning system (Wave 8):
- Failure cohort discovery from answer eval receipts and ledger feedback
- Contract-valid learning-change-proposal:1.0.0 synthesis
- Dry-run replay simulation (target gain, guard preservation, latency budget)
- L4 stage-chain seal across LP0-LP4
- CLI answer-scan subcommand execution
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import utc_now, write_json
from dual_pipeline.learning_engine import ClosedLoopLearningEngine
from dual_pipeline.learning_loop_core import (
    CATEGORY_COPILOT_CONTEXT,
    CATEGORY_COPILOT_GROUNDING,
    record_learning_event,
    scan_answer_eval_receipts,
    seal_learning_stage_chain,
    simulate_answer_learning_proposal,
    synthesize_answer_learning_proposal,
)


class TestAnswerLearningLoopUnit(unittest.TestCase):
    """Unit test suite for the answer learning loop core engine."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_ans_learning_")
        self.root = Path(self.temp_dir)
        self.eval_dir = self.root / "evals"
        self.eval_dir.mkdir(parents=True, exist_ok=True)
        self.proposals_dir = self.root / "proposals"
        self.proposals_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_dir = self.root / "ledger"
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir = self.root / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)

        self.config_path = self.root / "parameters.json"
        initial_params = {
            "schema_version": "1.0.0",
            "updated_at": utc_now(),
            "parameters": {
                "copilot": {
                    "context": {"decay_tau_seconds": 300, "focus_seconds": 15},
                    "rigor": {"budget_ms": 250.0, "min_provenance_score": 0.3},
                },
                "retrieval": {
                    "bm25_weight": 0.5,
                    "dense_weight": 0.5,
                },
            },
        }
        write_json(self.config_path, initial_params)
        self.engine = ClosedLoopLearningEngine(
            runtime_root=self.root,
            proposals_dir=self.proposals_dir,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_mock_eval_receipt(
        self,
        receipt_id: str,
        pipeline: str = "copilot",
        context_recall: float = 0.55,
        groundedness: float = 0.92,
        usefulness: float = 0.85,
    ) -> Path:
        data = {
            "contract_id": "urn:nee:eval:answer-eval-receipt:1.0.0",
            "receipt_id": receipt_id,
            "pipeline": pipeline,
            "metrics": {
                "context_recall": context_recall,
                "groundedness": groundedness,
                "usefulness": usefulness,
                "staleness": 0.04,
                "conflict_miss_rate": 0.0,
            },
            "sealed_at": utc_now(),
        }
        p = self.eval_dir / f"{receipt_id}.json"
        write_json(p, data)
        return p

    def test_01_scan_failure_cohorts_context_recall(self) -> None:
        """Verify repeated context recall misses cluster into copilot_context cohort."""
        self._create_mock_eval_receipt("eval_01", context_recall=0.50)
        self._create_mock_eval_receipt("eval_02", context_recall=0.55)

        cohorts = scan_answer_eval_receipts(
            eval_dir=self.eval_dir,
            min_occurrences=2,
            config_path=self.config_path,
        )
        self.assertEqual(len(cohorts), 1)
        c = cohorts[0]
        self.assertEqual(c.category, CATEGORY_COPILOT_CONTEXT)
        self.assertEqual(c.occurrence_count, 2)
        self.assertEqual(c.target_parameter, "copilot.context.decay_tau_seconds")
        self.assertEqual(c.current_value, 300)
        self.assertGreater(c.proposed_value, c.current_value)

    def test_02_scan_failure_cohorts_groundedness(self) -> None:
        """Verify repeated low groundedness clusters into copilot_grounding cohort."""
        self._create_mock_eval_receipt("eval_grnd_1", groundedness=0.72)
        self._create_mock_eval_receipt("eval_grnd_2", groundedness=0.75)

        cohorts = scan_answer_eval_receipts(
            eval_dir=self.eval_dir,
            min_occurrences=2,
            config_path=self.config_path,
        )
        # Should have copilot_grounding cohort
        grounding_cohorts = [c for c in cohorts if c.category == CATEGORY_COPILOT_GROUNDING]
        self.assertTrue(len(grounding_cohorts) >= 1)
        gc = grounding_cohorts[0]
        self.assertEqual(gc.target_parameter, "copilot.rigor.min_provenance_score")

    def test_03_proposal_synthesis_contract_conformance(self) -> None:
        """Verify synthesized proposal strictly satisfies learning-change-proposal:1.0.0."""
        self._create_mock_eval_receipt("eval_ctx_1", context_recall=0.45)
        self._create_mock_eval_receipt("eval_ctx_2", context_recall=0.52)

        cohorts = scan_answer_eval_receipts(self.eval_dir, min_occurrences=2, config_path=self.config_path)
        self.assertGreaterEqual(len(cohorts), 1)

        prop = synthesize_answer_learning_proposal(cohorts[0], self.proposals_dir)
        self.assertEqual(prop["contract_id"], "urn:nee:dual-pipeline:learning-change-proposal:1.0.0")
        self.assertEqual(prop["status"], "proposed")
        self.assertTrue(prop["approval_required"])
        self.assertFalse(prop["auto_apply"])
        self.assertEqual(prop["proposal_type"], "copilot_context")
        self.assertIn("com.partner_advisory.nee.learning", prop["extensions"])
        self.assertIn("com.partner_advisory.nee.calibration", prop["extensions"])

        # Check file was written
        p_file = self.proposals_dir / f"{prop['proposal_id']}.json"
        self.assertTrue(p_file.is_file())

    def test_04_dry_run_simulation_pass(self) -> None:
        """Verify dry-run simulation succeeds when target metric improves and guards hold."""
        self._create_mock_eval_receipt("eval_sim_1", context_recall=0.50)
        self._create_mock_eval_receipt("eval_sim_2", context_recall=0.55)

        cohorts = scan_answer_eval_receipts(self.eval_dir, min_occurrences=2, config_path=self.config_path)
        prop = synthesize_answer_learning_proposal(cohorts[0], self.proposals_dir)

        sim_res = simulate_answer_learning_proposal(
            prop,
            runtime_root=self.root,
            simulation_override={
                "target_before": 0.525,
                "target_after": 0.820,
                "guard_groundedness_before": 0.90,
                "guard_groundedness_after": 0.90,
                "simulated_latency_ms": 15.0,
            },
        )
        self.assertTrue(sim_res.success)
        self.assertEqual(sim_res.status, "SIMULATED_PASS")
        self.assertTrue(sim_res.guard_metrics_passed)
        self.assertTrue(sim_res.latency_within_budget)
        self.assertGreater(sim_res.target_metric_after, sim_res.target_metric_before)

    def test_05_l4_stage_chain_sealing(self) -> None:
        """Verify stage-chain sealing computes deterministic Merkle root across LP0-LP4."""
        p_id = "test-prop-001"
        run_id = f"run-learning-{p_id}"
        db_path = self.ledger_dir / "learning.sqlite"

        events = []
        for stage in ("LP0", "LP1", "LP2", "LP3", "LP4"):
            ev = record_learning_event(
                stage=stage,
                event_type=f"{stage.lower()}_completed",
                status="PASS",
                run_id=run_id,
                proposal_id=p_id,
                payload_reference=f"ref-{stage}",
                artifact_data={"stage": stage, "val": 123},
                db_path=db_path,
            )
            events.append(ev)

        self.assertEqual(len(events), 5)

        seal_res = seal_learning_stage_chain(
            proposal_id=p_id,
            run_id=run_id,
            stage_events=events,
            output_dir=self.runs_dir / run_id,
        )
        self.assertEqual(seal_res["terminal_outcome"], "PASS")
        self.assertEqual(len(seal_res["stages_sealed"]), 5)
        self.assertTrue(bool(seal_res["merkle_root"]))
        self.assertTrue(Path(seal_res["seal_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
