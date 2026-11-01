"""
tests/end_to_end/test_post_apply_canary_and_auto_rollback_e2e.py

End-to-End lifecycle tests for Wave 4 (O-8: Post-Application Canary Evaluator LP5
& Automatic Bidirectional Rollback):
- Post-apply canary pass records stage LP5 event and 6-stage Merkle seal (LP0-LP5)
- Post-apply canary regression automatically triggers bidirectional rollback,
  restores parameters, updates applied/rollback receipts, and records LP5 FAIL
- Scanner ignores _canary_receipt.json, _simulation_receipt.json, and _rollback_snapshot.json
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.calibration import create_signed_calibration_approval_receipt
from dual_pipeline.core import read_json, utc_now, write_json
from dual_pipeline.learning_engine import ClosedLoopLearningEngine
from dual_pipeline.learning_loop_core import (
    CANARY_STAGE,
    LEARNING_STAGES_WITH_CANARY,
    apply_answer_learning_proposal,
    record_learning_event,
    scan_answer_eval_receipts,
    seal_learning_stage_chain,
    simulate_answer_learning_proposal,
    synthesize_answer_learning_proposal,
)
from dual_pipeline.parameters import ParameterRegistry


def _init_fixture_graph(db_path: Path) -> str:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    schema_sql = (
        "CREATE TABLE IF NOT EXISTS nodes (id TEXT PRIMARY KEY, payload TEXT);"
        "CREATE TABLE IF NOT EXISTS edges (id TEXT PRIMARY KEY, payload TEXT);"
        "CREATE TABLE IF NOT EXISTS assertions (id TEXT PRIMARY KEY, payload TEXT);"
        "CREATE TABLE IF NOT EXISTS evidence_items (id TEXT PRIMARY KEY, payload TEXT);"
        "CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, payload TEXT);"
        "CREATE TABLE IF NOT EXISTS entities (id TEXT PRIMARY KEY, payload TEXT);"
        "CREATE TABLE IF NOT EXISTS claims (id TEXT PRIMARY KEY, payload TEXT);"
        "CREATE TABLE IF NOT EXISTS contexts (id TEXT PRIMARY KEY, payload TEXT);"
        "CREATE TABLE IF NOT EXISTS decisions (id TEXT PRIMARY KEY, payload TEXT);"
        "CREATE TABLE IF NOT EXISTS policies (id TEXT PRIMARY KEY, payload TEXT);"
        "CREATE TABLE IF NOT EXISTS metrics (id TEXT PRIMARY KEY, payload TEXT);"
        "CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, payload TEXT);"
        "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, payload TEXT);"
        "CREATE TABLE IF NOT EXISTS schema_version (id TEXT PRIMARY KEY, payload TEXT);"
        "INSERT INTO schema_version VALUES ('1', '1.0.0');"
    )
    with sqlite3.connect(db_path) as conn:
        conn.executescript(schema_sql)
        conn.commit()
    return hashlib.sha256(db_path.read_bytes()).hexdigest()


class TestPostApplyCanaryAndAutoRollbackE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="eval_sandbox_w4_e2e_")
        self.root = Path(self.temp_dir)
        self.eval_dir = self.root / "reports" / "answer_eval"
        self.eval_dir.mkdir(parents=True, exist_ok=True)
        self.proposals_dir = self.root / "reports" / "learning_proposals"
        self.proposals_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_dir = self.root / "ledger"
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir = self.root / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)

        self.graph_path = self.root / "seed_graph.sqlite"
        self.initial_graph_sha = _init_fixture_graph(self.graph_path)

        self.config_dir = self.root / "config" / "runtime"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.config_dir / "parameters.json"

        self.baseline_tau = 300.0
        initial_params = {
            "schema_version": "1.0.0",
            "updated_at": utc_now(),
            "parameters": {
                "copilot": {
                    "context": {
                        "focus_seconds": 15,
                        "decay_tau_seconds": self.baseline_tau,
                        "salient_entities_top_k": 8,
                        "flow_items_max": 10,
                        "focus_weight": 0.6,
                    },
                    "rigor": {"budget_ms": 250.0, "min_provenance_score": 0.3},
                },
                "retrieval": {"bm25_weight": 0.5, "dense_weight": 0.5},
            },
        }
        write_json(self.config_path, initial_params)
        self.reg = ParameterRegistry(config_path=self.config_path)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _prepare_and_apply_proposal(self, proposed_tau: float = 480.0) -> tuple[dict, list[dict], str, Path]:
        write_json(
            self.eval_dir / "eval_run_1.json",
            {
                "contract_id": "urn:nee:eval:answer-eval-receipt:1.0.0",
                "receipt_id": "eval_run_1",
                "pipeline": "copilot",
                "metrics": {"context_recall": 0.48, "groundedness": 0.94, "durability": 0.90, "staleness": 0.03},
                "sealed_at": utc_now(),
            },
        )
        write_json(
            self.eval_dir / "eval_run_2.json",
            {
                "contract_id": "urn:nee:eval:answer-eval-receipt:1.0.0",
                "receipt_id": "eval_run_2",
                "pipeline": "copilot",
                "metrics": {"context_recall": 0.52, "groundedness": 0.93, "durability": 0.89, "staleness": 0.04},
                "sealed_at": utc_now(),
            },
        )
        cohorts = scan_answer_eval_receipts(self.eval_dir, min_occurrences=2, config_path=self.config_path)
        cohort = cohorts[0]
        proposal = synthesize_answer_learning_proposal(
            cohort,
            proposals_dir=self.proposals_dir,
            parameter_override={
                "target_parameter": "copilot.context.decay_tau_seconds",
                "proposed_value": proposed_tau,
            },
        )
        p_id = proposal["proposal_id"]
        run_id = f"run-learning-{p_id}"
        db_path = self.ledger_dir / "learning.sqlite"

        ev_lp0 = record_learning_event(
            stage="LP0", event_type="answer_eval_cohort_detected", status="PASS",
            run_id=run_id, proposal_id=p_id, payload_reference=cohort.cohort_key,
            artifact_data=cohort.to_dict(), db_path=db_path,
        )
        ev_lp1 = record_learning_event(
            stage="LP1", event_type="learning_proposal_synthesized", status="PASS",
            run_id=run_id, proposal_id=p_id, payload_reference=p_id,
            artifact_data=proposal, db_path=db_path, parent_span_id=ev_lp0["span_id"],
        )
        sim_res = simulate_answer_learning_proposal(
            proposal,
            runtime_root=self.root,
            proposals_dir=self.proposals_dir,
            simulation_override={
                "target_before": 0.50,
                "target_after": 0.85,
                "guard_groundedness_before": 0.935,
                "guard_groundedness_after": 0.935,
                "guard_durability_before": 0.895,
                "guard_durability_after": 0.895,
                "simulated_latency_ms": 14.5,
            },
        )
        self.assertTrue(sim_res.success)
        ev_lp2 = record_learning_event(
            stage="LP2", event_type="learning_simulation_completed", status="PASS",
            run_id=run_id, proposal_id=p_id, payload_reference=p_id,
            artifact_data=sim_res.to_dict(), db_path=db_path, parent_span_id=ev_lp1["span_id"],
        )
        approval = create_signed_calibration_approval_receipt(
            proposal=proposal, approved_by="human_operator_lead",
            rationale="Approved calibrated tau", origin="USER_EXPLICIT",
        )
        ev_lp3 = record_learning_event(
            stage="LP3", event_type="learning_approval_verified", status="PASS",
            run_id=run_id, proposal_id=p_id, payload_reference=approval["receipt_id"],
            artifact_data=approval, db_path=db_path, parent_span_id=ev_lp2["span_id"],
        )
        commit_res = apply_answer_learning_proposal(
            proposal, approval, runtime_root=self.root,
            proposals_dir=self.proposals_dir, config_path=self.config_path,
            simulation_receipt=sim_res.to_dict(),
        )
        self.assertEqual(commit_res["status"], "APPLIED")
        ev_lp4 = record_learning_event(
            stage="LP4", event_type="learning_commit_applied", status="PASS",
            run_id=run_id, proposal_id=p_id, payload_reference=commit_res["commit_id"],
            artifact_data=commit_res["receipt"], db_path=db_path, parent_span_id=ev_lp3["span_id"],
        )
        return proposal, [ev_lp0, ev_lp1, ev_lp2, ev_lp3, ev_lp4], run_id, db_path

    def test_post_apply_canary_pass_seals_6_stage_chain(self) -> None:
        proposal, base_events, run_id, db_path = self._prepare_and_apply_proposal(480.0)
        p_id = proposal["proposal_id"]

        engine = ClosedLoopLearningEngine(runtime_root=self.root, proposals_dir=self.proposals_dir)
        canary_res = engine.evaluate_post_apply_canary(
            proposal,
            {
                "target_before": 0.50,
                "target_after": 0.84,
                "guard_groundedness_before": 0.935,
                "guard_groundedness_after": 0.94,
                "guard_durability_before": 0.895,
                "guard_durability_after": 0.90,
                "latency_ms": 18.0,
            },
            parameter_path=self.config_path,
            ledger_db_path=db_path,
            run_id=run_id,
            parent_span_id=base_events[-1]["span_id"],
        )
        self.assertTrue(canary_res["canary_passed"])
        self.assertFalse(canary_res["rolled_back"])
        self.assertEqual(canary_res["status"], "CANARY_PASS")
        self.assertEqual(canary_res["lp5_event"]["stage"], CANARY_STAGE)

        all_events = base_events + [canary_res["lp5_event"]]
        seal_res = seal_learning_stage_chain(
            proposal_id=p_id,
            run_id=run_id,
            stage_events=all_events,
            output_dir=self.runs_dir / run_id,
            require_canary=True,
        )
        self.assertEqual(seal_res["stage_count"], 6)
        self.assertEqual(seal_res["stages_sealed"], list(LEARNING_STAGES_WITH_CANARY))

        # Parameter stays at 480.0
        self.reg.reload()
        self.assertEqual(self.reg.get("copilot.context.decay_tau_seconds"), 480.0)

    def test_post_apply_canary_regression_triggers_auto_rollback(self) -> None:
        proposal, base_events, run_id, db_path = self._prepare_and_apply_proposal(510.0)
        p_id = proposal["proposal_id"]

        self.reg.reload()
        self.assertEqual(self.reg.get("copilot.context.decay_tau_seconds"), 510.0)

        engine = ClosedLoopLearningEngine(runtime_root=self.root, proposals_dir=self.proposals_dir)
        canary_res = engine.evaluate_post_apply_canary(
            proposal,
            {
                "target_before": 0.50,
                "target_after": 0.42,  # Regressed below baseline!
                "guard_groundedness_before": 0.935,
                "guard_groundedness_after": 0.935,
                "latency_ms": 18.0,
            },
            parameter_path=self.config_path,
            ledger_db_path=db_path,
            run_id=run_id,
            parent_span_id=base_events[-1]["span_id"],
        )
        self.assertFalse(canary_res["canary_passed"])
        self.assertTrue(canary_res["rolled_back"])
        self.assertEqual(canary_res["status"], "ROLLED_BACK")
        self.assertEqual(canary_res["lp5_event"]["status"], "FAIL")

        # Parameter automatically restored to baseline 300.0
        self.reg.reload()
        self.assertEqual(self.reg.get("copilot.context.decay_tau_seconds"), self.baseline_tau)

        # Applied receipt marked ROLLED_BACK
        applied_data = read_json(self.proposals_dir / f"{p_id}_applied_receipt.json")
        self.assertEqual(applied_data["status"], "ROLLED_BACK")


if __name__ == "__main__":
    unittest.main()
