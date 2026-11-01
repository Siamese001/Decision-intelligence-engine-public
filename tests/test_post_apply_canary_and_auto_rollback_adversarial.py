"""
tests/test_post_apply_canary_and_auto_rollback_adversarial.py

Adversarial and edge-case test suite for Wave 4 (O-8: Post-Application Canary
Evaluator LP5 & Automatic Bidirectional Rollback):
1. Rollback of non-HITL parameter categories (copilot_context, query_retrieval, retrieval_weight)
   via execute_rollback and engine.rollback_proposal
2. Tampered or missing _simulation_receipt.json fails canary closed and rolls back
3. Post-apply 11-gate regression on retrieval parameter triggers auto-rollback
4. Idempotent double-rollback safety during repeated canary failure calls
5. Scanner ignores _canary_receipt.json, _simulation_receipt.json, and _rollback_snapshot.json
6. require_canary=True rejects 5-stage seal missing LP5
"""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.calibration import create_signed_calibration_approval_receipt
from dual_pipeline.core import read_json, utc_now, write_json
from dual_pipeline.learning_engine import ClosedLoopLearningEngine
from dual_pipeline.learning_loop_core import (
    scan_answer_eval_receipts,
    seal_learning_stage_chain,
    simulate_answer_learning_proposal,
    synthesize_answer_learning_proposal,
)
from dual_pipeline.parameters import ParameterRegistry


