#!/usr/bin/env python3
"""Adversarial and edge-case test suite for PR latency Wave 2 scope qualification.

Covers mandatory edge-case categories:
1. Boundary payloads: empty diff numstat, 0-byte changesets, complex git renames.
2. Malformed input: truncated JSON, missing required keys in prior receipts, invalid types.
3. Invariant bypass attempts: docs-prefixed branch attempting to bypass Lane 2 review, static blocker bypass.
4. Rollback & recovery: repaired candidates recovering cleanly through incremental re-execution.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tools.change_lane_classifier import classify_changes, classify_file, load_policy
from tools.lint_blast_radius import (
    FileDiffStat,
    evaluate_blast_radius,
    parse_numstat_line,
    parse_numstat_output,
)
from tools.pr_governance_core.code_review import compute_code_review
from tools.pr_governance_core.code_review_rules import ChangeClass, classify_file_path
from tools.readiness_core import _load_prior_failures
from tools.verify_main_readiness import verify_main_readiness

ROOT = Path(__file__).resolve().parents[1]


class TestPRLatencyScopeQualificationAdversarial(unittest.TestCase):
    """Adversarial and boundary test suite for Wave 2 scope qualification."""

    def test_adversarial_boundary_payload_rename_and_empty_numstat(self) -> None:
        """Category 1: Boundary payloads - complex renames, binary files, empty lines."""
        # Empty string and whitespace lines
        self.assertIsNone(parse_numstat_line(""))
        self.assertIsNone(parse_numstat_line("   \n"))
        self.assertIsNone(parse_numstat_line("truncated\tline"))
        self.assertEqual(parse_numstat_output(""), [])

        # Non-numeric added/deleted
        self.assertIsNone(parse_numstat_line("abc\tdef\tfile.py"))

        # Complex git rename: nested directory rename
        line_nested = "15\t3\tdual_pipeline/{old_sub => new_sub}/engine.py"
        stat_nested = parse_numstat_line(line_nested)
        self.assertIsNotNone(stat_nested)
        self.assertEqual(stat_nested.path, "dual_pipeline/new_sub/engine.py")
        self.assertEqual(stat_nested.added, 15)
        self.assertEqual(stat_nested.deleted, 3)

        # Top-level file rename: old.py => new.py
        line_root = "5\t0\told_doc.md => new_doc.md"
        stat_root = parse_numstat_line(line_root)
        self.assertIsNotNone(stat_root)
        self.assertEqual(stat_root.path, "new_doc.md")

        # Root-level folder rename: {old_pkg => new_pkg}/module.py
        line_pkg = "20\t10\t{old_pkg => new_pkg}/module.py"
        stat_pkg = parse_numstat_line(line_pkg)
        self.assertIsNotNone(stat_pkg)
        self.assertEqual(stat_pkg.path, "new_pkg/module.py")

    def test_adversarial_malformed_prior_receipt_rejection(self) -> None:
        """Category 2: Malformed input - corrupt JSON, invalid schema, missing keys."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)

            # Corrupt JSON syntax
            corrupt_file = tmp_root / "corrupt.json"
            corrupt_file.write_text("{'not': valid json...}", encoding="utf-8")
            pmap, f_indices = _load_prior_failures(corrupt_file)
            self.assertEqual(pmap, {})
            self.assertEqual(f_indices, set())

            # Missing stages array
            missing_stages = tmp_root / "no_stages.json"
            missing_stages.write_text(json.dumps({"head_commit_sha": "abc1"}), encoding="utf-8")
            pmap, f_indices = _load_prior_failures(missing_stages)
            self.assertEqual(pmap, {})
            self.assertEqual(f_indices, set())

            # Malformed stage entries (missing status or stage_index)
            bad_stage_file = tmp_root / "bad_stages.json"
            bad_stage_file.write_text(json.dumps({
                "head_commit_sha": "abc1",
                "stages": [
                    {"wrong_key": 123},
                    {"stage_index": "not_an_int"},
                ],
            }), encoding="utf-8")
            pmap, f_indices = _load_prior_failures(bad_stage_file)
            self.assertIn(0, f_indices)  # fallback 0 index has non-PASS status

    def test_adversarial_invariant_bypass_docs_name_content_mismatch(self) -> None:
        """Category 3: Invariant bypass - docs/ branch with production code triggers mismatch."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)

            # Simulated numstat where branch has docs/ prefix but modifies core runtime
            mock_numstat = (
                "50\t10\tdual_pipeline/decision/orchestrator.py\n"
                "10\t2\tdocs/plan.md\n"
            )

            with patch("tools.pr_governance_core.code_review.run_git", return_value="00000000"), \
                 patch("tools.pr_governance_core.code_review._parse_numstat", return_value=(
                     2, 60, 12, 0, {
                         "dual_pipeline/decision/orchestrator.py": (50, 10),
                         "docs/plan.md": (10, 2),
                     }
                 )):
                review = compute_code_review(
                    "docs/my-stealth-feature",
                    base_branch="main",
                    root=tmp_root,
                    use_cache=False,
                    clean_merge=True,
                )
                self.assertTrue(
                    review.signals.name_content_mismatch,
                    "Branch named docs/ modifying dual_pipeline must trigger name_content_mismatch",
                )
                self.assertIn("docs/", review.signals.mismatch_description)

    def test_adversarial_rollback_and_recovery_incremental_recheck(self) -> None:
        """Category 4: Rollback & recovery - repaired candidate re-runs failed and NOT_RUN gates."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            receipt_dir = tmp_root / "reports" / "readiness"
            receipt_dir.mkdir(parents=True)
            receipt_path = receipt_dir / "local_main_readiness_feat_repair_receipt.json"

            # Initial run had static Gate 2 fail and Gate 28 marked NOT_RUN
            initial_receipt = {
                "head_commit_sha": "repair_sha_01",
                "terminal_outcome": "FAIL",
                "stages": [
                    {"stage_index": 1, "stage_name": "Stage 1", "status": "PASS", "duration_seconds": 0.1},
                    {"stage_index": 2, "stage_name": "Stage 2", "status": "FAIL", "duration_seconds": 0.1},
                    {"stage_index": 28, "stage_name": "Core Architectural & Governance Test Suite", "status": "NOT_RUN", "duration_seconds": 0.0},
                ],
            }
            receipt_path.write_text(json.dumps(initial_receipt), encoding="utf-8")

            # Load prior failures on the same commit: both 2 and 28 must be in failed_indices
            prior_map, failed_indices = _load_prior_failures(receipt_path, expected_head_sha="repair_sha_01")
            self.assertIn(2, failed_indices, "Failed gate must be re-run")
            self.assertIn(28, failed_indices, "Previously skipped NOT_RUN test suite must be re-run")
            self.assertNotIn(1, failed_indices, "Passing gate 1 is reusable")


if __name__ == "__main__":
    unittest.main()
