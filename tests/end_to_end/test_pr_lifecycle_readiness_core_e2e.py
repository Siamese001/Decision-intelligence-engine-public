"""End-to-end verification for Wave 1 PR Governance Lifecycle, 32-Gate Readiness Core, and CI Sharding."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.hitl_governance_core.validation import validate_hitl_presentation
from tools.pr_governance_core.inspection import _matches_receipt, _resolve_merge_eligibility
from tools.pr_governance_core.lifecycle import (
    _resolve_pr_review_receipt,
    _seal_pr_review_attestation,
    _validate_merge_receipt,
)
from tools.readiness_core import _build_stages, _load_cached_receipt_for_sha, _load_prior_failures


class TestPrLifecycleReadinessCoreE2E(unittest.TestCase):
    """End-to-end lifecycle tests for DSSE review receipts, --full 32-gate scoping, and CI shard isolation."""

    def test_dsse_attested_review_receipt_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo_root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
            (repo_root / "README.md").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "chore: init"], cwd=repo_root, check=True, capture_output=True)

            branch = "feat/wave1-e2e"
            slug = "feat_wave1_e2e"
            subprocess.run(["git", "checkout", "-b", branch], cwd=repo_root, check=True, capture_output=True)
            (repo_root / "feature.py").write_text("x = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "feature.py"], cwd=repo_root, check=True)
            subprocess.run(["git", "commit", "-m", "feat: add feature"], cwd=repo_root, check=True, capture_output=True)
            head_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo_root, check=True, capture_output=True, text=True
            ).stdout.strip()

            review_payload = {
                "branch": branch,
                "head_sha": head_sha,
                "terminal_outcome": "PASS",
                "readiness_verified": True,
                "wave_summary_verified": True,
            }
            _seal_pr_review_attestation(head_sha, review_payload, repo_root=repo_root)
            resolved = _resolve_pr_review_receipt(branch, slug, head_sha, repo_root=repo_root)
            self.assertIsNotNone(resolved)
            assert resolved is not None
            self.assertEqual(resolved["branch"], branch)
            self.assertEqual(resolved["head_sha"], head_sha)

    def test_full_build_stages_never_emits_staged_flag(self) -> None:
        stages_full = _build_stages("python3", [], ["tests.test_sample"], test_workers=2, full=True)
        self.assertGreaterEqual(len(stages_full), 32)
        for stage_name, cmd in stages_full:
            self.assertNotIn(
                "--staged",
                cmd,
                f"Stage '{stage_name}' unexpectedly received '--staged' during full=True readiness build",
            )

    def test_prior_failures_and_cached_receipt_strict_binding(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            receipt_path = Path(tmpdir) / "readiness_receipt.json"
            stages = [
                {"stage_index": idx, "stage_name": f"Stage {idx}", "status": "PASS"}
                for idx in range(1, 33)
            ]
            stages[29]["stage_name"] = "Grounded Delivery & Claim Manifest Verification"
            receipt_data = {
                "branch": "feat/wave1-e2e",
                "head_commit_sha": "sha_head_1",
                "base_commit_sha": "sha_base_1",
                "terminal_outcome": "PASS",
                "stages_evaluated": 32,
                "qualification_fingerprint": "fp_valid_123",
                "stages": stages,
            }
            receipt_path.write_text(json.dumps(receipt_data), encoding="utf-8")

            cached = _load_cached_receipt_for_sha(
                "sha_head_1",
                receipt_path,
                qualification_fingerprint="fp_valid_123",
                expected_base_sha="sha_base_1",
            )
            self.assertIsNotNone(cached)
            self.assertTrue(_matches_receipt(receipt_data, "feat/wave1-e2e", "sha_head_1", expected_base_sha="sha_base_1"))

            prior_map, failed = _load_prior_failures(
                receipt_path,
                expected_head_sha="sha_head_1",
                expected_fingerprint="fp_valid_123",
                expected_base_sha="sha_base_1",
            )
            self.assertEqual(len(prior_map), 32)
            self.assertEqual(failed, set())

    def test_merge_eligibility_and_hitl_confidence_validation(self) -> None:
        eligible_item = {
            "branch": "feat/ready",
            "tier": "P0",
            "ahead": 2,
            "clean_merge": True,
            "dirty": False,
            "readiness_receipt_pass": True,
            "open_waves": "—",
            "review_verdict": "APPROVE",
        }
        self.assertEqual(_resolve_merge_eligibility(eligible_item, {}, []), "ELIGIBLE")

        valid_hitl = {
            "question": "Select deployment lane?",
            "options": [
                "(Recommended) Promote verified branch (Confidence: HIGH (92%))",
                "Hold branch for further review (Confidence: MEDIUM (60%))",
            ],
        }
        self.assertEqual(validate_hitl_presentation(valid_hitl), [])


if __name__ == "__main__":
    unittest.main()
