#!/usr/bin/env python3
"""End-to-End integration test suite for PR Review Gate Deduplication and Parallelization.

Validates the full lifecycle of optimized PR review qualification:
1. Gate 28 Linter Deduplication: Omits redundant linter tests on non-linter changes.
2. Linter Change Detection: Dynamically re-enrolls linter tests when linters are touched.
3. Parallel Gate Execution: Stages 1-27 execute concurrently with ordered receipt output.
4. Test Runner Worker Concurrency: Stage 28 command properly binds parallel test workers.
5. Receipt Schema Parity: Emits sealed receipts with exact 29-gate structure and metadata.
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.readiness_core import (
    _build_core_test_modules,
    _build_stages,
)
from tools.verify_main_readiness import (
    _execute_parallel_linter_gates,
    verify_main_readiness,
)


class TestPRReviewGateDedupOptimizationE2E(unittest.TestCase):
    """End-to-end tests for PR review gate deduplication and parallelization."""

    def test_e2e_gate28_linter_deduplication_lifecycle(self) -> None:
        """Verifies that linter tests are excluded when no linter changes exist, but included when changed."""
        # Case 1: Empty target files (e.g. clean branch / main) -> deduplicated!
        empty_targets: list[str] = []
        dedup_modules = _build_core_test_modules(empty_targets, full=False)
        self.assertNotIn("tests.test_lint_layer_imports", dedup_modules)
        self.assertNotIn("tests.test_lint_file_budgets_universal", dedup_modules)
        self.assertNotIn("tests.test_lint_hardcoded_parameters", dedup_modules)
        self.assertNotIn("tests.test_cognitive_complexity_linter", dedup_modules)
        # Core judge & governance tests must always be present
        self.assertIn("tests.test_runtime_evidence_claims", dedup_modules)
        self.assertIn("tests.test_career_firewall_adversarial", dedup_modules)
        self.assertIn("tests.governance.test_pr_governance", dedup_modules)

        # Case 2: Feature changes (e.g. dual_pipeline/core.py) -> linter tests remain deduplicated
        feature_targets = ["dual_pipeline/core.py", "storage/connection.py"]
        feature_modules = _build_core_test_modules(feature_targets, full=False)
        self.assertNotIn("tests.test_lint_layer_imports", feature_modules)
        self.assertNotIn("tests.test_strict_type_gate", feature_modules)

        # Case 3: Linter changes (e.g. tools/lint_layer_imports.py) -> linter tests automatically enrolled
        linter_targets = ["tools/lint_layer_imports.py"]
        linter_modules = _build_core_test_modules(linter_targets, full=False)
        self.assertIn("tests.test_lint_layer_imports", linter_modules)
        self.assertIn("tests.test_lint_file_budgets_universal", linter_modules)
        self.assertIn("tests.test_strict_type_gate", linter_modules)

        # Case 4: Full mode forces inclusion of all modules
        full_modules = _build_core_test_modules(empty_targets, full=True)
        self.assertIn("tests.test_lint_layer_imports", full_modules)
        self.assertIn("tests.test_comprehensive_transcript_e2e", full_modules)

    def test_e2e_parallel_linter_gates_execution_and_ordering(self) -> None:
        """Verifies that concurrent linter gate execution preserves stage ordering and results."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)

            mock_stages = [
                ("Stage 1", ["echo", "1"]),
                ("Stage 2", ["echo", "2"]),
                ("Stage 3", ["echo", "3"]),
            ]

            def mock_run_stage(name: str, cmd: list[str]) -> tuple[bool, str, float]:
                return True, f"Output from {name}", 0.02

            with patch("tools.verify_main_readiness.ROOT", tmp_root), \
                 patch("tools.verify_main_readiness.run_stage", side_effect=mock_run_stage):
                results, passed = _execute_parallel_linter_gates(
                    stages_slice=mock_stages,
                    total_stages=3,
                    use_cache=False,
                    max_workers=3,
                    head_sha="head123",
                    target_files=[],
                    verbose=False,
                )

                self.assertTrue(passed)
                self.assertEqual(len(results), 3)
                # Verify exact 1-indexed ordering is preserved
                self.assertEqual(results[0]["stage_index"], 1)
                self.assertEqual(results[0]["stage_name"], "Stage 1")
                self.assertEqual(results[1]["stage_index"], 2)
                self.assertEqual(results[1]["stage_name"], "Stage 2")
                self.assertEqual(results[2]["stage_index"], 3)
                self.assertEqual(results[2]["stage_name"], "Stage 3")

    def test_e2e_build_stages_worker_concurrency_binding(self) -> None:
        """Verifies that test workers count binds correctly to Gate 28 command tokens."""
        # 1. Default test workers = 4
        stages_4w = _build_stages("python", [], ["tests.test_sample", "tests.test_sample2", "tests.test_sample3", "tests.test_sample4"], test_workers=4)
        gate_cmd = stages_4w[27][1]
        self.assertIn("-j", gate_cmd)
        idx_j = gate_cmd.index("-j")
        self.assertEqual(gate_cmd[idx_j + 1], "4")

        # 2. Single worker = 1 -> omits -j flag
        stages_1w = _build_stages("python", [], ["tests.test_sample"], test_workers=1)
        gate_1w = stages_1w[27][1]
        self.assertNotIn("-j", gate_1w)

    def test_e2e_receipt_schema_parity_under_parallel_execution(self) -> None:
        """Verifies that parallel gate execution outputs a 100% contract-valid receipt."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)

            stages = [(f"Stage {i}", ["echo", str(i)]) for i in range(1, 31)]

            def mock_run_stage(name: str, cmd: list[str]) -> tuple[bool, str, float]:
                return True, "Mock passed", 0.01

            with patch("tools.verify_main_readiness.ROOT", tmp_root), \
                 patch("tools.verify_main_readiness._build_stages", return_value=stages), \
                 patch("tools.verify_main_readiness.run_stage", side_effect=mock_run_stage):
                code = verify_main_readiness(
                    parallel_gates=True,
                    max_gate_workers=4,
                    explicit_slug="test_parallel_receipt",
                    use_cache=False,
                )
                self.assertEqual(code, 0)

                receipt_file = tmp_root / "reports" / "readiness" / "local_main_readiness_test_parallel_receipt_receipt.json"
                self.assertTrue(receipt_file.is_file(), "Readiness receipt must be written")

                data = json.loads(receipt_file.read_text(encoding="utf-8"))
                self.assertEqual(data["terminal_outcome"], "PASS")
                self.assertEqual(data["stages_evaluated"], 30)
                self.assertEqual(data["stages_passed"], 30)
                self.assertEqual(data["stages_failed"], 0)
                self.assertEqual(len(data["stages"]), 30)

                for idx, stage in enumerate(data["stages"], start=1):
                    self.assertEqual(stage["stage_index"], idx)
                    self.assertEqual(stage["status"], "PASS")

    def test_unchanged_whole_receipt_retry_is_under_two_seconds(self) -> None:
        """An equivalent successful retry must avoid every gate command."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            stages = [(f"Stage {i}", ["echo", str(i)]) for i in range(1, 31)]
            with patch("tools.verify_main_readiness.ROOT", tmp_root), \
                 patch("tools.verify_main_readiness._build_stages", return_value=stages), \
                 patch(
                     "tools.verify_main_readiness.run_stage",
                     return_value=(True, "passed", 0.01),
                 ) as run_stage:
                self.assertEqual(
                    verify_main_readiness(explicit_slug="warm_retry", use_cache=True), 0
                )
                run_stage.reset_mock()
                started = time.monotonic()
                self.assertEqual(
                    verify_main_readiness(explicit_slug="warm_retry", use_cache=True), 0
                )
                elapsed = time.monotonic() - started
            run_stage.assert_not_called()
            self.assertLess(elapsed, 2.0)


if __name__ == "__main__":
    unittest.main()
