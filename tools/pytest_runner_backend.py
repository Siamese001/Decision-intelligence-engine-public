#!/usr/bin/env python3
"""Pytest execution backend with collection, in-band progress, and count reporting.

Provides a unified execution interface for running pytest suites within
tools/run_tests_with_progress.py and tools/run_output3_source_wave.py.
"""
from __future__ import annotations

import contextlib
import os
from pathlib import Path
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import pytest

ROOT = Path(__file__).resolve().parents[1]


def normalize_pytest_target(target: str, repo_root: Optional[Path] = None) -> str:
    """Normalize module dot syntax or relative file paths to pytest-compatible targets."""
    base = repo_root or ROOT
    raw = target.strip()
    if not raw:
        return raw

    # If it is already an existing path on disk, return as relative path
    direct_path = base / raw
    if direct_path.exists():
        return str(direct_path.relative_to(base) if direct_path.is_relative_to(base) else direct_path)

    # Clean leading/trailing slashes
    clean = raw.replace("\\", "/").lstrip("/")
    if clean.endswith(".py"):
        p = base / clean
        if p.exists():
            return clean

    # Handle dot-separated module or class syntax: e.g. tests.test_foo.TestClass.test_case
    parts = clean.split(".")
    for idx in range(len(parts), 0, -1):
        candidate_rel = Path(*parts[:idx]).with_suffix(".py")
        if (base / candidate_rel).is_file():
            sub_selectors = parts[idx:]
            if sub_selectors:
                return f"{candidate_rel.as_posix()}::{'::'.join(sub_selectors)}"
            return candidate_rel.as_posix()

    # Fallback: replace slashes/dots and assume .py file
    if "/" in clean or clean.startswith("tests."):
        rel = clean.replace(".", "/")
        if not rel.endswith(".py"):
            rel += ".py"
        return rel

    return clean


class PytestCollectorPlugin:
    """Pytest plugin collecting exact nodeids, execution status, and in-band progress."""

    def __init__(
        self,
        stream=None,
        progress_callback: Optional[Callable[[int, int, str, Dict[str, Any]], None]] = None,
        verbose: int = 1,
    ) -> None:
        self.stream = stream or sys.stdout
        self.progress_callback = progress_callback
        self.verbose = verbose
        self.collected_ids: List[str] = []
        self.passed: List[str] = []
        self.failed: List[Tuple[str, str]] = []
        self.errors: List[Tuple[str, str]] = []
        self.skipped: List[Tuple[str, str]] = []
        self.test_timings: List[Tuple[str, float]] = []
        self.total_tests: int = 0
        self.start_time: float = 0.0
        self._test_start_times: Dict[str, float] = {}

    def pytest_sessionstart(self, session: Any) -> None:
        self.start_time = time.monotonic()

    def pytest_collection_finish(self, session: Any) -> None:
        self.collected_ids = [item.nodeid for item in session.items]
        self.total_tests = len(session.items)

    def pytest_runtest_protocol(self, item: Any, nextitem: Any) -> Optional[bool]:
        self._test_start_times[item.nodeid] = time.monotonic()
        return None

    def pytest_runtest_logreport(self, report: Any) -> None:
        nodeid = getattr(report, "nodeid", "")
        duration = report.duration if hasattr(report, "duration") else 0.0
        if nodeid in self._test_start_times:
            duration = max(duration, time.monotonic() - self._test_start_times[nodeid])

        if report.when == "call":
            self.test_timings.append((nodeid, duration))
            if report.passed:
                self.passed.append(nodeid)
            elif report.failed:
                self.failed.append((nodeid, str(report.longrepr or "")))
            elif report.skipped:
                self.skipped.append((nodeid, str(report.longrepr or "")))
            self._notify_progress(nodeid)
        elif report.when in ("setup", "teardown"):
            if report.failed:
                self.errors.append((nodeid, str(report.longrepr or "")))
                self._notify_progress(nodeid)
            elif report.skipped and report.when == "setup":
                self.skipped.append((nodeid, str(report.longrepr or "")))
                self._notify_progress(nodeid)

    def _notify_progress(self, current_name: str) -> None:
        completed = len(self.passed) + len(self.failed) + len(self.errors) + len(self.skipped)
        if self.progress_callback and self.total_tests > 0:
            metrics = {
                "passed": len(self.passed),
                "failed": len(self.failed),
                "errors": len(self.errors),
                "skipped": len(self.skipped),
                "percentage": min(100.0, max(0.0, round((completed / self.total_tests) * 100.0, 1))),
            }
            with contextlib.suppress(Exception):
                self.progress_callback(completed, self.total_tests, current_name, metrics)


class PytestRunResult:
    """Encapsulates pytest execution metrics, counts, and exit status."""

    def __init__(
        self,
        exit_code: int,
        collector: PytestCollectorPlugin,
        elapsed_seconds: float,
    ) -> None:
        self.exit_code = exit_code
        self.total_tests = collector.total_tests
        self.passed_count = len(collector.passed)
        self.failed_count = len(collector.failed)
        self.error_count = len(collector.errors)
        self.skipped_count = len(collector.skipped)
        self.collected_ids = list(collector.collected_ids)
        self.failures = list(collector.failed)
        self.errors = list(collector.errors)
        self.test_timings = list(collector.test_timings)
        self.elapsed_seconds = elapsed_seconds

    def wasSuccessful(self) -> bool:
        return self.exit_code == 0 and self.failed_count == 0 and self.error_count == 0


def run_pytest_with_progress(
    tests: Sequence[str],
    timeout: Optional[int] = 60,
    failfast: bool = False,
    verbose: int = 1,
    parallel_workers: int = 1,
    stream=None,
    progress_callback: Optional[Callable[[int, int, str, Dict[str, Any]], None]] = None,
    extra_args: Optional[Sequence[str]] = None,
) -> PytestRunResult:
    """Executes a scoped pytest test run with continuous progress and count reporting."""
    out_stream = stream or sys.stdout
    normalized_targets: List[str] = [normalize_pytest_target(t) for t in tests if t.strip()]

    pytest_args: List[str] = []
    if timeout and timeout > 0:
        pytest_args.append(f"--timeout={timeout}")
    if failfast:
        pytest_args.append("-x")
    if parallel_workers > 1:
        pytest_args.extend(["-n", str(parallel_workers)])

    if verbose > 1:
        pytest_args.append("-vv")
    elif verbose == 1:
        pytest_args.append("-v")
    else:
        pytest_args.append("-q")

    if extra_args:
        pytest_args.extend(extra_args)

    if normalized_targets:
        pytest_args.extend(normalized_targets)
    else:
        pytest_args.append("tests")

    collector = PytestCollectorPlugin(
        stream=out_stream,
        progress_callback=progress_callback,
        verbose=verbose,
    )

    t0 = time.monotonic()
    exit_code = pytest.main(pytest_args, plugins=[collector])
    elapsed = time.monotonic() - t0

    result = PytestRunResult(int(exit_code), collector, elapsed)

    # Print summary matching progress indicator format
    status = "PASS" if result.wasSuccessful() else "FAIL"
    summary_line = (
        f"\nTest Execution Summary: [{status}] in {elapsed:.2f}s | "
        f"Total: {result.total_tests} | "
        f"Passed: {result.passed_count} | "
        f"Failed: {result.failed_count} | "
        f"Errors: {result.error_count} | "
        f"Skipped: {result.skipped_count}"
    )
    print(summary_line, file=out_stream, flush=True)

    return result
