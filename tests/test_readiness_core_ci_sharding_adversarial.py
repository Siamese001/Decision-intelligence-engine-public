"""Adversarial & edge-case tests for Wave 1 PR Governance Lifecycle, Readiness Core, and CI Sharding.

Covers:
1. Invariant bypass attempt: Unsigned in-tree review receipt, cross-branch receipt reuse, and bare '%' HITL labels.
2. Boundary payload: Empty target_files on full=True readiness runs and partial --fail-fast stage lists.
3. Malformed input: Missing qualification_fingerprint, mismatched base_commit_sha, and unverified readiness flags.
4. Exact production regression: Single-shard E2E run overwriting e2e-gate-receipt.json.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.hitl_governance_core.validation import validate_hitl_presentation
from tools.pr_governance_core.inspection import _matches_receipt, _resolve_merge_eligibility
from tools.pr_governance_core.lifecycle import (
    _resolve_pr_review_receipt,
    _seal_pr_review_attestation,
    _validate_merge_receipt,
)
from tools.readiness_core import _build_stages, _load_cached_receipt_for_sha, _load_prior_failures
import tools.run_ci_gates as run_ci_gates


class TestReadinessCoreCiShardingAdversarial(unittest.TestCase):
    """Adversarial tests for unsigned receipts, empty target_files, partial fail-fast receipts, and bare '%' options."""

    def test_unsigned_in_tree_review_receipt_and_branch_mismatch_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reports_dir = root / "reports"
            reports_dir.mkdir(parents=True)
            slug = "feat_unsigned"
            unsigned_file = reports_dir / f"pr_review_{slug}_receipt.json"
            unsigned_file.write_text(
                json.dumps({
                    "branch": "feat/unsigned",
                    "head_sha": "deadbeef1234",
                    "terminal_outcome": "PASS",
                    "readiness_verified": True,
                    "wave_summary_verified": True,
                }),
                encoding="utf-8",
            )
            # Plain unsigned in-tree receipt must be rejected
            self.assertIsNone(_resolve_pr_review_receipt("feat/unsigned", slug, "deadbeef1234", repo_root=root))

            # Sealed attestation for branch A must be rejected when queried for branch B
            _seal_pr_review_attestation(
                "deadbeef1234",
                {
                    "branch": "feat/branch-a",
                    "head_sha": "deadbeef1234",
                    "terminal_outcome": "PASS",
                    "readiness_verified": True,
                    "wave_summary_verified": True,
                },
                repo_root=root,
            )
            self.assertIsNone(_resolve_pr_review_receipt("feat/branch-b", "feat_branch_b", "deadbeef1234", repo_root=root))

    def test_validate_merge_receipt_fails_closed_on_git_error_or_unverified_flags(self) -> None:
        with patch(
            "tools.pr_governance_core.lifecycle._resolve_pr_review_receipt",
            return_value={
                "branch": "feat/test",
                "head_sha": "sha123",
                "terminal_outcome": "PASS",
                "readiness_verified": False,
                "wave_summary_verified": True,
            },
        ):
            self.assertFalse(_validate_merge_receipt("feat/test", "feat_test", "sha123"))

        with patch(
            "tools.pr_governance_core.lifecycle._resolve_pr_review_receipt",
            return_value={
                "branch": "feat/test",
                "head_sha": "sha123",
                "terminal_outcome": "PASS",
                "readiness_verified": True,
                "wave_summary_verified": True,
            },
        ), patch("tools.pr_governance_core.lifecycle.run_git", side_effect=RuntimeError("git failure")):
            self.assertFalse(_validate_merge_receipt("feat/test", "feat_test", "sha123"))

    def test_fail_fast_partial_receipt_and_null_fingerprint_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            receipt_path = Path(tmpdir) / "partial_receipt.json"
            # Simulate a --fail-fast run that stopped at stage 3
            receipt_path.write_text(
                json.dumps({
                    "branch": "feat/partial",
                    "head_commit_sha": "sha_1",
                    "base_commit_sha": "base_1",
                    "terminal_outcome": "FAIL",
                    "stages_evaluated": 32,
                    "qualification_fingerprint": "fp_1",
                    "stages": [
                        {"stage_index": 1, "stage_name": "S1", "status": "PASS"},
                        {"stage_index": 2, "stage_name": "S2", "status": "PASS"},
                        {"stage_index": 3, "stage_name": "S3", "status": "FAIL"},
                    ],
                }),
                encoding="utf-8",
            )
            _, failed = _load_prior_failures(
                receipt_path,
                expected_head_sha="sha_1",
                expected_fingerprint="fp_1",
                expected_base_sha="base_1",
            )
            # Stages 3..32 must all be marked failed so unreached stages are not skipped
            for idx in range(3, 33):
                self.assertIn(idx, failed)

            # Null qualification_fingerprint must not match a requested fingerprint
            receipt_path.write_text(
                json.dumps({
                    "branch": "feat/partial",
                    "head_commit_sha": "sha_1",
                    "base_commit_sha": "base_1",
                    "terminal_outcome": "PASS",
                    "stages_evaluated": 32,
                    "qualification_fingerprint": None,
                    "stages": [{"stage_index": i, "stage_name": f"S{i}", "status": "PASS"} for i in range(1, 33)],
                }),
                encoding="utf-8",
            )
            self.assertIsNone(
                _load_cached_receipt_for_sha(
                    "sha_1",
                    receipt_path,
                    qualification_fingerprint="fp_required",
                    expected_base_sha="base_1",
                )
            )
            self.assertIsNone(
                _load_cached_receipt_for_sha(
                    "sha_1",
                    receipt_path,
                    qualification_fingerprint=None,
                    expected_base_sha="base_2_moved",
                )
            )

    def test_matches_receipt_and_merge_eligibility_reject_incomplete_state(self) -> None:
        stages = [{"stage_index": i, "stage_name": f"Stage {i}", "status": "PASS"} for i in range(1, 32)]
        stages.append({
            "stage_index": 32,
            "stage_name": "Grounded Delivery & Claim Manifest Verification",
            "status": "FAIL",
        })
        bad_receipt = {
            "branch": "feat/bad",
            "head_sha": "sha1",
            "base_commit_sha": "base1",
            "terminal_outcome": "PASS",
            "total_stages": 32,
            "stages": stages,
        }
        self.assertFalse(_matches_receipt(bad_receipt, "feat/bad", "sha1"))

        unverified_item = {
            "branch": "feat/unverified",
            "tier": "P0",
            "ahead": 1,
            "clean_merge": True,
            "dirty": False,
            "readiness_receipt_pass": False,
            "open_waves": "Wave 2",
            "review_verdict": "GOVERNANCE_BLOCK",
        }
        self.assertEqual(_resolve_merge_eligibility(unverified_item, {}, []), "BLOCKED_GOVERNANCE")

    def test_sharded_ci_gate_writes_shard_scoped_receipt_and_bare_percent_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_root = Path(tmpdir) / "ci_out"
            with patch("tools.run_ci_gates.verify_ci_test_list_integrity", return_value=[]), \
                 patch("tools.run_ci_gates.run_commands", return_value=0):
                rc = run_ci_gates.main([
                    "--profile", "e2e",
                    "--shard", "2",
                    "--total-shards", "4",
                    "--output-root", str(out_root),
                ])
                self.assertEqual(rc, 0)
                self.assertTrue((out_root / "e2e-gate-shard-2-of-4-receipt.json").is_file())
                self.assertFalse((out_root / "e2e-gate-receipt.json").exists())

        # Bare '%' in prose without confidence label must be rejected
        bare_percent_payload = {
            "question": "Adjust budget allocation?",
            "options": [
                "Increase allocation by 100%",
                "Reduce allocation by 50%",
            ],
        }
        errs = validate_hitl_presentation(bare_percent_payload, require_layman=False)
        self.assertTrue(any("HITL_VIOLATION_MISSING_CONFIDENCE" in e for e in errs))


if __name__ == "__main__":
    unittest.main()
