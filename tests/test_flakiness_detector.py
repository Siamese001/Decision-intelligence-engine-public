#!/usr/bin/env python3
"""Unit test suite for automated test flakiness detection and duration benchmarking."""
from __future__ import annotations

import unittest
from pathlib import Path

from tools.check_pytest_compliance import (
    check_test_duration,
    check_test_durations_report,
)
from tools.run_flakiness_detector import (
    TestFlakinessSummary,
    _compute_summary_metrics,
    build_runtime_receipt,
    normalize_target_to_module,
    run_flakiness_assessment,
)


class SyntheticFlakyTestCase(unittest.TestCase):
    _counter: int = 0

    @classmethod
    def reset_counter(cls) -> None:
        cls._counter = 0

    def check_synthetic_flake(self) -> None:
        SyntheticFlakyTestCase._counter += 1
        if SyntheticFlakyTestCase._counter % 2 == 0:
            self.fail("Synthetic intentional intermittent failure")


class SyntheticStableTestCase(unittest.TestCase):
    def check_synthetic_pass(self) -> None:
        self.assertTrue(True)


class SyntheticAlwaysFailingTestCase(unittest.TestCase):
    def check_synthetic_always_fail(self) -> None:
        self.fail("Consistent failure across all runs")


class FlakinessDetectorUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        SyntheticFlakyTestCase.reset_counter()

    def test_detects_synthetic_flaky_test(self) -> None:
        descriptors = [(SyntheticFlakyTestCase, "check_synthetic_flake", "tests.SyntheticFlakyTestCase.check_synthetic_flake")]
        summaries = run_flakiness_assessment(descriptors, iterations=4, shuffle=False, show_progress=False)

        s = summaries["tests.SyntheticFlakyTestCase.check_synthetic_flake"]
        self.assertTrue(s.is_flaky, "Expected test to be classified as flaky")
        self.assertEqual(s.passed, 2)
        self.assertEqual(s.failed, 2)
        self.assertEqual(s.flakiness_rate, 0.5)
        self.assertEqual(s.recommendation, "QUARANTINE_FLAKY")
        self.assertTrue(len(s.failure_samples) > 0)

    def test_identifies_consistently_failing_test(self) -> None:
        descriptors = [
            (
                SyntheticAlwaysFailingTestCase,
                "check_synthetic_always_fail",
                "tests.SyntheticAlwaysFailingTestCase.check_synthetic_always_fail",
            )
        ]
        summaries = run_flakiness_assessment(descriptors, iterations=3, shuffle=False, show_progress=False)

        s = summaries["tests.SyntheticAlwaysFailingTestCase.check_synthetic_always_fail"]
        self.assertFalse(s.is_flaky, "Consistently failing test should not be classified as flaky")
        self.assertTrue(s.is_consistently_failing)
        self.assertEqual(s.passed, 0)
        self.assertEqual(s.failed, 3)
        self.assertEqual(s.recommendation, "DEFECT_CONSISTENT_FAILURE")

    def test_identifies_stable_passing_test(self) -> None:
        descriptors = [(SyntheticStableTestCase, "check_synthetic_pass", "tests.SyntheticStableTestCase.check_synthetic_pass")]
        summaries = run_flakiness_assessment(descriptors, iterations=5, shuffle=False, show_progress=False)

        s = summaries["tests.SyntheticStableTestCase.check_synthetic_pass"]
        self.assertFalse(s.is_flaky)
        self.assertFalse(s.is_consistently_failing)
        self.assertEqual(s.passed, 5)
        self.assertEqual(s.failed, 0)
        self.assertEqual(s.recommendation, "STABLE")
        self.assertEqual(s.flakiness_rate, 0.0)

    def test_detects_slow_test_duration_limit(self) -> None:
        s = TestFlakinessSummary(
            test_id="tests.test_slow_suite.test_heavy_op",
            total_runs=3,
            passed=3,
            failed=0,
            durations=[3.2, 3.5, 3.1],
        )
        _compute_summary_metrics(s, max_duration=3.0)

        self.assertTrue(s.exceeds_duration_limit)
        self.assertFalse(s.is_flaky)
        self.assertEqual(s.recommendation, "PERFORMANCE_SLOW")
        self.assertAlmostEqual(s.avg_duration, 3.2666666, places=4)

    def test_build_runtime_receipt_structure_and_digest(self) -> None:
        summaries = {
            "test_a": TestFlakinessSummary(test_id="test_a", total_runs=5, passed=5),
        }
        receipt = build_runtime_receipt(["tests/test_foo.py"], iterations=5, summaries=summaries, max_duration=3.0)

        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["wave_id"], "Wave-3")
        self.assertIn("canonical_digest", receipt)
        self.assertEqual(len(receipt["canonical_digest"]), 64)
        self.assertEqual(receipt["metrics"]["total_test_definitions"], 1)
        self.assertEqual(receipt["metrics"]["flaky_test_count"], 0)

    def test_target_normalization(self) -> None:
        root = Path("/tmp/repo")
        self.assertEqual(
            normalize_target_to_module("tests/test_governance_rules.py", root),
            "tests.test_governance_rules",
        )
        self.assertEqual(
            normalize_target_to_module("tests/test_gov.py::Suite::test_case", root),
            "tests.test_gov.Suite.test_case",
        )
        self.assertEqual(
            normalize_target_to_module("tests.test_direct_module", root),
            "tests.test_direct_module",
        )

    def test_check_pytest_compliance_duration_checks(self) -> None:
        ok, msg = check_test_duration("test_fast", 0.45, max_duration=3.0)
        self.assertTrue(ok)
        self.assertIn("DURATION_OK", msg)

        bad, fail_msg = check_test_duration("test_slow", 4.12, max_duration=3.0)
        self.assertFalse(bad)
        self.assertIn("DURATION_VIOLATION", fail_msg)
        self.assertIn("exceeding maximum allowed 3.0s", fail_msg)

        all_ok, violations = check_test_durations_report([("test_1", 0.2), ("test_2", 1.8)], max_duration=3.0)
        self.assertTrue(all_ok)
        self.assertEqual(len(violations), 0)

        all_bad, violations_bad = check_test_durations_report([("test_1", 0.2), ("test_slow", 3.9)], max_duration=3.0)
        self.assertFalse(all_bad)
        self.assertEqual(len(violations_bad), 1)


if __name__ == "__main__":
    unittest.main()