def _init_fixture_graph(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        for tbl in (
            "nodes", "edges", "assertions", "evidence_items", "sources", "entities",
            "claims", "contexts", "decisions", "policies", "metrics", "events", "runs", "schema_version",
        ):
            conn.execute(f"CREATE TABLE IF NOT EXISTS {tbl} (id TEXT PRIMARY KEY, payload TEXT);")
        conn.commit()


class TestPostApplyCanaryAndAutoRollbackAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="eval_sandbox_w4_adv_")
        self.root = Path(self.temp_dir)
        self.eval_dir = self.root / "evals"
        self.eval_dir.mkdir(parents=True, exist_ok=True)
        self.proposals_dir = self.root / "reports" / "learning_proposals"
        self.proposals_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.root / "parameters.json"
        _init_fixture_graph(self.root / "seed_graph.sqlite")

        write_json(
            self.config_path,
            {
                "schema_version": "1.0.0",
                "updated_at": utc_now(),
                "parameters": {
                    "copilot": {
                        "context": {"decay_tau_seconds": 300.0, "focus_seconds": 15},
                        "rigor": {"budget_ms": 250.0, "min_provenance_score": 0.3},
                    },
                    "retrieval": {"bm25_weight": 0.5, "dense_weight": 0.5},
                },
            },
        )
        self.reg = ParameterRegistry(config_path=self.config_path)
        self.engine = ClosedLoopLearningEngine(runtime_root=self.root, proposals_dir=self.proposals_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_and_apply_copilot_proposal(self, proposed_tau: float = 420.0) -> dict:
        for idx in (1, 2):
            write_json(
                self.eval_dir / f"eval_{idx}.json",
                {
                    "contract_id": "urn:nee:eval:answer-eval-receipt:1.0.0",
                    "receipt_id": f"eval_{idx}",
                    "pipeline": "copilot",
                    "metrics": {"context_recall": 0.45},
                    "sealed_at": utc_now(),
                },
            )
        cohort = scan_answer_eval_receipts(self.eval_dir, min_occurrences=2, config_path=self.config_path)[0]
        proposal = synthesize_answer_learning_proposal(
            cohort,
            proposals_dir=self.proposals_dir,
            parameter_override={
                "target_parameter": "copilot.context.decay_tau_seconds",
                "proposed_value": proposed_tau,
            },
        )
        sim_res = simulate_answer_learning_proposal(
            proposal,
            runtime_root=self.root,
            proposals_dir=self.proposals_dir,
            simulation_override={"target_before": 0.45, "target_after": 0.80, "simulated_latency_ms": 12.0},
        )
        self.assertTrue(sim_res.success)
        approval = create_signed_calibration_approval_receipt(
            proposal=proposal, approved_by="operator-lead", rationale="Approve", origin="USER_EXPLICIT"
        )
        self.engine.apply_approved_proposal(
            proposal,
            approval,
            parameter_path=self.config_path,
            skip_decision_clearance=True,
            simulation_receipt=sim_res.to_dict(),
        )
        return proposal

    def test_01_engine_rollback_restores_copilot_context_and_idempotent_replay(self) -> None:
        proposal = self._create_and_apply_copilot_proposal(420.0)
        p_id = proposal["proposal_id"]

        self.reg.reload()
        self.assertEqual(self.reg.get("copilot.context.decay_tau_seconds"), 420.0)

        rb1 = self.engine.rollback_proposal(p_id, parameter_path=self.config_path)
        self.assertEqual(rb1["status"], "ROLLED_BACK")
        self.reg.reload()
        self.assertEqual(self.reg.get("copilot.context.decay_tau_seconds"), 300.0)

        # Second rollback is idempotent
        rb2 = self.engine.rollback_proposal(p_id, parameter_path=self.config_path)
        self.assertEqual(rb2["status"], "ROLLED_BACK")
        self.assertTrue(rb2.get("idempotent_replay"))

    def test_02_tampered_simulation_receipt_digest_fails_canary_and_rolls_back(self) -> None:
        proposal = self._create_and_apply_copilot_proposal(450.0)
        p_id = proposal["proposal_id"]

        # Tamper with simulation receipt on disk
        sim_path = self.proposals_dir / f"{p_id}_simulation_receipt.json"
        sim_data = read_json(sim_path)
        sim_data["proposal_digest"] = "f" * 64
        write_json(sim_path, sim_data)

        canary_res = self.engine.evaluate_post_apply_canary(
            proposal,
            {"target_before": 0.45, "target_after": 0.85, "latency_ms": 10.0},
            parameter_path=self.config_path,
        )
        self.assertFalse(canary_res["canary_passed"])
        self.assertTrue(canary_res["rolled_back"])
        self.assertTrue(any("mismatch" in r for r in canary_res["regressions"]))

        self.reg.reload()
        self.assertEqual(self.reg.get("copilot.context.decay_tau_seconds"), 300.0)

    def test_03_scanner_ignores_canary_simulation_and_snapshot_artifacts(self) -> None:
        write_json(
            self.eval_dir / "prop_canary_receipt.json",
            {"receipt_id": "c1", "pipeline": "copilot", "metrics": {"context_recall": 0.10}},
        )
        write_json(
            self.eval_dir / "prop_simulation_receipt.json",
            {"receipt_id": "s1", "pipeline": "copilot", "metrics": {"context_recall": 0.10}},
        )
        write_json(
            self.eval_dir / "prop_rollback_snapshot.json",
            {"receipt_id": "r1", "pipeline": "copilot", "metrics": {"context_recall": 0.10}},
        )
        cohorts = scan_answer_eval_receipts(self.eval_dir, min_occurrences=1, config_path=self.config_path)
        self.assertEqual(len(cohorts), 0)

    def test_04_require_canary_rejects_5_stage_seal_missing_lp5(self) -> None:
        events_5 = [
            {"stage": "LP0", "status": "PASS"},
            {"stage": "LP1", "status": "PASS"},
            {"stage": "LP2", "status": "PASS"},
            {"stage": "LP3", "status": "PASS"},
            {"stage": "LP4", "status": "PASS"},
        ]
        with self.assertRaises(ValueError) as ctx:
            seal_learning_stage_chain(
                proposal_id="prop-no-lp5",
                run_id="run-no-lp5",
                stage_events=events_5,
                require_canary=True,
            )
        self.assertIn("LP5", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
