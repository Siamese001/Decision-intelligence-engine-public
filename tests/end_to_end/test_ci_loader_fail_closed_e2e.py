"""End-to-end tests for CI test loader fail-closed behavior and slash normalization."""

from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from tools.run_ci_gates import ROOT, verify_ci_test_list_integrity
from tools.run_tests_with_progress import ProgressBarTestRunner, build_suite


class CILoaderFailClosedE2ETests(unittest.TestCase):
    """Verify that test suite loader normalizes paths and fails closed on unimportable modules."""

    def test_build_suite_normalizes_slashes_and_extensions(self) -> None:
        """Slash-separated or .py paths are normalized to dotted package paths."""
        suite = build_suite(["tests/test_truth_invariant.py"])
        self.assertGreater(suite.countTestCases(), 0)

        suite_dot = build_suite(["tests.test_truth_invariant"])
        self.assertGreater(suite_dot.countTestCases(), 0)
        self.assertEqual(suite.countTestCases(), suite_dot.countTestCases())

    def test_build_suite_fails_closed_on_nonexistent_module(self) -> None:
        """Unloadable test modules inject a synthetic test case that fails when run."""
        nonexistent = "tests.end_to_end.test_completely_fictional_module_12345"
        suite = build_suite([nonexistent])
        self.assertGreater(suite.countTestCases(), 0)

        # Run the suite and ensure it fails
        runner = unittest.TextTestRunner(stream=io.StringIO())
        result = runner.run(suite)
        self.assertFalse(result.wasSuccessful())
        self.assertGreater(len(result.errors) + len(result.failures), 0)

    def test_progress_bar_runner_reports_load_failure(self) -> None:
        """ProgressBarTestRunner returns unsuccessful when loading an invalid module."""
        nonexistent = "tests.adversarial.test_phantom_broken_test_module"
        suite = build_suite([nonexistent])

        runner = ProgressBarTestRunner(verbosity=0)
        result = runner.run(suite)
        self.assertFalse(result.wasSuccessful())

    def test_verify_ci_test_list_integrity_clean_on_repo(self) -> None:
        """Active repository test manifests contain zero missing or slashed test modules."""
        errors = verify_ci_test_list_integrity(ROOT)
        self.assertEqual(errors, [], f"Active CI test lists contain integrity defects: {errors}")

    def test_verify_ci_test_list_integrity_detects_slashes_and_missing(self) -> None:
        """Integrity verifier catches slashes, .py suffixes, and missing files."""
        with patch(
            "tools.run_ci_gates.DETERMINISTIC_TESTS",
            ("tests/broken/slash", "tests.fake.nonexistent", "tests.test_truth_invariant.py"),
        ):
            errors = verify_ci_test_list_integrity(ROOT)
            self.assertEqual(len(errors), 3)
            self.assertTrue(any("slash" in e for e in errors))
            self.assertTrue(any("not found on disk" in e for e in errors))
            self.assertTrue(any(".py suffix" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
