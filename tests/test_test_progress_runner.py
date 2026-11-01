#!/usr/bin/env python3
"""Tests for the progress-bar test runner."""
from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_tests_with_progress import (
    PRESET_PROFILES,
    ProgressBarTestRunner,
    build_suite,
)


class TestProgressRunnerTests(unittest.TestCase):
    def test_preset_profiles_contain_required_suites(self) -> None:
        self.assertIn("deterministic", PRESET_PROFILES)
        self.assertIn("waves-13-18", PRESET_PROFILES)
        self.assertIn("hitl", PRESET_PROFILES)
        self.assertIn("routing", PRESET_PROFILES)
        self.assertIn("tests.test_hitl_governance_enforcement", PRESET_PROFILES["hitl"])

    def test_runner_executes_passing_suite_and_records_metrics(self) -> None:
        class LocalPassTest(unittest.TestCase):
            def runTest(self) -> None:
                self.assertTrue(True)

        suite = unittest.TestSuite()
        suite.addTest(LocalPassTest())
        output_stream = io.StringIO()
        runner = ProgressBarTestRunner(verbosity=1, stream=output_stream)
        result = runner.run(suite)

        self.assertTrue(result.wasSuccessful())
        self.assertEqual(result.passed_count, 1)
        self.assertEqual(result.failed_count, 0)
        self.assertEqual(result.error_count, 0)
        output = output_stream.getvalue()
        self.assertIn("Test Execution Summary: [PASS]", output)
        self.assertIn("Total: 1", output)

    def test_runner_records_failures_and_prints_summary(self) -> None:
        class LocalFailTest(unittest.TestCase):
            def runTest(self) -> None:
                self.fail("expected dummy failure")

        suite = unittest.TestSuite()
        suite.addTest(LocalFailTest())
        output_stream = io.StringIO()
        runner = ProgressBarTestRunner(verbosity=1, stream=output_stream)
        result = runner.run(suite)

        self.assertFalse(result.wasSuccessful())
        self.assertEqual(result.failed_count, 1)
        output = output_stream.getvalue()
        self.assertIn("Test Execution Summary: [FAIL]", output)
        self.assertIn("FAILURES:", output)
        self.assertIn("expected dummy failure", output)

    def test_runner_records_skipped_tests(self) -> None:
        class LocalSkipTest(unittest.TestCase):
            @unittest.skip("expected skip")
            def runTest(self) -> None:
                pass

        suite = unittest.TestSuite()
        suite.addTest(LocalSkipTest())
        output_stream = io.StringIO()
        runner = ProgressBarTestRunner(verbosity=1, stream=output_stream)
        result = runner.run(suite)

        self.assertTrue(result.wasSuccessful())
        self.assertEqual(result.skipped_count, 1)
        output = output_stream.getvalue()
        self.assertIn("Skipped: 1", output)

    def test_build_suite_loads_profile_tests(self) -> None:
        suite = build_suite(["tests.test_hitl_governance_enforcement"])
        self.assertGreater(suite.countTestCases(), 0)

    def test_runner_timeout_arg_parsed(self) -> None:
        from tools.run_tests_with_progress import main
        code = main(["--timeout", "60", "tests.test_test_progress_runner.TestProgressRunnerTests.test_preset_profiles_contain_required_suites"])
        self.assertEqual(code, 0)

    def test_runner_times_out_hanging_test(self) -> None:
        import time
        class HangingTest(unittest.TestCase):
            def runTest(self) -> None:
                time.sleep(3)

        suite = unittest.TestSuite()
        suite.addTest(HangingTest())
        output_stream = io.StringIO()
        runner = ProgressBarTestRunner(verbosity=1, stream=output_stream, timeout=1)
        result = runner.run(suite)

        self.assertFalse(result.wasSuccessful())
        self.assertGreaterEqual(result.error_count, 1)
        output = output_stream.getvalue()
        self.assertIn("Test Execution Summary: [FAIL]", output)
        self.assertIn("timed out after 1s", output)


if __name__ == "__main__":
    unittest.main()
