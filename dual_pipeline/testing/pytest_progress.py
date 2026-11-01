"""Live Multi-Worker Progress Reporter Plugin for Pytest and Pytest-xdist.

Enforces AGENTS.md:
"Progress bars are mandatory for long tests and other runs:
 Long-running test suites (>3s or multi-module), evaluation gates, CI profiles, batch
 operations, and pipeline executions must provide continuous visual progress tracking
 (tqdm progress bars with current/total counts, percentage, elapsed time, ETA, and live
 pass/fail/error/skip metrics). Silent execution or uninformative dot feeds are strictly
 forbidden."
"""
from __future__ import annotations

import sys
from typing import Optional, Set

import pytest

from config.credentials import CredentialManager

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None  # type: ignore[assignment]


def is_xdist_worker(config: pytest.Config) -> bool:
    """Returns True if currently executing inside a spawned pytest-xdist worker process."""
    return getattr(config, "workerinput", None) is not None


class LiveProgressReporterPlugin:
    """Pytest plugin providing real-time visual progress tracking across single and multi-worker runs."""

    def __init__(self) -> None:
        self.pbar: Optional[tqdm] = None
        self.total_tests: int = 0
        self.passed_count: int = 0
        self.failed_count: int = 0
        self.error_count: int = 0
        self.skipped_count: int = 0
        self.completed_nodeids: Set[str] = set()
        self.is_active: bool = True

    def pytest_configure(self, config: pytest.Config) -> None:
        """Initialize plugin configuration and silence worker instances."""
        if is_xdist_worker(config):
            self.is_active = False
            return

        # Disable in verbose mode (-v) where users requested line-by-line reporting
        if config.get_verbosity(0) > 0:
            self.is_active = False
            return

        # Disable in quiet mode or if explicitly disabled
        if CredentialManager.get_credential("PYTEST_DISABLE_PROGRESS_BAR", "").lower() in ("1", "true", "yes"):
            self.is_active = False
            return

    @pytest.hookimpl(trylast=True)
    def pytest_sessionstart(self, session: pytest.Session) -> None:
        """Suppress default terminal reporter character dots and trailing progress percentages."""
        if not self.is_active:
            return

        tr = session.config.pluginmanager.get_plugin("terminalreporter")
        if tr is not None:
            # Disable terminalreporter's built-in progress string to prevent line overwrites
            tr._show_progress_info = False
            if session.config.get_verbosity(0) <= 0:
                tr.showfspath = False

    @pytest.hookimpl(tryfirst=True)
    def pytest_report_teststatus(self, report: pytest.TestReport, config: pytest.Config) -> Optional[tuple[str, str, str]]:
        """
        Suppresses the single-character dot feed ('.', 'F', 's') at standard verbosity,
        allowing the tqdm progress bar to own the terminal line exclusively.
        """
        if not self.is_active or tqdm is None:
            return None

        # Only suppress character feed in normal or quiet mode (verbosity <= 0)
        if config.get_verbosity(0) <= 0:
            if report.when == "call":
                if report.passed:
                    return ("passed", "", "PASSED")
                if report.failed:
                    return ("failed", "", "FAILED")
                if report.skipped:
                    return ("skipped", "", "SKIPPED")
            elif report.when == "setup":
                if report.failed:
                    return ("error", "", "ERROR")
                if report.skipped:
                    return ("skipped", "", "SKIPPED")
        return None

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        """Captured collected test count for sequential test sessions."""
        if not self.is_active:
            return

        collected = len(session.items)
        if collected > 0 and self.pbar is None:
            self.total_tests = collected
            self._init_progress_bar(session.config)

    def pytest_xdist_node_collection_finished(self, node: object, ids: list[str]) -> None:
        """Capture collected test count for distributed multi-worker sessions."""
        if not self.is_active:
            return

        if self.total_tests == 0 and len(ids) > 0:
            self.total_tests = len(ids)
            config = getattr(node, "config", None)
            self._init_progress_bar(config)

    def _init_progress_bar(self, config: Optional[pytest.Config]) -> None:
        """Initialize the unified tqdm progress bar on the master process."""
        if tqdm is None or self.pbar is not None or self.total_tests <= 0:
            return

        num_workers = 0
        if config is not None:
            raw_workers = getattr(config.option, "numprocesses", None)
            if raw_workers is not None:
                try:
                    num_workers = int(raw_workers)
                except (ValueError, TypeError):
                    num_workers = 0

        worker_desc = f" (Parallel: {num_workers} workers)" if num_workers > 0 else ""
        is_tty = sys.stderr.isatty() if hasattr(sys.stderr, "isatty") else False

        self.pbar = tqdm(
            total=self.total_tests,
            desc=f"Running Tests{worker_desc}",
            unit="test",
            file=sys.stderr,
            dynamic_ncols=is_tty,
            ncols=80 if not is_tty else None,
            mininterval=0.1 if not is_tty else 0.05,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}{postfix}]",
        )

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        """Track test completion events across all workers and update the live progress bar."""
        if not self.is_active or self.pbar is None:
            return

        nodeid = getattr(report, "nodeid", "")
        if not nodeid or nodeid in self.completed_nodeids:
            # Check for teardown failure on already-completed test
            if report.when == "teardown" and report.failed:
                self.error_count += 1
                self._update_postfix()
            return

        if report.when == "call":
            self.completed_nodeids.add(nodeid)
            if report.passed:
                self.passed_count += 1
            elif report.failed:
                self.failed_count += 1
            elif report.skipped:
                self.skipped_count += 1

            short_name = nodeid.split("::")[-1][:24]
            self.pbar.set_description(f"Running {short_name}")
            self.pbar.update(1)
            self._update_postfix()

        elif report.when == "setup" and (report.failed or report.skipped):
            self.completed_nodeids.add(nodeid)
            if report.failed:
                self.error_count += 1
            else:
                self.skipped_count += 1

            short_name = nodeid.split("::")[-1][:24]
            self.pbar.set_description(f"Running {short_name}")
            self.pbar.update(1)
            self._update_postfix()

    def _update_postfix(self) -> None:
        """Update live status counts in progress bar postfix."""
        if self.pbar is None:
            return

        metrics = {
            "passed": self.passed_count,
            "failed": self.failed_count,
        }
        if self.error_count > 0:
            metrics["errors"] = self.error_count
        if self.skipped_count > 0:
            metrics["skipped"] = self.skipped_count

        self.pbar.set_postfix(metrics)

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        """Cleanly close progress bar and flush streams upon session completion."""
        if self.pbar is not None:
            self.pbar.close()
            self.pbar = None
            sys.stderr.flush()
            sys.stdout.flush()


# Pytest entrypoint / hook registration
def pytest_configure(config: pytest.Config) -> None:
    """Registers the LiveProgressReporterPlugin into pytest's pluginmanager."""
    plugin = LiveProgressReporterPlugin()
    config.pluginmanager.register(plugin, name="live_progress_reporter")
