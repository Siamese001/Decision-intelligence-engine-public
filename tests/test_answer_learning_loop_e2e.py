"""
tests/test_answer_learning_loop_e2e.py

End-to-End lifecycle test for closed-loop answer learning via UWG (Wave 8):
1. Ingest fixture answer eval receipts with recurring failure patterns
2. Cluster into failure cohort and synthesize learning-change-proposal:1.0.0
3. Dry-run replay simulation (target improvement, guard metrics, latency)
4. Cryptographic Ed25519 human approval binding (USER_EXPLICIT)
5. UniversalWriteGateway commit (status: APPLIED, +0 graph mutation, dual receipts)
6. Parameter change visible to next copilot context model session
7. Stage-chain Merkle seal across LP0-LP4
8. Exact bidirectional rollback restoring original parameters (digest match)
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
from dual_pipeline.learning_loop_core import (
    CATEGORY_COPILOT_CONTEXT,
    apply_answer_learning_proposal,
    record_learning_event,
    rollback_answer_learning_proposal,
    scan_answer_eval_receipts,
    seal_learning_stage_chain,
    simulate_answer_learning_proposal,
    synthesize_answer_learning_proposal,
)
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import (
    ConversationContextModel,
)
from dual_pipeline.parameters import ParameterRegistry

CANONICAL_TABLES = (
    "nodes",
    "edges",
    "assertions",
    "evidence_items",
    "sources",
    "entities",
    "claims",
    "contexts",
    "decisions",
    "policies",
    "metrics",
    "events",
    "runs",
    "schema_version",
)


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
    with open(db_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class TestAnswerLearningLoopE2E(unittest.TestCase):
    """Complete End-to-End integration test for the closed-loop answer learning system."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_ans_e2e_")
        self.root = Path(self.temp_dir)
        self.eval_dir = self.root / "reports" / "answer_eval"
        self.eval_dir.mkdir(parents=True, exist_ok=True)
        self.proposals_dir = self.root / "reports" / "learning_proposals"
        self.proposals_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_dir = self.root / "ledger"
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir = self.root / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)

        # Initialize mock runtime graph and active pair
        self.graph_path = self.root / "seed_graph.sqlite"
        self.initial_graph_sha = _init_fixture_graph(self.graph_path)

        self.config_dir = self.root / "config" / "runtime"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.config_dir / "parameters.json"

        self.baseline_tau = 300
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
                    "rigor": {
                        "budget_ms": 250.0,
                        "min_provenance_score": 0.3,
                    },
                    "neighborhood_max_hops": 2,
                    "rerank": {"lexical": 0.3, "structural": 0.3, "dense": 0.4},
                },
                "retrieval": {
                    "bm25_weight": 0.5,
                    "dense_weight": 0.5,
                },
            },
        }
        write_json(self.config_path, initial_params)
        self.reg = ParameterRegistry(config_path=self.config_path)
        self.initial_config_digest = self.reg.compute_digest()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_full_closed_loop_learning_lifecycle_e2e(self) -> None:
        """Executes full 5-phase learning loop from failure receipt to UWG commit and rollback."""
        # -------------------------------------------------------------
        # Phase 1: Ingest Failure Receipts
        # -------------------------------------------------------------
        write_json(
            self.eval_dir / "answer_eval_run1_receipt.json",
            {
                "contract_id": "urn:nee:eval:answer-eval-receipt:1.0.0",
                "receipt_id": "receipt_eval_run1",
                "pipeline": "copilot",
                "metrics": {
                    "context_recall": 0.48,
                    "groundedness": 0.94,
                    "durability": 0.90,
                    "staleness": 0.03,
                },
                "sealed_at": utc_now(),
            },
        )
        write_json(
            self.eval_dir / "answer_eval_run2_receipt.json",
            {
                "contract_id": "urn:nee:eval:answer-eval-receipt:1.0.0",
                "receipt_id": "receipt_eval_run2",
                "pipeline": "copilot",
                "metrics": {
                    "context_recall": 0.52,
                    "groundedness": 0.93,
                    "durability": 0.89,
                    "staleness": 0.04,
                },
                "sealed_at": utc_now(),
            },
        )

        cohorts = scan_answer_eval_receipts(
            self.eval_dir, min_occurrences=2, config_path=self.config_path
        )
        self.assertGreaterEqual(len(cohorts), 1)
        cohort = cohorts[0]
        self.assertEqual(cohort.category, CATEGORY_COPILOT_CONTEXT)
        self.assertEqual(cohort.occurrence_count, 2)
        self.assertEqual(cohort.target_parameter, "copilot.context.decay_tau_seconds")
        self.assertEqual(cohort.current_value, self.baseline_tau)

        # -------------------------------------------------------------
        # Phase 2: Synthesize Learning Proposal (LP0 -> LP1)
        # -------------------------------------------------------------
        proposal = synthesize_answer_learning_proposal(
            cohort,
            proposals_dir=self.proposals_dir,
            parameter_override={
                "target_parameter": "copilot.context.decay_tau_seconds",
                "proposed_value": 450.0,
            },
        )
        p_id = proposal["proposal_id"]
        run_id = f"run-learning-{p_id}"
        db_path = self.ledger_dir / "learning.sqlite"

        ev_lp0 = record_learning_event(
            stage="LP0",
            event_type="answer_eval_cohort_detected",
            status="PASS",
            run_id=run_id,
            proposal_id=p_id,
            payload_reference=cohort.cohort_key,
            artifact_data=cohort.to_dict(),
            db_path=db_path,
        )
        ev_lp1 = record_learning_event(
            stage="LP1",
            event_type="learning_proposal_synthesized",
            status="PASS",
            run_id=run_id,
            proposal_id=p_id,
            payload_reference=p_id,
            artifact_data=proposal,
            db_path=db_path,
            parent_span_id=ev_lp0["span_id"],
        )

        # -------------------------------------------------------------
        # Phase 3: Dry-Run Replay Simulation (LP2)
        # -------------------------------------------------------------
        sim_res = simulate_answer_learning_proposal(
            proposal,
            runtime_root=self.root,
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
        self.assertEqual(sim_res.status, "SIMULATED_PASS")

        ev_lp2 = record_learning_event(
            stage="LP2",
            event_type="learning_simulation_completed",
            status="PASS",
            run_id=run_id,
            proposal_id=p_id,
            payload_reference=p_id,
            artifact_data=sim_res.to_dict(),
            db_path=db_path,
            parent_span_id=ev_lp1["span_id"],
        )

        # -------------------------------------------------------------
        # Phase 4: Authentic Human Approval Binding (LP3)
        # -------------------------------------------------------------
        approval_receipt = create_signed_calibration_approval_receipt(
            proposal=proposal,
            approved_by="human_operator_lead",
            rationale="Approved calibrated tau to increase copilot context recall",
            origin="USER_EXPLICIT",
        )
        ev_lp3 = record_learning_event(
            stage="LP3",
            event_type="learning_approval_verified",
            status="PASS",
            run_id=run_id,
            proposal_id=p_id,
            payload_reference=approval_receipt["receipt_id"],
            artifact_data=approval_receipt,
            db_path=db_path,
            parent_span_id=ev_lp2["span_id"],
        )

        # -------------------------------------------------------------
        # Phase 5: Universal Write Gateway Commit (LP4)
        # -------------------------------------------------------------
        commit_res = apply_answer_learning_proposal(
            proposal,
            approval_receipt,
            runtime_root=self.root,
            proposals_dir=self.proposals_dir,
            config_path=self.config_path,
        )
        self.assertEqual(commit_res["status"], "APPLIED")
        self.assertEqual(commit_res["graph_delta_counts"]["total_delta"], 0)

        ev_lp4 = record_learning_event(
            stage="LP4",
            event_type="learning_commit_applied",
            status="PASS",
            run_id=run_id,
            proposal_id=p_id,
            payload_reference=commit_res["commit_id"],
            artifact_data=commit_res["receipt"],
            db_path=db_path,
            parent_span_id=ev_lp3["span_id"],
        )

        # -------------------------------------------------------------
        # Phase 6: Verify Next-Session Copilot Context Model Visibility
        # -------------------------------------------------------------
        self.reg.reload()
        new_tau = self.reg.get("copilot.context.decay_tau_seconds")
        self.assertEqual(new_tau, 450.0)

        # New copilot context model session picks up new decay_tau_seconds
        model = ConversationContextModel(decay_tau_seconds=new_tau)
        self.assertEqual(model.decay_tau, 450.0)

        # Zero Graph Mutation verification: graph SHA unchanged
        with open(self.graph_path, "rb") as f:
            current_graph_sha = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(current_graph_sha, self.initial_graph_sha)

        # -------------------------------------------------------------
        # Phase 7: L4 Stage-Chain Sealing
        # -------------------------------------------------------------
        all_events = [ev_lp0, ev_lp1, ev_lp2, ev_lp3, ev_lp4]
        seal_res = seal_learning_stage_chain(
            proposal_id=p_id,
            run_id=run_id,
            stage_events=all_events,
            output_dir=self.runs_dir / run_id,
        )
        self.assertEqual(seal_res["terminal_outcome"], "PASS")
        self.assertEqual(len(seal_res["stages_sealed"]), 5)
        self.assertTrue(bool(seal_res["merkle_root"]))

        # -------------------------------------------------------------
        # Phase 8: Bidirectional Rollback
        # -------------------------------------------------------------
        rollback_receipt_path = commit_res["rollback_receipt_path"]
        self.assertTrue(Path(rollback_receipt_path).is_file())

        rb_res = rollback_answer_learning_proposal(
            rollback_receipt_path,
            runtime_root=self.root,
        )
        self.assertEqual(rb_res["status"], "ROLLED_BACK")

        # Verify exact parameter restoration (digest match)
        self.reg.reload()
        restored_tau = self.reg.get("copilot.context.decay_tau_seconds")
        self.assertEqual(restored_tau, self.baseline_tau)

        current_config_digest = self.reg.compute_digest()
        self.assertEqual(current_config_digest, self.initial_config_digest)

        # Verify applied receipt status was updated to ROLLED_BACK
        applied_receipt_path = commit_res["receipt_path"]
        applied_data = read_json(Path(applied_receipt_path))
        self.assertEqual(applied_data["status"], "ROLLED_BACK")

        # Verify zero graph mutation after rollback
        with open(self.graph_path, "rb") as f:
            final_graph_sha = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(final_graph_sha, self.initial_graph_sha)


if __name__ == "__main__":
    unittest.main()
