"""tests/test_hitl_formal_loop_e2e.py — Comprehensive end-to-end test of the complete closed loop."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.calibration import create_signed_calibration_approval_receipt
from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import utc_now
from dual_pipeline.hitl_governance_core.ledger import save_hitl_records
from dual_pipeline.hitl_ui_bridge import AntigravityUIBridge
from dual_pipeline.learning_engine import ClosedLoopLearningEngine
from dual_pipeline.parameters import ParameterRegistry
from dual_pipeline.telemetry_core.recorder import RunRecorder, verify_run_l4_seal
from dual_pipeline.uwg_core.gateway import UniversalWriteGateway


class TestHitlFormalLoopE2E(unittest.TestCase):
    """Proves the full closed-loop system learning cycle with Universal Write Gateway commit and L4 seal."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_hitl_formal_loop_")
        self.root = Path(self.temp_dir)
        self.ledger_dir = self.root / ".local" / "ledger"
        self.ledger_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.ledger_dir / "hitl_decision_ledger.json"
        self.proposals_dir = self.root / "proposals"
        self.proposals_dir.mkdir(parents=True, exist_ok=True)
        self.config_dir = self.root / "config" / "runtime"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.config_dir / "parameters.json"

        initial_config = {
            "schema_version": "1.0.0",
            "updated_at": utc_now(),
            "parameters": {
                "hitl": {
                    "bayesian_prior_strength": 4.0,
                    "human_feedback_weight": 1.0,
                    "autonomous_feedback_weight": 0.05,
                    "ambiguity_margin_threshold": 0.15,
                }
            },
        }
        self.config_path.write_text(json.dumps(initial_config, indent=2), encoding="utf-8")

        self.engine = ClosedLoopLearningEngine(runtime_root=self.root, proposals_dir=self.proposals_dir)
        self.uwg = UniversalWriteGateway(self.root, self.root / "seed.sqlite")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_complete_closed_loop_learning_and_uwg_commit(self) -> None:
        # Phase 1: Ingest HITL decisions with repeated category overrides
        category = "speaker_diarization_attribution"
        decisions = []
        for i in range(12):
            outcome = "OVERRIDDEN" if i < 8 else "AFFIRMED"
            decisions.append({
                "record_id": f"dec-loop-{i:03d}",
                "run_id": "run-hitl-source-loop",
                "question": f"Attribute speaker for turn {i}?",
                "chosen_option": "Operator override" if outcome == "OVERRIDDEN" else "Autonomous",
                "category": category,
                "confidence": 0.85,
                "outcome_type": outcome,
                "created_at": utc_now(),
            })
        save_hitl_records(self.ledger_path, decisions)

        # Phase 2: Ingest into learning engine & cluster cohorts
        ingested = self.engine.ingest_hitl_decisions(self.ledger_path)
        self.assertEqual(len(ingested), 12)
        cohorts = self.engine.analyze_failure_cohorts(ingested, min_occurrences=5)
        self.assertTrue(any(c["cohort_key"] == f"hitl_override:{category}" for c in cohorts))
        target_cohort = next(c for c in cohorts if c["cohort_key"] == f"hitl_override:{category}")

        # Phase 3: Synthesize proposal
        proposal = self.engine.generate_proposal_for_cohort(
            target_cohort,
            proposal_type="hitl_confidence",
            target_override={"category": category},
        )
        validate_artifact(proposal)
        self.assertEqual(proposal["status"], "proposed")
        self.assertTrue(proposal["approval_required"])

        # Phase 4: Dry-run simulation gate
        sim_result = self.engine.simulate(proposal)
        self.assertTrue(sim_result.success)
        self.assertLess(sim_result.metrics["brier_score_delta"], 0)

        # Telemetry & L4 Sealing of the learning cycle run
        run_id = f"run-learning-{proposal['proposal_id']}"
        recorder = RunRecorder(
            runtime_root=self.root,
            run_id=run_id,
            trace_id="b" * 32,
            use_case="learning",
            graph_snapshot=None,
        )
        corr = {
            "source_id": None,
            "candidate_id": None,
            "query_id": None,
            "graph_operation_id": None,
            "hitl_item_id": f"item-{proposal['proposal_id']}",
        }
        for stg in ("LP0", "LP1", "LP2", "LP3", "LP4"):
            recorder.record(
                stage=stg,
                event_type="pipeline_event",
                payload={"proposal_id": proposal["proposal_id"], "stage": stg},
                correlation=corr,
                status="PASS",
                record_count=1,
            )
        seal_result = recorder.seal(
            required_stages=["LP0", "LP1", "LP2", "LP3", "LP4", "L4"],
            correlation=corr,
        )
        recorder.close()
        self.assertEqual(seal_result["status"], "PASS")

        # Independent L4 seal verification
        l4_verification = verify_run_l4_seal(self.root, run_id)
        self.assertEqual(l4_verification["status"], "PASS")
        self.assertTrue(l4_verification["seal_verified"])

        # Phase 5: Cryptographic approval issuance
        approval_receipt = create_signed_calibration_approval_receipt(
            proposal=proposal,
            approved_by="operator-hitl-lead",
            rationale="Approved high-confidence Bayesian tuning based on simulated Brier reduction",
            origin="USER_EXPLICIT",
        )

        # Phase 6: Universal Write Gateway learning_parameter_commit lane
        commit_res = self.uwg.process_commit_request({
            "request_type": "learning_parameter_commit",
            "proposal": proposal,
            "approval_receipt": approval_receipt,
            "config_path": str(self.config_path),
            "proposals_dir": str(self.proposals_dir),
        })
        self.assertEqual(commit_res["status"], "APPLIED")
        self.assertEqual(commit_res["graph_delta_counts"]["total_delta"], 0)

        # Validate generated commit receipt artifact
        apply_receipt = commit_res["receipt"]
        validate_artifact(apply_receipt)
        self.assertEqual(apply_receipt["status"], "APPLIED")

        # Phase 7: Live Bridge verification with newly committed parameters
        reg = ParameterRegistry(config_path=self.config_path)
        reg.clear_cache()
        updated_prior = reg.get_float("hitl.bayesian_prior_strength")
        self.assertEqual(updated_prior, 8.0)

        # Bridge evaluates decision using adapted parameter
        bridge = AntigravityUIBridge(
            ledger_path=self.ledger_path,
            margin_threshold=0.15,
        )
        test_options = [
            {"label": "Option A", "confidence": 0.82},
            {"label": "Option B", "confidence": 0.70},
        ]
        decision = bridge.arbitrate_and_present(
            pipeline_id="pipeline-test",
            decision_id="dec-test-post-commit",
            question="Attribute speaker?",
            options=test_options,
            category=category,
        )
        self.assertIsNotNone(decision)

        # Phase 8: Rollback via UWG
        rollback_res = self.uwg.process_commit_request({
            "request_type": "learning_parameter_rollback",
            "rollback_path": commit_res["rollback_receipt_path"],
        })
        self.assertEqual(rollback_res["status"], "ROLLED_BACK")

        # Parameters successfully restored
        reg.clear_cache()
        self.assertEqual(reg.get_float("hitl.bayesian_prior_strength"), 4.0)


if __name__ == "__main__":
    unittest.main()
