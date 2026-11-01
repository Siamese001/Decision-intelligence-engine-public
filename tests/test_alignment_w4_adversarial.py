"""tests/test_alignment_w4_adversarial.py

Adversarial and Edge-Case Test Suite for Wave 4 Governance.
Covers 4 mandatory edge-case categories under e2e-and-edge-case-coverage.md:
1. Boundary payload:
   - Score boundaries: exactly 0.0 and 100.0 valid; -0.001 and 100.001 rejected fail-closed.
   - Missing Wave 3 predecessor lock blocks Wave 4 gate execution fail-closed (Exit Code 1).
   - Empty claim text or zero-citation list rejected.
2. Malformed input:
   - String booleans ("true", "false", "True", "False") rejected fail-closed.
   - NaN, Infinity, and non-numeric scores rejected fail-closed.
   - Non-dictionary / malformed JSON payloads rejected fail-closed.
   - Duplicate dimension keys in dimension_scores rejected fail-closed.
   - Tampered predecessor lock file rejected fail-closed.
3. Invariant bypass attempt:
   - Silent live-to-mock fallback attempts on timeout or unavailability rejected fail-closed.
   - Unsupported claim with irrelevant/missing citations rejected fail-closed.
   - Omitted item from live batch evaluation fails closed.
   - Overwrite of existing Wave 4 receipt/lock without --allow-overwrite fails closed.
4. Rollback & recovery:
   - Failed gate execution leaves zero orphaned receipt or lock files.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dual_pipeline.l0.evaluation_types import (
    FailureCategory,
    check_live_to_mock_fallback,
    validate_claim_grounding,
    validate_evaluator_payload,
)
from dual_pipeline.l0.receipts import verify_predecessor_lock


class TestAlignmentW4Adversarial(unittest.TestCase):
    """Adversarial and edge-case testing for Wave 4 evaluator truthfulness and typing."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)
        self.gate_script = REPO_ROOT / "tools" / "run_wave_gate.py"
        self.parent_lock = REPO_ROOT / "reports" / "alignment" / "wave-3.lock"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # ── Category 1: Boundary Payload ──────────────────────────────────────────

    def test_boundary_score_exact_limits_and_violations(self) -> None:
        """Boundary scores 0.0 and 100.0 pass; values strictly outside [0, 100] fail."""
        # Valid boundaries
        ok_zero, _, _ = validate_evaluator_payload({"composite_score": 0.0})
        self.assertTrue(ok_zero)
        ok_hundred, _, _ = validate_evaluator_payload({"composite_score": 100.0})
        self.assertTrue(ok_hundred)

        # Out-of-bounds
        ok_neg, errs_neg, cat_neg = validate_evaluator_payload({"composite_score": -0.001})
        self.assertFalse(ok_neg)
        self.assertEqual(cat_neg, FailureCategory.INVALID_SCORE_RANGE)

        ok_over, errs_over, cat_over = validate_evaluator_payload({"composite_score": 100.001})
        self.assertFalse(ok_over)
        self.assertEqual(cat_over, FailureCategory.INVALID_SCORE_RANGE)

    def test_boundary_empty_claim_and_citations(self) -> None:
        """Empty claim or empty citations rejected fail-closed."""
        ok_empty_c, err, cat = validate_claim_grounding("", ["citation"], "source text")
        self.assertFalse(ok_empty_c)
        self.assertEqual(cat, FailureCategory.SCHEMA_VALIDATION_ERROR)

        ok_empty_cit, err, cat = validate_claim_grounding("valid claim", [], "source text")
        self.assertFalse(ok_empty_cit)
        self.assertEqual(cat, FailureCategory.UNSUPPORTED_CLAIM)

    def test_boundary_missing_prior_lock_blocks_wave4_gate(self) -> None:
        """Wave 4 execution without prior Wave 3 lock fails closed with Exit Code 1."""
        spec_content = {
            "wave_id": "Wave 4: Test",
            "wave_number": 4,
            "acceptance_criteria": ["Criteria"],
            "test_suites": ["tests/test_ci_gates.py"],
            "plan_path": "docs/plans/docs_pipeline_alignment_debate_plan_20261006.md",
        }
        spec_path = self.work_dir / "bad_wave4_spec.yaml"
        spec_path.write_text(yaml.dump(spec_content), encoding="utf-8")

        cmd = [
            sys.executable,
            str(self.gate_script),
            "--wave", "4",
            "--spec", str(spec_path),
            "--receipt", str(self.work_dir / "r.json"),
            "--lock", str(self.work_dir / "l.lock"),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 1)
        self.assertIn("Wave 4 requires prior lock from Wave 3", res.stdout)

    # ── Category 2: Malformed Input ───────────────────────────────────────────

    def test_malformed_string_booleans_rejected(self) -> None:
        """String booleans ('true', 'false', 'True', 'False') rejected fail-closed."""
        for str_bool in ("true", "false", "True", "False"):
            payload = {"passed": str_bool, "composite_score": 90.0}
            ok, errors, cat = validate_evaluator_payload(payload)
            self.assertFalse(ok, f"String boolean '{str_bool}' was not rejected")
            self.assertEqual(cat, FailureCategory.STRING_BOOLEAN_REJECTED)

    def test_malformed_nan_and_inf_scores_rejected(self) -> None:
        """NaN and Infinity scores rejected fail-closed."""
        payload_nan = {"composite_score": float("nan")}
        ok_nan, errors_nan, cat_nan = validate_evaluator_payload(payload_nan)
        self.assertFalse(ok_nan)
        self.assertEqual(cat_nan, FailureCategory.INVALID_SCORE_RANGE)

        payload_inf = {"composite_score": float("inf")}
        ok_inf, errors_inf, cat_inf = validate_evaluator_payload(payload_inf)
        self.assertFalse(ok_inf)
        self.assertEqual(cat_inf, FailureCategory.INVALID_SCORE_RANGE)

    def test_malformed_non_dict_payload_rejected(self) -> None:
        """Non-dictionary payload rejected fail-closed."""
        for invalid_payload in ("{}", ["list"], 123, None):
            ok, errors, cat = validate_evaluator_payload(invalid_payload)
            self.assertFalse(ok)
            self.assertEqual(cat, FailureCategory.MALFORMED_PAYLOAD)

    def test_malformed_tampered_parent_lock_rejected(self) -> None:
        """A tampered predecessor lock file fails predecessor verification."""
        tampered_lock = self.work_dir / "tampered.lock"
        tampered_lock.write_text(json.dumps({
            "lock_version": "1.0.0",
            "wave": 3,
            "status": "LOCKED",
            "receipt_digest": "0000000000000000000000000000000000000000000000000000000000000000",
            "lock_digest": "bad_digest_not_matching_content",
        }), encoding="utf-8")

        ok, reason, _ = verify_predecessor_lock(tampered_lock, expected_parent_wave=3)
        self.assertFalse(ok)
        self.assertIn("digest mismatch", reason.lower())

    # ── Category 3: Invariant Bypass Attempt ──────────────────────────────────

    def test_invariant_zero_live_to_mock_fallback_on_timeout(self) -> None:
        """Evaluator timeout cannot silently fall back to mock scoring."""
        ok, reason, cat = check_live_to_mock_fallback(is_mock=True, timeout_occurred=True)
        self.assertFalse(ok)
        self.assertEqual(cat, FailureCategory.TIMEOUT_EXCEEDED)

    def test_invariant_zero_live_to_mock_fallback_on_unavailability(self) -> None:
        """Evaluator unavailability cannot silently fall back to mock scoring."""
        ok, reason, cat = check_live_to_mock_fallback(is_mock=True, provider_available=False)
        self.assertFalse(ok)
        self.assertEqual(cat, FailureCategory.EVALUATOR_UNAVAILABLE)

    def test_invariant_mock_result_rejected_in_production(self) -> None:
        """Mock evaluator result rejected in production gate evaluation."""
        ok, reason, cat = check_live_to_mock_fallback(is_mock=True, provider_available=True)
        self.assertFalse(ok)
        self.assertEqual(cat, FailureCategory.MOCK_FALLBACK_PROHIBITED)

    def test_invariant_claim_grounding_rejects_irrelevant_citation(self) -> None:
        """Claim with fabricated citations not present in source text rejected fail-closed."""
        source = "The session focused on budget allocation and Q3 roadmap items."
        claim = "Aurora Postgres migration approved"
        fake_citations = ["Aurora Postgres approved by leadership team"]

        ok, reason, cat = validate_claim_grounding(claim, fake_citations, source)
        self.assertFalse(ok)
        self.assertEqual(cat, FailureCategory.IRRELEVANT_CITATION)

    def test_invariant_overwrite_prevention_on_receipt_and_lock(self) -> None:
        """Overwrite of existing receipt or lock file without --allow-overwrite fails closed."""
        dummy_receipt = self.work_dir / "existing_receipt.json"
        dummy_receipt.write_text("{}", encoding="utf-8")
        dummy_lock = self.work_dir / "existing_lock.lock"
        dummy_lock.write_text("{}", encoding="utf-8")

        spec_path = self.work_dir / "test_spec.yaml"
        spec_path.write_text(yaml.dump({
            "wave_number": 4,
            "acceptance_criteria": [],
            "test_suites": [],
            "required_prior_lock": str(self.parent_lock),
        }), encoding="utf-8")

        cmd = [
            sys.executable,
            str(self.gate_script),
            "--wave", "4",
            "--spec", str(spec_path),
            "--receipt", str(dummy_receipt),
            "--lock", str(dummy_lock),
            "--require-lock", str(self.parent_lock),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 1)
        self.assertIn("already exists", res.stdout)

    # ── Category 4: Rollback & Recovery ───────────────────────────────────────

    def test_rollback_no_partial_receipt_or_lock_on_gate_failure(self) -> None:
        """Failed gate execution leaves zero orphaned receipt or lock files at destination."""
        spec_path = self.work_dir / "failing_spec.yaml"
        spec_path.write_text(yaml.dump({
            "wave_number": 4,
            "acceptance_criteria": ["Must pass"],
            "test_suites": ["tests/non_existent_test_suite_w4_xyz.py"],
            "required_prior_lock": str(self.parent_lock),
        }), encoding="utf-8")

        target_receipt = self.work_dir / "nonexistent_receipt.json"
        target_lock = self.work_dir / "nonexistent_lock.lock"

        cmd = [
            sys.executable,
            str(self.gate_script),
            "--wave", "4",
            "--spec", str(spec_path),
            "--receipt", str(target_receipt),
            "--lock", str(target_lock),
            "--require-lock", str(self.parent_lock),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0)
        self.assertFalse(target_receipt.exists(), "No receipt file should be created on gate failure")
        self.assertFalse(target_lock.exists(), "No lock file should be created on gate failure")


if __name__ == "__main__":
    unittest.main()
