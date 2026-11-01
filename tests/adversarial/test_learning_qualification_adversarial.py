"""
tests/adversarial/test_learning_qualification_adversarial.py

Adversarial and edge-case test suite for Wave 2: Require genuine replay and prove governed application.
Covers 5 mandatory categories per .agents/rules/e2e-and-edge-case-coverage.md:
1. Boundary payload (empty holdout dataset, empty/unmeasured target scores, self-certified simulation scores)
2. Malformed input (tampered replay digest, mismatched proposal ID, malformed rollback target)
3. Invariant bypass attempt (missing replay receipt, failed replay receipt, duplicate apply, synthetic approval, unsupported category)
4. Concurrency & CAS (compare-and-swap conflict rejection)
5. Rollback & recovery (absent-key restoration, partial failure rollback)
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

from dual_pipeline.calibration import create_signed_calibration_approval_receipt
from dual_pipeline.contracts import create_artifact, validate_artifact
from dual_pipeline.core import read_json, utc_now, write_json
from dual_pipeline.learning_engine import ClosedLoopLearningEngine
from dual_pipeline.learning_engine_core.provenance import (
    PROVENANCE_EXTENSION_KEY,
    build_signed_provenance,
)
from dual_pipeline.learning_loop_core import apply_answer_learning_proposal, simulate_answer_learning_proposal
from dual_pipeline.parameters import ParameterRegistry
from dual_pipeline.system_learning.holdout_replay_evaluator import HoldoutCase, HoldoutReplayEvaluator
from dual_pipeline.uwg_core.gateway import UniversalWriteGateway


class TestLearningQualificationAdversarial(unittest.TestCase):
    """Adversarial and boundary verification for Wave 2 replay qualification and governed application."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_learning_qual_adv_")
        self.root = Path(self.temp_dir)
        self.config_dir = self.root / "config" / "runtime"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.config_dir / "parameters.json"
        self.proposals_dir = self.root / "proposals"
        self.proposals_dir.mkdir(parents=True, exist_ok=True)

        initial_config = {
            "schema_version": "1.0.0",
            "updated_at": utc_now(),
            "parameters": {
                "copilot": {
                    "recency_window_seconds": 3600,
                    "max_candidates": 10,
                },
                "hitl": {
                    "bayesian_prior_strength": 4.0,
                },
            },
        }
        self.config_path.write_text(json.dumps(initial_config, indent=2), encoding="utf-8")
        self.engine = ClosedLoopLearningEngine(
            runtime_root=self.root,
            proposals_dir=self.proposals_dir,
        )
        self.uwg = UniversalWriteGateway(self.root, self.root / "seed.sqlite")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_copilot_proposal(
        self,
        p_id: str,
        target_param: str = "copilot.recency_window_seconds",
        current_val: Any = 3600,
        proposed_val: Any = 7200,
        baseline_score: float | None = 0.65,
        target_scores: Dict[str, float] | None = None,
        proposal_type: str = "copilot_context",
        target_type: str = "parameters",
    ) -> Dict[str, Any]:
        ext_dict: Dict[str, Any] = {
            "category": proposal_type,
            "target_type": target_type,
            "parameter_name": target_param,
            "current_value": current_val,
            "proposed_value": proposed_val,
        }
        if baseline_score is not None:
            ext_dict["baseline_score"] = baseline_score
        if target_scores is not None:
            ext_dict["target_scores"] = target_scores

        replay_def = {"evaluator": "simulate_answer_learning_proposal"}
        prov = build_signed_provenance(
            proposal_id=p_id,
            evidence_event_ids=["evt-adv-1"],
            run_ids=["run-adv-1"],
            proposed_change=f"Change {target_param}",
            replay_definition=replay_def,
        )

        prop = create_artifact(
            "learning-change-proposal",
            p_id,
            proposal_id=p_id,
            proposal_type=proposal_type,
            status="proposed",
            problem="Adversarial evaluation test",
            evidence_event_ids=["evt-adv-1"],
            run_ids=["run-adv-1"],
            proposed_change=f"Change {target_param}",
            evaluation_plan=["Replay evaluation plan"],
            risk="low",
            auto_apply=False,
            approval_required=True,
            created_at=utc_now(),
            extensions={
                "com.partner_advisory.nee.learning": ext_dict,
                PROVENANCE_EXTENSION_KEY: prov,
            },
        )
        validate_artifact(prop)
        return prop

    # --- CATEGORY 1: BOUNDARY PAYLOADS ---

    def test_boundary_empty_holdout_dataset_rejected(self) -> None:
        """HoldoutReplayEvaluator rejects empty evaluation datasets fail-closed."""
        evaluator = HoldoutReplayEvaluator([])
        proposal = {"proposal_id": "p-empty", "proposal_type": "alias_addition"}
        report = evaluator.evaluate_proposal(proposal)
        self.assertEqual(report.verdict, "REJECT")
        self.assertTrue(len(report.rejection_reasons) > 0)
        self.assertIn("no holdout cases", report.rejection_reasons[0].lower())

    def test_boundary_empty_or_unmeasured_target_scores_fail(self) -> None:
        """Simulation fails closed when target metric scores are empty or unmeasured."""
        prop = self._make_copilot_proposal("p-no-scores", baseline_score=None, target_scores=None)
        res = simulate_answer_learning_proposal(prop, runtime_root=self.root)
        self.assertFalse(res.success)
        self.assertEqual(res.status, "SIMULATED_FAIL")
        self.assertIn("unmeasured", res.details.get("error", "").lower())

    def test_boundary_self_certified_simulation_scores_rejected(self) -> None:
        """Proposal asserting self-certified simulation scores in extensions fails closed."""
        prop = self._make_copilot_proposal("p-self-cert", baseline_score=None, target_scores=None)
        prop["extensions"]["com.partner_advisory.nee.learning"]["self_certified_accuracy"] = 0.99
        res = simulate_answer_learning_proposal(prop, runtime_root=self.root)
        self.assertFalse(res.success)
        self.assertEqual(res.status, "SIMULATED_FAIL")

    # --- CATEGORY 2: MALFORMED INPUTS ---

    def test_malformed_tampered_proposal_digest_in_replay_receipt(self) -> None:
        """Simulation receipt with tampered proposal_digest triggers REPLAY_TAMPERED."""
        prop = self._make_copilot_proposal(
            "p-tamper-digest", target_scores={"grounding_accuracy": 0.85}
        )
        sim_res = simulate_answer_learning_proposal(prop, runtime_root=self.root)
        sim_receipt = {
            "status": "SIMULATED_PASS",
            "success": True,
            "proposal_id": "p-tamper-digest",
            "proposal_digest": "0" * 64,  # Tampered digest
            "replay_digest": sim_res.replay_digest,
        }
        approval = create_signed_calibration_approval_receipt(
            proposal=prop, approved_by="op-lead", rationale="Approval", origin="USER_EXPLICIT"
        )
        with self.assertRaises(ValueError) as ctx:
            self.uwg.process_commit_request({
                "request_type": "learning_parameter_commit",
                "proposal": prop,
                "approval_receipt": approval,
                "config_path": str(self.config_path),
                "proposals_dir": str(self.proposals_dir),
                "simulation_receipt": sim_receipt,
            })
        self.assertIn("REPLAY_TAMPERED", str(ctx.exception))

    def test_malformed_mismatched_proposal_id_in_replay_receipt(self) -> None:
        """Simulation receipt with mismatched proposal_id triggers REPLAY_TAMPERED."""
        prop = self._make_copilot_proposal(
            "p-mismatch-id", target_scores={"grounding_accuracy": 0.85}
        )
        sim_res = simulate_answer_learning_proposal(prop, runtime_root=self.root)
        sim_receipt = {
            "status": "SIMULATED_PASS",
            "success": True,
            "proposal_id": "p-other-proposal-id",
            "proposal_digest": prop["canonical_digest"]["value"],
            "replay_digest": sim_res.replay_digest,
        }
        approval = create_signed_calibration_approval_receipt(
            proposal=prop, approved_by="op-lead", rationale="Approval", origin="USER_EXPLICIT"
        )
        with self.assertRaises(ValueError) as ctx:
            self.uwg.process_commit_request({
                "request_type": "learning_parameter_commit",
                "proposal": prop,
                "approval_receipt": approval,
                "config_path": str(self.config_path),
                "proposals_dir": str(self.proposals_dir),
                "simulation_receipt": sim_receipt,
            })
        self.assertIn("REPLAY_TAMPERED", str(ctx.exception))

    def test_malformed_rollback_target_rejected(self) -> None:
        """Invalid rollback target specification raises ValueError."""
        with self.assertRaises(ValueError):
            self.uwg.process_commit_request({
                "request_type": "learning_parameter_rollback",
                "rollback_path": 12345,
            })

    # --- CATEGORY 3: INVARIANT BYPASS ATTEMPTS ---

    def test_invariant_missing_simulation_receipt_blocks_uwg(self) -> None:
        """Answer learning category requires replay simulation receipt before UWG commit."""
        prop = self._make_copilot_proposal("p-no-replay", target_scores={"grounding_accuracy": 0.85})
        approval = create_signed_calibration_approval_receipt(
            proposal=prop, approved_by="op-lead", rationale="Approval", origin="USER_EXPLICIT"
        )
        with self.assertRaises(ValueError) as ctx:
            self.uwg.process_commit_request({
                "request_type": "learning_parameter_commit",
                "proposal": prop,
                "approval_receipt": approval,
                "config_path": str(self.config_path),
                "proposals_dir": str(self.proposals_dir),
                "require_replay_receipt": True,
            })
        self.assertIn("REPLAY_RECEIPT_REQUIRED", str(ctx.exception))

    def test_invariant_failed_simulation_receipt_blocks_uwg(self) -> None:
        """Failed replay simulation cannot authorize UWG commit."""
        prop = self._make_copilot_proposal("p-failed-sim", target_scores={"grounding_accuracy": 0.85})
        approval = create_signed_calibration_approval_receipt(
            proposal=prop, approved_by="op-lead", rationale="Approval", origin="USER_EXPLICIT"
        )
        failed_sim = {
            "status": "SIMULATED_FAIL",
            "success": False,
            "proposal_id": "p-failed-sim",
            "proposal_digest": prop["canonical_digest"]["value"],
        }
        with self.assertRaises(ValueError) as ctx:
            self.uwg.process_commit_request({
                "request_type": "learning_parameter_commit",
                "proposal": prop,
                "approval_receipt": approval,
                "config_path": str(self.config_path),
                "proposals_dir": str(self.proposals_dir),
                "simulation_receipt": failed_sim,
            })
        self.assertIn("REPLAY_QUALIFICATION_FAILED", str(ctx.exception))

    def test_invariant_duplicate_apply_rejected(self) -> None:
        """Applying the same proposal twice fails closed with duplicate rejection."""
        prop = self._make_copilot_proposal("p-duplicate", target_scores={"grounding_accuracy": 0.85})
        sim_res = simulate_answer_learning_proposal(prop, runtime_root=self.root)
        sim_receipt = {
            "status": "SIMULATED_PASS",
            "success": True,
            "proposal_id": "p-duplicate",
            "proposal_digest": prop["canonical_digest"]["value"],
            "replay_digest": sim_res.replay_digest,
        }
        approval = create_signed_calibration_approval_receipt(
            proposal=prop, approved_by="op-lead", rationale="Approval", origin="USER_EXPLICIT"
        )
        commit_req = {
            "request_type": "learning_parameter_commit",
            "proposal": prop,
            "approval_receipt": approval,
            "config_path": str(self.config_path),
            "proposals_dir": str(self.proposals_dir),
            "simulation_receipt": sim_receipt,
        }
        res1 = self.uwg.process_commit_request(commit_req)
        self.assertEqual(res1["status"], "APPLIED")

        # Second apply attempt
        with self.assertRaises(RuntimeError) as ctx:
            self.uwg.process_commit_request(commit_req)
        self.assertIn("DUPLICATE_APPLY_REJECTED", str(ctx.exception))

    def test_invariant_synthetic_approval_origin_rejected(self) -> None:
        """Non-human synthetic approval origin fails closed."""
        prop = self._make_copilot_proposal("p-synth", target_scores={"grounding_accuracy": 0.85})
        synthetic_receipt = {
            "receipt_id": "receipt-synth",
            "proposal_id": "p-synth",
            "proposal_digest": prop["canonical_digest"]["value"],
            "approved_by": "Synthetic Bot",
            "approved_at": utc_now(),
            "origin": "IDE_AUTO_APPROVE",
        }
        with self.assertRaises(PermissionError) as ctx:
            self.uwg.process_commit_request({
                "request_type": "learning_parameter_commit",
                "proposal": prop,
                "approval_receipt": synthetic_receipt,
                "config_path": str(self.config_path),
                "proposals_dir": str(self.proposals_dir),
            })
        self.assertIn("non-human approval origin", str(ctx.exception))

    def test_invariant_unsupported_proposal_category_fails_closed(self) -> None:
        """Unsupported proposal categories fail closed without emitting APPLIED status."""
        prop = self._make_copilot_proposal(
            "p-unsupported",
            target_scores={"grounding_accuracy": 0.85},
            proposal_type="benchmark_case",
            target_type="benchmark_case",
        )
        approval = create_signed_calibration_approval_receipt(
            proposal=prop, approved_by="op-lead", rationale="Approval", origin="USER_EXPLICIT"
        )
        with self.assertRaises(ValueError) as ctx:
            self.engine.apply_approved_proposal(
                proposal=prop,
                approval_receipt=approval,
                parameter_path=self.config_path,
                skip_decision_clearance=True,
            )
        self.assertIn("unsupported proposal type/category", str(ctx.exception).lower())
        applied_file = self.proposals_dir / "p-unsupported_applied_receipt.json"
        self.assertFalse(applied_file.exists())

    # --- CATEGORY 4: CONCURRENCY & CAS ---

    def test_concurrency_cas_conflict_rejection(self) -> None:
        """Compare-and-swap detects concurrent parameter modifications and aborts."""
        prop = self._make_copilot_proposal(
            "p-cas-conflict",
            current_val=3600,
            proposed_val=7200,
            target_scores={"grounding_accuracy": 0.85},
        )
        sim_res = simulate_answer_learning_proposal(prop, runtime_root=self.root)
        sim_receipt = {
            "status": "SIMULATED_PASS",
            "success": True,
            "proposal_id": "p-cas-conflict",
            "proposal_digest": prop["canonical_digest"]["value"],
            "replay_digest": sim_res.replay_digest,
        }
        approval = create_signed_calibration_approval_receipt(
            proposal=prop, approved_by="op-lead", rationale="Approval", origin="USER_EXPLICIT"
        )

        # Concurrently modify parameter on disk to 5000
        reg = ParameterRegistry(config_path=self.config_path)
        reg.set("copilot.recency_window_seconds", 5000, persist=True, config_path=self.config_path)

        with self.assertRaises(ValueError) as ctx:
            self.uwg.process_commit_request({
                "request_type": "learning_parameter_commit",
                "proposal": prop,
                "approval_receipt": approval,
                "config_path": str(self.config_path),
                "proposals_dir": str(self.proposals_dir),
                "simulation_receipt": sim_receipt,
            })
        self.assertIn("CAS_CONFLICT", str(ctx.exception))

    # --- CATEGORY 5: ROLLBACK & RECOVERY ---

    def test_rollback_absent_key_restoration(self) -> None:
        """Parameters that did not exist prior to commit are cleanly pruned on rollback."""
        new_param = "copilot.experimental_telemetry_lane"
        prop = self._make_copilot_proposal(
            "p-absent-key",
            target_param=new_param,
            current_val=None,
            proposed_val=1,
            target_scores={"grounding_accuracy": 0.90},
        )
        # Ensure key is absent initially
        reg = ParameterRegistry(config_path=self.config_path)
        self.assertFalse(reg._lookup_dotted(reg._load_json(), new_param)[0])

        sim_res = simulate_answer_learning_proposal(prop, runtime_root=self.root)
        sim_receipt = {
            "status": "SIMULATED_PASS",
            "success": True,
            "proposal_id": "p-absent-key",
            "proposal_digest": prop["canonical_digest"]["value"],
            "replay_digest": sim_res.replay_digest,
        }
        approval = create_signed_calibration_approval_receipt(
            proposal=prop, approved_by="op-lead", rationale="Approval", origin="USER_EXPLICIT"
        )

        commit_res = self.uwg.process_commit_request({
            "request_type": "learning_parameter_commit",
            "proposal": prop,
            "approval_receipt": approval,
            "config_path": str(self.config_path),
            "proposals_dir": str(self.proposals_dir),
            "simulation_receipt": sim_receipt,
            "enforce_cas": False,
        })
        self.assertEqual(commit_res["status"], "APPLIED")
        self.assertIn(new_param, commit_res["receipt"]["extensions"]["com.partner_advisory.nee.learning"]["absent_parameters"])

        # Parameter is now visible on disk
        fresh_data = read_json(self.config_path)
        fresh_params = fresh_data.get("parameters", fresh_data)
        found, _ = reg._lookup_dotted(fresh_params, new_param)
        self.assertTrue(found)

        # Rollback must prune key completely
        rb_res = self.uwg.process_commit_request({
            "request_type": "learning_parameter_rollback",
            "rollback_path": commit_res["rollback_receipt_path"],
        })
        self.assertEqual(rb_res["status"], "ROLLED_BACK")
        self.assertIn(new_param, rb_res["pruned_parameters"])

        # Verify key is absent again
        fresh_data = read_json(self.config_path)
        fresh_params = fresh_data.get("parameters", fresh_data)
        found, _ = reg._lookup_dotted(fresh_params, new_param)
        self.assertFalse(found)

    def test_rollback_partial_failure_restores_pristine_state(self) -> None:
        """Mid-apply failure rolls back applied parameters and leaves prior state pristine."""
        prop = self._make_copilot_proposal(
            "p-partial-fail",
            target_param="copilot.recency_window_seconds",
            current_val=3600,
            proposed_val=7200,
            target_scores={"grounding_accuracy": 0.88},
        )
        sim_res = simulate_answer_learning_proposal(prop, runtime_root=self.root)
        sim_receipt = {
            "status": "SIMULATED_PASS",
            "success": True,
            "proposal_id": "p-partial-fail",
            "proposal_digest": prop["canonical_digest"]["value"],
            "replay_digest": sim_res.replay_digest,
        }
        approval = create_signed_calibration_approval_receipt(
            proposal=prop, approved_by="op-lead", rationale="Approval", origin="USER_EXPLICIT"
        )

        # Force failure during post-apply verification
        with patch.object(self.uwg, "_verify_active_pair_unchanged", side_effect=RuntimeError("GRAPH_MUTATION_DETECTED")):
            with self.assertRaises(RuntimeError):
                self.uwg.process_commit_request({
                    "request_type": "learning_parameter_commit",
                    "proposal": prop,
                    "approval_receipt": approval,
                    "config_path": str(self.config_path),
                    "proposals_dir": str(self.proposals_dir),
                    "simulation_receipt": sim_receipt,
                })

        # Verify parameter was reverted to pristine prior value (3600)
        reg = ParameterRegistry(config_path=self.config_path)
        self.assertEqual(reg.get_int("copilot.recency_window_seconds"), 3600)


if __name__ == "__main__":
    unittest.main()
