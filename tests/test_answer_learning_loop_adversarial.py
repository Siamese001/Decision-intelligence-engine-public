"""
tests/test_answer_learning_loop_adversarial.py

Adversarial and boundary test suite for closed-loop answer learning (Wave 8):
- Synthetic/stop-hook approval rejection (validate_approval_origin)
- Out-of-bounds parameter rejection by ParameterRegistry and simulation
- Guard metric worsening rejection (simulation fails closed)
- Latency budget exhaustion rejection
- 11 BGE-M3 gate clearance enforcement for query_retrieval
- Tampered digest and missing stage seal handling
- Boundary and malformed eval receipt handling
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.calibration import create_signed_calibration_approval_receipt
from dual_pipeline.core import utc_now, write_json
from dual_pipeline.learning_loop_core import (
    CATEGORY_COPILOT_CONTEXT,
    CATEGORY_QUERY_RETRIEVAL,
    apply_answer_learning_proposal,
    scan_answer_eval_receipts,
    seal_learning_stage_chain,
    simulate_answer_learning_proposal,
    synthesize_answer_learning_proposal,
)
from dual_pipeline.parameters import ParameterRegistry


class TestAnswerLearningLoopAdversarial(unittest.TestCase):
    """Adversarial and invariant stress testing for the closed-loop learning engine."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_ans_adv_")
        self.root = Path(self.temp_dir)
        self.eval_dir = self.root / "evals"
        self.eval_dir.mkdir(parents=True, exist_ok=True)
        self.proposals_dir = self.root / "proposals"
        self.proposals_dir.mkdir(parents=True, exist_ok=True)
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

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_sample_proposal(
        self,
        category: str = CATEGORY_COPILOT_CONTEXT,
        param_name: str = "copilot.context.decay_tau_seconds",
        proposed_value: float = 450.0,
    ) -> dict:
        write_json(
            self.eval_dir / "eval_adv_1.json",
            {
                "contract_id": "urn:nee:eval:answer-eval-receipt:1.0.0",
                "receipt_id": "eval_adv_1",
                "pipeline": "copilot",
                "metrics": {"context_recall": 0.50},
                "sealed_at": utc_now(),
            },
        )
        write_json(
            self.eval_dir / "eval_adv_2.json",
            {
                "contract_id": "urn:nee:eval:answer-eval-receipt:1.0.0",
                "receipt_id": "eval_adv_2",
                "pipeline": "copilot",
                "metrics": {"context_recall": 0.52},
                "sealed_at": utc_now(),
            },
        )
        cohorts = scan_answer_eval_receipts(self.eval_dir, min_occurrences=2, config_path=self.config_path)
        cohort = cohorts[0]
        return synthesize_answer_learning_proposal(
            cohort,
            self.proposals_dir,
            parameter_override={
                "target_parameter": param_name,
                "proposed_value": proposed_value,
            },
        )

    def test_01_synthetic_approval_origin_rejected(self) -> None:
        """Synthetic stop-hook approval must be rejected via validate_approval_origin."""
        proposal = self._create_sample_proposal()
        synthetic_receipt = {
            "receipt_id": "receipt-synthetic-approval",
            "proposal_id": proposal["proposal_id"],
            "proposal_digest": proposal["canonical_digest"]["value"],
            "approved_by": "IDE Stop Hook Policy Reviewer",
            "approved_at": utc_now(),
            "origin": "IDE_AUTO_APPROVE",
            "extensions": {"stop_hook_auto_approved": True},
        }

        with self.assertRaises(PermissionError) as ctx:
            apply_answer_learning_proposal(
                proposal,
                synthetic_receipt,
                runtime_root=self.root,
                proposals_dir=self.proposals_dir,
                config_path=self.config_path,
            )
        self.assertIn("non-human approval origin", str(ctx.exception))

    def test_02_out_of_bounds_parameters_rejected(self) -> None:
        """Out-of-bounds parameter values must be rejected by ParameterRegistry and simulation."""
        registry = ParameterRegistry(config_path=self.config_path)

        # Upper bound violation (> 1800)
        with self.assertRaises(ValueError) as ctx:
            registry.set("copilot.context.decay_tau_seconds", 2500.0, config_path=self.config_path)
        self.assertIn("out of bounds", str(ctx.exception))

        # Lower bound violation (< 30)
        with self.assertRaises(ValueError) as ctx:
            registry.set("copilot.context.decay_tau_seconds", 10.0, config_path=self.config_path)
        self.assertIn("out of bounds", str(ctx.exception))

        # Simulation must also fail closed
        oob_proposal = self._create_sample_proposal(proposed_value=3000.0)
        sim_res = simulate_answer_learning_proposal(oob_proposal, runtime_root=self.root)
        self.assertFalse(sim_res.success)
        self.assertEqual(sim_res.status, "SIMULATED_FAIL")
        self.assertIn("outside governed bounds", sim_res.details.get("error", ""))

    def test_03_guard_metric_worsening_fails_simulation(self) -> None:
        """Proposal that degrades guard metrics must fail simulation and cannot be approved."""
        proposal = self._create_sample_proposal(proposed_value=450.0)

        # Degraded groundedness
        sim_res = simulate_answer_learning_proposal(
            proposal,
            runtime_root=self.root,
            simulation_override={
                "target_before": 0.50,
                "target_after": 0.85,
                "guard_groundedness_before": 0.90,
                "guard_groundedness_after": 0.70,  # Worsened!
            },
        )
        self.assertFalse(sim_res.success)
        self.assertEqual(sim_res.status, "SIMULATED_FAIL")
        self.assertFalse(sim_res.guard_metrics_passed)
        self.assertIn("Guard metric invariant violation", sim_res.details.get("error", ""))

    def test_04_latency_budget_exhaustion_fails_simulation(self) -> None:
        """Simulated latency exceeding copilot.rigor.budget_ms fails simulation."""
        proposal = self._create_sample_proposal(proposed_value=450.0)

        sim_res = simulate_answer_learning_proposal(
            proposal,
            runtime_root=self.root,
            simulation_override={
                "target_before": 0.50,
                "target_after": 0.85,
                "simulated_latency_ms": 350.0,  # Exceeds 250.0 ms budget!
            },
        )
        self.assertFalse(sim_res.success)
        self.assertEqual(sim_res.status, "SIMULATED_FAIL")
        self.assertFalse(sim_res.latency_within_budget)
        self.assertIn("exceeds budget", sim_res.details.get("error", ""))

    def test_05_query_retrieval_fails_without_11_bge_m3_gates(self) -> None:
        """query_retrieval proposals are blocked unless all 11 BGE-M3 gates pass."""
        write_json(
            self.eval_dir / "eval_query_1.json",
            {
                "contract_id": "urn:nee:eval:answer-eval-receipt:1.0.0",
                "receipt_id": "eval_query_1",
                "pipeline": "query",
                "metrics": {"usefulness": 0.50},
                "sealed_at": utc_now(),
            },
        )
        write_json(
            self.eval_dir / "eval_query_2.json",
            {
                "contract_id": "urn:nee:eval:answer-eval-receipt:1.0.0",
                "receipt_id": "eval_query_2",
                "pipeline": "query",
                "metrics": {"usefulness": 0.55},
                "sealed_at": utc_now(),
            },
        )
        cohorts = scan_answer_eval_receipts(self.eval_dir, min_occurrences=2, config_path=self.config_path)
        query_cohort = [c for c in cohorts if c.category == CATEGORY_QUERY_RETRIEVAL][0]
        query_prop = synthesize_answer_learning_proposal(query_cohort, self.proposals_dir)

        # 1. No gates provided -> fails
        res1 = simulate_answer_learning_proposal(query_prop, runtime_root=self.root)
        self.assertFalse(res1.success)
        self.assertFalse(res1.bge_m3_gates_passed)

        # 2. Incomplete gates (only 5 of 11) -> fails
        partial_gates = {
            "real_bge_m3": True,
            "pair_binding": True,
            "coverage_complete": True,
            "core_thresholds": True,
            "delta_thresholds": True,
        }
        res2 = simulate_answer_learning_proposal(
            query_prop, runtime_root=self.root, bge_gate_results=partial_gates
        )
        self.assertFalse(res2.success)
        self.assertFalse(res2.bge_m3_gates_passed)

        # 3. All 11 gates pass -> succeeds
        all_11_gates = {
            "real_bge_m3": True,
            "pair_binding": True,
            "coverage_complete": True,
            "core_thresholds": True,
            "delta_thresholds": True,
            "critical_cases": True,
            "rank_stability": True,
            "repeat_stability": True,
            "forbidden_leakage": True,
            "latency": True,
            "bm25_execution": True,
        }
        res3 = simulate_answer_learning_proposal(
            query_prop, runtime_root=self.root, bge_gate_results=all_11_gates
        )
        self.assertTrue(res3.success)
        self.assertTrue(res3.bge_m3_gates_passed)

    def test_06_tampered_proposal_digest_fails_closed(self) -> None:
        """Approval receipt with tampered proposal digest fails verification."""
        proposal = self._create_sample_proposal()
        valid_receipt = create_signed_calibration_approval_receipt(
            proposal=proposal,
            approved_by="operator-lead",
            origin="USER_EXPLICIT",
        )
        # Tamper with digest
        valid_receipt["proposal_digest"] = "0000000000000000000000000000000000000000000000000000000000000000"

        with self.assertRaises(PermissionError) as ctx:
            apply_answer_learning_proposal(
                proposal,
                valid_receipt,
                runtime_root=self.root,
                proposals_dir=self.proposals_dir,
                config_path=self.config_path,
            )
        self.assertIn("approval verification failed", str(ctx.exception))

    def test_07_malformed_and_boundary_receipts_handled_gracefully(self) -> None:
        """Scanner handles empty files, malformed JSON, and empty directories without crashing."""
        # 0-byte file
        (self.eval_dir / "zero_byte.json").write_text("", encoding="utf-8")
        # Malformed JSON
        (self.eval_dir / "corrupted.json").write_text("{truncated...", encoding="utf-8")
        # Empty JSON object
        (self.eval_dir / "empty_obj.json").write_text("{}", encoding="utf-8")

        cohorts = scan_answer_eval_receipts(self.eval_dir, min_occurrences=2, config_path=self.config_path)
        self.assertEqual(len(cohorts), 0)

    def test_08_missing_stage_in_sealing_fails_closed(self) -> None:
        """Stage-chain sealing fails if any mandatory stage LP0-LP4 is absent."""
        incomplete_events = [
            {"stage": "LP0", "status": "PASS"},
            {"stage": "LP1", "status": "PASS"},
            # LP2 missing!
            {"stage": "LP3", "status": "PASS"},
            {"stage": "LP4", "status": "PASS"},
        ]
        with self.assertRaises(ValueError) as ctx:
            seal_learning_stage_chain(
                proposal_id="prop-incomplete",
                run_id="run-incomplete",
                stage_events=incomplete_events,
            )
        self.assertIn("missing mandatory stages", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
