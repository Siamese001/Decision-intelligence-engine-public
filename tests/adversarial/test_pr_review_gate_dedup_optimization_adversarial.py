#!/usr/bin/env python3
"""Adversarial stress and edge-case testing for PR review gate deduplication and parallelization.

Covers >= 5 mandatory edge-case categories:
1. Boundary Payloads: Malformed/empty file lists, special characters, whitespace paths.
2. Invariant Bypass Attempts: Parallel gates cannot be bypassed by injecting failing linters.
3. Concurrency Races: Multi-threaded gate execution under rapid parallel completion.
4. Rollback & Recovery: Corrupt stage cache entries recover to live execution cleanly.
5. Production Regression Pins: Invariant 29-gate taxonomy, stage indices, and gate labels.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.readiness_concurrent import (
    _execute_parallel_linter_gates as _execute_parallel_linter_gates,
)
from tools.readiness_core import (
    _build_core_test_modules as _build_core_test_modules,
)
from tools.readiness_core import (
    _build_stages as _build_stages,
)
from tools.verify_main_readiness import verify_main_readiness


class TestPRReviewGateDedupOptimizationAdversarial(unittest.TestCase):
    """Adversarial tests for PR review gate deduplication and parallelization."""

    def test_adversarial_boundary_payloads_malformed_target_files(self) -> None:
        """Category 1: Boundary payloads - handles strange, empty, or non-python paths safely."""
        strange_targets = [
            "",
            "   ",
            "non_existent_file.py",
            "path with spaces/test.py",
            "tools/lint_some_unicode_üñîçødé.py",
            "dual_pipeline/non_existent.py",
            "///root///absolute///bad.py",
        ]
        # Must not raise exceptions or crash
        modules = _build_core_test_modules(strange_targets, full=False)
        self.assertIsInstance(modules, list)
        self.assertTrue(len(modules) > 0)
        # tools/lint_ unicode path triggers linter enrollment safely
        self.assertIn("tests.test_lint_layer_imports", modules)

    def test_adversarial_invariant_bypass_parallel_gate_failure_capture(self) -> None:
        """Category 2: Invariant bypass attempts - parallel gates cannot conceal an internal failure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)

            stages = [(f"Stage {i}", ["echo", str(i)]) for i in range(1, 30)]

            # Stage 7 (e.g. Epistemic Boundaries) fails in parallel
            def mock_run_stage(name: str, cmd: list[str], *args, **kwargs) -> tuple[bool, str, float]:
                if name == "Stage 7":
                    return False, "Violation: Epistemic boundary breach detected", 0.05
                return True, "Passed", 0.01

            with patch("tools.verify_main_readiness.ROOT", tmp_root), \
                 patch("tools.verify_main_readiness._core_build_stages", return_value=stages), \
                 patch("tools.verify_main_readiness.run_stage", side_effect=mock_run_stage):
                code = verify_main_readiness(
                    parallel_gates=True,
                    max_gate_workers=6,
                    explicit_slug="test_parallel_failure_capture",
                    use_cache=False,
                )
                self.assertEqual(code, 1, "Parallel gate failure must block readiness verification")

                receipt_file = tmp_root / "reports" / "readiness" / "local_main_readiness_test_parallel_failure_capture_receipt.json"
                self.assertTrue(receipt_file.is_file())
                data = receipt_file.read_text(encoding="utf-8")
                self.assertIn('"terminal_outcome": "FAIL"', data)
                self.assertIn('"status": "FAIL"', data)

    def test_adversarial_concurrency_race_condition_stress(self) -> None:
        """Category 3: Concurrency races - high-worker concurrency with varied completion latency."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)

            # 27 simulated parallel stages with varying completion times
            mock_stages = [(f"Stress Stage {i}", ["echo", str(i)]) for i in range(1, 28)]

            import random
            import time

            def mock_variable_latency_run(name: str, cmd: list[str], *args, **kwargs) -> tuple[bool, str, float]:
                delay = random.uniform(0.005, 0.02)
                time.sleep(delay)
                return True, f"Success from {name}", delay

            with patch("tools.readiness_core.ROOT", tmp_root), \
                 patch("tools.readiness_core.run_stage", side_effect=mock_variable_latency_run):
                results, passed = _execute_parallel_linter_gates(
                    stages_slice=mock_stages,
                    total_stages=27,
                    use_cache=False,
                    max_workers=12,  # Stress with 12 concurrent workers
                    head_sha="sha_stress",
                    target_files=[],
                    verbose=False,
                )

                self.assertTrue(passed)
                self.assertEqual(len(results), 27)
                # Verify that despite random execution order, results are perfectly index-aligned
                for idx, r in enumerate(results, start=1):
                    self.assertEqual(r["stage_index"], idx)
                    self.assertEqual(r["stage_name"], f"Stress Stage {idx}")
                    self.assertEqual(r["status"], "PASS")

    def test_adversarial_rollback_and_cache_corruption_recovery(self) -> None:
        """Category 4: Rollback & Recovery - corrupt cache file falls back to live stage execution."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            cache_dir = tmp_root / ".local" / "readiness_cache"
            cache_dir.mkdir(parents=True)

            mock_stages = [("Stage 1", ["echo", "1"])]

            # Mock get_cached_stage returning None (as when corrupt)
            call_count = 0
            def mock_run_stage(name: str, cmd: list[str], *args, **kwargs) -> tuple[bool, str, float]:
                nonlocal call_count
                call_count += 1
                return True, "Executed live after cache miss", 0.02

            with patch("tools.readiness_concurrent.ROOT", tmp_root), \
                 patch("tools.readiness_concurrent.get_cached_stage", return_value=None), \
                 patch("tools.readiness_concurrent.run_stage", side_effect=mock_run_stage):
                results, passed = _execute_parallel_linter_gates(
                    stages_slice=mock_stages,
                    total_stages=1,
                    use_cache=True,
                    max_workers=2,
                    head_sha="sha_corrupt",
                    target_files=[],
                    verbose=False,
                )

                self.assertTrue(passed)
                self.assertEqual(call_count, 1, "Must execute live stage when cache is missing/corrupt")

    def test_adversarial_production_regression_pins_29_gate_taxonomy(self) -> None:
        """Category 5: Production regression pins - exact 29 gates, exact names, exact positions."""
        stages = _build_stages("python", [], ["tests.test_sample"], test_workers=4)
        self.assertEqual(len(stages), 29, "Total stages must remain exactly 29")

        # Gate 1 pin
        self.assertEqual(stages[0][0], "Model-Neutral Artifact Naming")
        # Gate 28 pin
        self.assertEqual(stages[27][0], "Core Architectural & Governance Test Suite")
        # Gate 29 pin
        self.assertEqual(stages[28][0], "Gate 10: E2E Coverage Audit Score (Dimension 2 ≥ 80%)")


if __name__ == "__main__":
    unittest.main()
