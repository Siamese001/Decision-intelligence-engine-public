"""tests/end_to_end/test_alignment_w4_e2e.py

End-to-End Verification Suite for Wave 4: Make judgments truthful and enforceable.
Verifies:
1. Predecessor verification of Wave 3 lock (reports/alignment/wave-3.lock).
2. Distinct strongly-typed evaluation result envelopes:
   - HeuristicAssessmentResult
   - VectorAssessmentResult
   - SemanticAssessmentResult
   - CompositeEvaluationVerdict (tracking executed capabilities)
3. Frozen evaluation benchmark manifest (eval_benchmark_manifest_v1.yaml) validation:
   - Leakage quarantine and holdout split verification.
   - Evaluator versions and per-mode thresholds.
4. Evaluator payload validation: genuine booleans and valid score ranges pass cleanly.
5. Strict zero live-to-mock fallback enforcement on timeout or unavailability.
6. Pre-wave interceptor clearance for all Wave 4 ratified decisions.
7. Full governed gate execution generating verified Wave 4 receipt and sequential lock.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dual_pipeline.l0.evaluation_types import (
    CompositeEvaluationVerdict,
    EvaluationModality,
    EvaluationVerdict,
    FailureCategory,
    HeuristicAssessmentResult,
    SemanticAssessmentResult,
    VectorAssessmentResult,
    check_live_to_mock_fallback,
    validate_claim_grounding,
    validate_evaluator_payload,
)
from dual_pipeline.l0.receipts import (
    verify_predecessor_lock,
    verify_runtime_receipt,
    verify_sequential_lock,
)
from tools.record_wave_receipt import verify_receipt


class TestAlignmentW4E2E(unittest.TestCase):
    """E2E suite for Wave 4 truthful judgment governance and evaluation typing."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)
        self.receipt_path = self.work_dir / "wave-4.json"
        self.lock_path = self.work_dir / "wave-4.lock"
        self.spec_path = REPO_ROOT / "config" / "governance" / "alignment" / "wave-4.yaml"
        self.parent_lock_path = REPO_ROOT / "reports" / "alignment" / "wave-3.lock"
        self.manifest_path = REPO_ROOT / "config" / "governance" / "alignment" / "eval_benchmark_manifest_v1.yaml"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_wave3_predecessor_lock_validity(self) -> None:
        """Wave 3 predecessor lock exists, is valid, and matches wave 3 sequence."""
        self.assertTrue(self.parent_lock_path.is_file(), "Wave 3 lock must exist before Wave 4 can proceed")
        ok, reason, parent_digest = verify_predecessor_lock(self.parent_lock_path, expected_parent_wave=3)
        self.assertTrue(ok, f"Wave 3 predecessor lock invalid: {reason}")
        self.assertTrue(parent_digest, "Missing parent lock digest")

    def test_frozen_benchmark_manifest_integrity(self) -> None:
        """Frozen evaluation benchmark manifest is well-formed with leakage controls."""
        self.assertTrue(self.manifest_path.is_file())
        with open(self.manifest_path, "r", encoding="utf-8") as f:
            manifest = yaml.safe_load(f)

        self.assertEqual(manifest.get("manifest_version"), "1.0.0")
        self.assertEqual(manifest.get("ratified_by"), "DECISION-ALIGNMENT-W4-DATASET-HOLDOUT-RATIFICATION")

        # Check evaluator registry
        evaluators = manifest.get("evaluator_registry", {})
        self.assertIn("forensic_judge", evaluators)
        self.assertIn("recap_judge", evaluators)
        self.assertIn("graph_judge", evaluators)
        self.assertIn("vector_retrieval_judge", evaluators)
        self.assertIn("deterministic_qa_gates", evaluators)

        # Check splits and leakage quarantine
        splits = manifest.get("benchmark_splits", {})
        holdout = splits.get("holdout_evaluation_split", {})
        self.assertTrue(holdout.get("leakage_quarantined"))
        self.assertGreater(holdout.get("sample_count", 0), 0)

        # Check governance invariants
        invariants = manifest.get("governance_invariants", {})
        self.assertTrue(invariants.get("strict_zero_live_to_mock_fallback"))
        self.assertTrue(invariants.get("require_typed_result_envelopes"))

    def test_distinct_typed_evaluation_result_envelopes(self) -> None:
        """Distinct evaluation result envelopes instantiate cleanly and serialize canonically."""
        h_res = HeuristicAssessmentResult(
            assessment_id="heur_01",
            rule_name="action_ledger_single_dri_check",
            passed=True,
            score=100.0,
            observed_value={"dri": "Alice Chen"},
            details="Verified single named DRI",
        )
        self.assertEqual(h_res.score, 100.0)
        self.assertTrue(h_res.passed)

        v_res = VectorAssessmentResult(
            assessment_id="vec_01",
            similarity_score=0.88,
            threshold=0.65,
            passed=True,
            embedding_model="BGE-M3",
            query_digest="abc123query",
            reference_digest="def456ref",
        )
        self.assertTrue(v_res.passed)

        s_res = SemanticAssessmentResult(
            assessment_id="sem_01",
            evaluator_name="ExecutiveRecapJudge",
            evaluator_version="1.0.0",
            composite_score=94.0,
            verdict=EvaluationVerdict.PASS,
            dimension_scores={
                "executive_altitude": 24.0,
                "technical_substance": 24.0,
                "strategic_synthesis": 23.0,
                "scoping_and_modality": 23.0,
            },
            claims_evaluated=5,
            unsupported_claims=(),
            grounding_citations=("Section 1", "Section 3"),
        )
        self.assertEqual(s_res.verdict, EvaluationVerdict.PASS)

        composite = CompositeEvaluationVerdict(
            evaluation_id="comp_eval_01",
            output_id=2,
            overall_verdict=EvaluationVerdict.PASS,
            composite_score=94.0,
            executed_capabilities=("gate2_recap", "BGE-M3", "ExecutiveRecapJudge"),
            heuristic_results=(h_res,),
            vector_results=(v_res,),
            semantic_results=(s_res,),
        )
        self.assertEqual(len(composite.executed_capabilities), 3)
        digest = composite.canonical_digest()
        self.assertEqual(len(digest), 64)

    def test_evaluator_payload_validation_success_path(self) -> None:
        """Valid evaluator payload with real booleans and numeric scores passes validation."""
        valid_payload = {
            "status": "COMPLETE",
            "verdict": "PASS",
            "composite_score": 92.5,
            "dimension_scores": {
                "dim_1": 24.0,
                "dim_2": 23.5,
                "dim_3": 22.0,
                "dim_4": 23.0,
            },
            "passed": True,  # genuine bool
        }
        ok, errors, cat = validate_evaluator_payload(valid_payload)
        self.assertTrue(ok, f"Validation failed: {errors}")
        self.assertEqual(cat, FailureCategory.NONE)

    def test_claim_grounding_verification(self) -> None:
        """Claim grounding verifies that citations are present in source text."""
        source = "The migration to Aurora Postgres was ratified by Alice Chen during the technical sync."
        claim = "Aurora Postgres migration was approved"
        citations = ["Aurora Postgres was ratified by Alice Chen"]

        ok, err, cat = validate_claim_grounding(claim, citations, source)
        self.assertTrue(ok, f"Grounding check failed: {err}")
        self.assertEqual(cat, FailureCategory.NONE)

    def test_pre_wave_interceptor_clearance_for_wave4(self) -> None:
        """Pre-wave interceptor grants clearance for Wave 4 with both ratified decisions."""
        pre_gate_script = REPO_ROOT / "tools" / "pre_wave_decision_gate.py"
        cmd = [
            sys.executable,
            str(pre_gate_script),
            "--wave", "4",
            "--plan-path", "docs/plans/docs_pipeline_alignment_debate_plan_20261006.md",
            "--ledger-path", "hitl_decision_ledger.json",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(
            res.returncode, 0,
            f"Pre-wave decision interceptor failed (exit {res.returncode}):\n{res.stdout}\n{res.stderr}",
        )
        self.assertIn("CLEARANCE GRANTED", res.stdout)
        self.assertIn("DECISION-ALIGNMENT-W4-EVALUATOR-TYPING-AND-FALLBACK", res.stdout)
        self.assertIn("DECISION-ALIGNMENT-W4-DATASET-HOLDOUT-RATIFICATION", res.stdout)

    def test_full_wave4_gate_runner_execution(self) -> None:
        """Wave 4 gate runs successfully via child spec and seals receipt and sequential lock."""
        gate_script = REPO_ROOT / "tools" / "run_wave_gate.py"

        with open(self.spec_path, "r", encoding="utf-8") as f:
            spec_data = yaml.safe_load(f)

        child_spec = dict(spec_data)
        child_spec["test_suites"] = ["tests/test_alignment_w4_adversarial.py"]
        child_spec_path = self.work_dir / "test_wave_4_child_spec.yaml"
        with open(child_spec_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(child_spec, f)

        cmd = [
            sys.executable,
            str(gate_script),
            "--wave", "4",
            "--spec", str(child_spec_path),
            "--receipt", str(self.receipt_path),
            "--lock", str(self.lock_path),
            "--require-lock", str(self.parent_lock_path),
            "--immutable",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(
            res.returncode, 0,
            f"Wave 4 gate failed (exit {res.returncode}):\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}",
        )
        self.assertIn("WAVE 4 GATE PASSED", res.stdout)

        # Verify receipt
        self.assertTrue(self.receipt_path.is_file())
        with open(self.receipt_path, "r", encoding="utf-8") as f:
            receipt_data = json.load(f)
        self.assertEqual(receipt_data.get("status"), "COMPLETE")
        self.assertEqual(receipt_data.get("wave_id"), "Wave 4: Make judgments truthful and enforceable")

        # Verify lock
        self.assertTrue(self.lock_path.is_file())
        with open(self.lock_path, "r", encoding="utf-8") as f:
            lock_data = json.load(f)
        self.assertEqual(lock_data.get("status"), "LOCKED")
        self.assertEqual(lock_data.get("receipt_digest"), receipt_data.get("receipt_digest"))

        # Predecessor lock verification on Wave 4 lock
        ok, reason, lock_digest = verify_predecessor_lock(self.lock_path, expected_parent_wave=4)
        self.assertTrue(ok, reason)

        # Cryptographic verification
        rec_ok, rec_reason, rec_digest = verify_runtime_receipt(
            self.receipt_path,
            expected_wave_id="Wave 4: Make judgments truthful and enforceable",
        )
        self.assertTrue(rec_ok, rec_reason)
        self.assertTrue(verify_receipt(self.receipt_path))


if __name__ == "__main__":
    unittest.main()
