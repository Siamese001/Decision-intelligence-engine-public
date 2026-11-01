"""Tests for Live Multi-Worker Progress Reporter Plugin.

Verifies:
1. Correct detection of master process vs xdist worker process.
2. Suppression of character dot feeds at normal verbosity (<= 0).
3. Accurate event tracking (passes, failures, errors, skips).
4. Clean progress bar initialization and session teardown.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from dual_pipeline.testing.pytest_progress import (
    LiveProgressReporterPlugin,
    is_xdist_worker,
)


class TestPytestProgressReporter(unittest.TestCase):
    """Unit tests for the live progress reporter pytest plugin."""

    def test_is_xdist_worker_detection(self) -> None:
        """Verify worker detection returns False on master and True on workers."""
        mock_master_config = MagicMock()
        del mock_master_config.workerinput
        self.assertFalse(is_xdist_worker(mock_master_config))

        mock_worker_config = MagicMock()
        mock_worker_config.workerinput = {"workerid": "gw0"}
        self.assertTrue(is_xdist_worker(mock_worker_config))

    def test_plugin_silenced_on_worker(self) -> None:
        """Verify plugin deactivates itself when running inside a worker process."""
        plugin = LiveProgressReporterPlugin()
        mock_worker_config = MagicMock()
        mock_worker_config.workerinput = {"workerid": "gw1"}

        plugin.pytest_configure(mock_worker_config)
        self.assertFalse(plugin.is_active)

    def test_plugin_silenced_via_env_var(self) -> None:
        """Verify plugin deactivates itself when PYTEST_DISABLE_PROGRESS_BAR is set."""
        plugin = LiveProgressReporterPlugin()
        mock_config = MagicMock()
        del mock_config.workerinput
        mock_config.get_verbosity.return_value = 0

        with patch.dict(os.environ, {"PYTEST_DISABLE_PROGRESS_BAR": "1"}):
            plugin.pytest_configure(mock_config)
            self.assertFalse(plugin.is_active)

    def test_plugin_silenced_in_verbose_mode(self) -> None:
        """Verify plugin deactivates itself in verbose mode (-v) to avoid output interleaving."""
        plugin = LiveProgressReporterPlugin()
        mock_config = MagicMock()
        del mock_config.workerinput
        mock_config.get_verbosity.return_value = 1

        plugin.pytest_configure(mock_config)
        self.assertFalse(plugin.is_active)

    def test_dot_suppression_normal_verbosity(self) -> None:
        """Verify single-character dot feed is replaced by empty letter at verbosity <= 0."""
        plugin = LiveProgressReporterPlugin()
        mock_config = MagicMock()
        mock_config.get_verbosity.return_value = 0

        # Passed test report
        passed_rep = MagicMock()
        passed_rep.when = "call"
        passed_rep.passed = True
        passed_rep.failed = False
        passed_rep.skipped = False

        status = plugin.pytest_report_teststatus(passed_rep, mock_config)
        self.assertEqual(status, ("passed", "", "PASSED"))

        # Failed test report
        failed_rep = MagicMock()
        failed_rep.when = "call"
        failed_rep.passed = False
        failed_rep.failed = True
        failed_rep.skipped = False

        status = plugin.pytest_report_teststatus(failed_rep, mock_config)
        self.assertEqual(status, ("failed", "", "FAILED"))

    def test_event_counting_and_deduplication(self) -> None:
        """Verify accurate test outcome counting and nodeid deduplication."""
        plugin = LiveProgressReporterPlugin()
        plugin.pbar = MagicMock()
        plugin.total_tests = 3

        # Call report: pass
        rep1 = MagicMock()
        rep1.nodeid = "tests/test_sample.py::test_one"
        rep1.when = "call"
        rep1.passed = True
        rep1.failed = False
        rep1.skipped = False

        plugin.pytest_runtest_logreport(rep1)
        self.assertEqual(plugin.passed_count, 1)
        self.assertEqual(plugin.failed_count, 0)
        plugin.pbar.update.assert_called_with(1)

        # Teardown report on same test should not double-count
        rep1_teardown = MagicMock()
        rep1_teardown.nodeid = "tests/test_sample.py::test_one"
        rep1_teardown.when = "teardown"
        rep1_teardown.failed = False
        plugin.pytest_runtest_logreport(rep1_teardown)
        self.assertEqual(plugin.passed_count, 1)

        # Setup error report: error
        rep2 = MagicMock()
        rep2.nodeid = "tests/test_sample.py::test_two"
        rep2.when = "setup"
        rep2.failed = True
        rep2.skipped = False

        plugin.pytest_runtest_logreport(rep2)
        self.assertEqual(plugin.error_count, 1)

    def test_sessionfinish_closes_pbar(self) -> None:
        """Verify progress bar is cleanly closed on session finish."""
        plugin = LiveProgressReporterPlugin()
        mock_pbar = MagicMock()
        plugin.pbar = mock_pbar

        mock_session = MagicMock()
        plugin.pytest_sessionfinish(mock_session, exitstatus=0)

        mock_pbar.close.assert_called_once()
        self.assertIsNone(plugin.pbar)


if __name__ == "__main__":
    unittest.main()
