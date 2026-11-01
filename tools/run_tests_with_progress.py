#!/usr/bin/env python3
"""Deterministic test runner with live progress bar and execution metrics.

Enforces the repository control that all long test suites, batch evaluations,
and gate executions must display continuous visual progress bars with exact
counts, percentage completion, elapsed time, and real-time pass/fail metrics.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import signal
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - fallback if tqdm is missing
    tqdm = None  # type: ignore[assignment]


class _NamedTest:
    def __init__(self, tid: str):
        self._tid = tid

    def id(self) -> str:
        return self._tid

    def __str__(self) -> str:
        return self._tid


SUITE_TIMEOUT_REARM_S = 5
SUITE_TIMEOUT_GRACE_S = int(os.environ.get("NEE_TEST_TIMEOUT_GRACE_S", "30"))
SUITE_TIMEOUT_EXIT_CODE = 124


class SuiteTimeoutError(KeyboardInterrupt):
    """Raised by SIGALRM. Subclasses KeyboardInterrupt because unittest re-raises it instead of
    recording it as an ordinary test error and continuing (the old BaseException was swallowed)."""


def _hard_exit_on_timeout(timeout_s: int) -> None:
    """Last-resort watchdog: main thread could not be interrupted; kill children and exit."""
    import subprocess

    sys.stderr.write(
        f"\n\nFATAL: test suite exceeded --timeout {timeout_s}s + {SUITE_TIMEOUT_GRACE_S}s grace; "
        f"terminating child processes and exiting {SUITE_TIMEOUT_EXIT_CODE}\n"
    )
    sys.stderr.flush()
    subprocess.run(["pkill", "-TERM", "-P", str(os.getpid())], check=False, capture_output=True, timeout=5.0)
    os._exit(SUITE_TIMEOUT_EXIT_CODE)


SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) in sys.path:
    sys.path.remove(str(SCRIPT_ROOT))
sys.path.insert(0, str(SCRIPT_ROOT))

ROOT = Path.cwd()
if str(ROOT) in sys.path:
    sys.path.remove(str(ROOT))
sys.path.insert(0, str(ROOT))
os.environ["NEE_ALLOW_TEST_FIXTURES"] = "1"

from tools.test_timing_db import TestTimingDB, get_timing_db_path  # noqa: E402

# Preset test profiles
PRESET_PROFILES = {
    "deterministic": [
        "tests.test_runtime_evidence_claims",
        "tests.test_no_synthetic_embedding_ban",
        "tests.test_bge_m3_preflight",
        "tests.test_bge_m3_runtime_qualification",
        "tests.test_bge_m3_deprecation_gates",
        "tests.test_ingestion_directive_invariants",
        "tests.test_sidecar_validation_cache",
        "tests.test_retrieval_release_evaluation.RetrievalReleaseStaticGateTests",
        "tests.test_dual_pipeline_wave1.DualPipelineWave1Tests.test_01_complete_contract_suite_passes",
        "tests.test_eval_harness.DualPipelineEvaluationHarnessTests.test_route_confusion_suite_is_provider_embedding_and_l2_free",
        "tests.test_eval_harness.DualPipelineEvaluationHarnessTests.test_wave8_outer_quote_contract_unwraps_the_known_blocker",
        "tests.test_eval_harness.DualPipelineEvaluationHarnessTests.test_route_robustness_suite_passes_all_256_cases",
        "tests.test_retrieval_clearance",
        "tests.test_decision_context",
        "tests.test_decision_use_cases",
        "tests.test_w5_reference_monitor",
        "tests.test_w5_1_semantic_expansion",
        "tests.test_daily_intelligence",
        "tests.test_semantic_spine_v2",
        "tests.test_temporal_observations",
        "tests.test_strategic_evidence",
        "tests.test_w7_ai_use_case_qualification",
        "tests.test_runtime_decision_adapter",
        "tests.test_retrieval_di_bridge",
        "tests.test_dev_retrieval_smoke",
        "tests.test_ci_gates",
        "tests.test_drift_guard",
        "tests.test_eval_harness",
        "tests.test_query_output_lifecycle",
        "tests.test_graph_model_integrity",
        "tests.test_node_type_policy",
        "tests.test_graph_retrieval_unit",
        "tests.test_graph_delta_unit",
        "tests.test_graph_content_audit",
        "tests.test_graph_topology_audit",
        "tests.test_decision_pipeline_readiness",
        "tests.test_hitl_governance_enforcement",
        "tests.test_l1_wave13_macro_routing",
        "tests.test_l1_wave14_decision_hardening",
        "tests.test_l1_wave15_pre_mutation_reconciliation",
        "tests.test_l1_wave16_query_routing",
        "tests.test_l1_wave17_ingestion_l0",
        "tests.test_l1_wave18_uwg_pinning",
        "tests.test_query_router",
        "tests.test_decision_engine",
        "tests.test_decision_service",
        "tests.test_nee_pre_mutation_gate",
        "tests.test_ingestion_l2_boundary",
        "tests.test_l1_provider_boundary",
    ],
    "waves-13-18": [
        "tests.test_l1_wave13_macro_routing",
        "tests.test_l1_wave14_decision_hardening",
        "tests.test_l1_wave15_pre_mutation_reconciliation",
        "tests.test_l1_wave16_query_routing",
        "tests.test_l1_wave17_ingestion_l0",
        "tests.test_l1_wave18_uwg_pinning",
    ],
    "hitl": [
        "tests.test_hitl_governance_enforcement",
    ],
    "routing": [
        "tests.test_query_router",
        "tests.test_decision_engine",
        "tests.test_decision_service",
        "tests.test_l1_wave16_query_routing",
        "tests.test_l1_wave17_ingestion_l0",
        "tests.test_l1_wave18_uwg_pinning",
    ],
}


class ProgressBarTestResult(unittest.TextTestResult):
    """Custom TestResult that updates a live progress bar on every test lifecycle event."""

    def __init__(
        self,
        stream,
        descriptions: bool,
        verbosity: int,
        pbar=None,
        total_tests: int = 0,
        progress_callback: Optional[Callable[[int, int, str, Dict[str, Any]], None]] = None,
        per_test_timeout: Optional[int] = None,
    ) -> None:
        super().__init__(stream, descriptions, verbosity)
        self.pbar = pbar
        self.total_tests = total_tests
        self.progress_callback = progress_callback
        self.per_test_timeout = per_test_timeout
        self._test_watchdog: Optional[threading.Timer] = None
        self.dots = False
        self.showAll = False
        self.passed_count = 0
        self.failed_count = 0
        self.error_count = 0
        self.skipped_count = 0
        self.test_timings: list[tuple[str, float]] = []
        self._current_test_start: float = time.monotonic()
        self._current_test_name: str = ""

    def _format_test_name(self, test: unittest.TestCase) -> str:
        test_id = test.id()
        parts = test_id.split(".")
        if len(parts) >= 2:
            return f"{parts[-2]}.{parts[-1]}"
        return test_id

    def startTest(self, test: unittest.TestCase) -> None:
        super().startTest(test)
        self._current_test_start = time.monotonic()
        short_name = self._format_test_name(test)
        self._current_test_name = short_name
        if len(short_name) > 36:
            short_name = short_name[:33] + "..."
        if self.pbar is not None:
            self.pbar.set_description(f"Running {short_name:<36}")
        if (
            self.per_test_timeout
            and self.per_test_timeout > 0
            and threading.current_thread() is threading.main_thread()
        ):
            if hasattr(signal, "alarm"):
                signal.alarm(int(self.per_test_timeout))
            timeout_s = int(self.per_test_timeout)
            self._test_watchdog = threading.Timer(
                timeout_s + SUITE_TIMEOUT_GRACE_S, _hard_exit_on_timeout, args=(timeout_s,)
            )
            self._test_watchdog.daemon = True
            self._test_watchdog.start()

    def stopTest(self, test: unittest.TestCase) -> None:
        super().stopTest(test)
        if self._test_watchdog is not None:
            self._test_watchdog.cancel()
            self._test_watchdog = None
        if (
            self.per_test_timeout
            and self.per_test_timeout > 0
            and hasattr(signal, "alarm")
            and threading.current_thread() is threading.main_thread()
        ):
            signal.alarm(0)

    def _update_pbar(self) -> None:
        if self.pbar is not None:
            self.pbar.update(1)
            self.pbar.set_postfix(
                passed=self.passed_count,
                failed=self.failed_count,
                errors=self.error_count,
                skipped=self.skipped_count,
            )
        if hasattr(self.stream, "flush"):
            self.stream.flush()

        completed = self.passed_count + self.failed_count + self.error_count + self.skipped_count
        if self.progress_callback and self.total_tests > 0:
            metrics = {
                "passed": self.passed_count,
                "failed": self.failed_count,
                "errors": self.error_count,
                "skipped": self.skipped_count,
                "percentage": min(
                    100.0, max(0.0, round((completed / self.total_tests) * 100.0, 1))
                ),
            }
            with contextlib.suppress(Exception):
                self.progress_callback(
                    completed, self.total_tests, self._current_test_name, metrics
                )

    def addSuccess(self, test: unittest.TestCase) -> None:
        super().addSuccess(test)
        self.passed_count += 1
        elapsed = time.monotonic() - self._current_test_start
        self.test_timings.append((test.id(), elapsed))
        self._update_pbar()

    def addFailure(self, test: unittest.TestCase, err) -> None:
        super().addFailure(test, err)
        self.failed_count += 1
        elapsed = time.monotonic() - self._current_test_start
        self.test_timings.append((test.id(), elapsed))
        self._update_pbar()

    def addError(self, test: unittest.TestCase, err) -> None:
        super().addError(test, err)
        self.error_count += 1
        elapsed = time.monotonic() - self._current_test_start
        self.test_timings.append((test.id(), elapsed))
        self._update_pbar()

    def addSkip(self, test: unittest.TestCase, reason: str) -> None:
        super().addSkip(test, reason)
        self.skipped_count += 1
        elapsed = time.monotonic() - self._current_test_start
        self.test_timings.append((test.id(), elapsed))
        self._update_pbar()


class ProgressBarTestRunner:
    """A TestRunner that executes test suites with live progress tracking."""

    def __init__(
        self,
        verbosity: int = 1,
        failfast: bool = False,
        stream=None,
        progress_callback: Optional[Callable[[int, int, str, Dict[str, Any]], None]] = None,
        timeout: Optional[int] = None,
        suite_timeout: Optional[int] = None,
    ) -> None:
        self.verbosity = verbosity
        self.failfast = failfast
        self.stream = stream or sys.stdout
        self.progress_callback = progress_callback
        self.timeout = timeout
        self.suite_timeout = suite_timeout or int(os.environ.get("NEE_SUITE_TIMEOUT", "1800"))

    def run(self, test: unittest.TestSuite) -> ProgressBarTestResult:
        seen_tids: set[str] = set()

        def _dedup_suite(s: unittest.TestSuite) -> unittest.TestSuite:
            filtered = unittest.TestSuite()
            for item in s:
                if isinstance(item, unittest.TestSuite):
                    filtered.addTest(_dedup_suite(item))
                else:
                    tid = item.id()
                    if tid not in seen_tids:
                        seen_tids.add(tid)
                        filtered.addTest(item)
            return filtered

        test = _dedup_suite(test)
        total_tests = test.countTestCases()
        pbar = None
        is_tty = getattr(self.stream, "isatty", lambda: False)()
        if tqdm is not None and total_tests > 0:
            pbar = tqdm(
                total=total_tests,
                desc=f"Running Tests (0/{total_tests})",
                unit="test",
                file=self.stream,
                dynamic_ncols=is_tty,
                ncols=80 if not is_tty else None,
                mininterval=0.5 if not is_tty else 0.1,
                bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}{postfix}]",
            )

        result = ProgressBarTestResult(
            stream=self.stream,
            descriptions=True,
            verbosity=self.verbosity,
            pbar=pbar,
            total_tests=total_tests,
            progress_callback=self.progress_callback,
            per_test_timeout=self.timeout,
        )
        result.failfast = self.failfast

        start_time = time.monotonic()
        alarm_set = False
        old_handler = None
        watchdog: Optional[threading.Timer] = None

        # Suite-level watchdog deadline (protects entire execution from unbounded hang)
        if self.suite_timeout and self.suite_timeout > 0:
            suite_timeout_s = int(self.suite_timeout)
            watchdog = threading.Timer(
                suite_timeout_s + SUITE_TIMEOUT_GRACE_S, _hard_exit_on_timeout, args=(suite_timeout_s,)
            )
            watchdog.daemon = True
            watchdog.start()

        # Per-test timeout handler
        if (
            self.timeout
            and self.timeout > 0
            and hasattr(signal, "alarm")
            and threading.current_thread() is threading.main_thread()
        ):
            timeout_s = int(self.timeout)

            def _timeout_handler(signum, frame):
                signal.alarm(0)
                raise SuiteTimeoutError(f"Individual test execution timed out after {timeout_s}s")

            old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
            alarm_set = True

        try:
            test.run(result)
        except SuiteTimeoutError as te:
            result.shouldStop = True
            result.error_count += 1
            result.errors.append((_NamedTest("execution_timeout"), str(te)))
            if pbar is not None:
                pbar.set_description("Timed out!")
            if hasattr(self.stream, "write"):
                self.stream.write(f"\n\nERROR: {te}\n")
        finally:
            if hasattr(result, "_test_watchdog") and result._test_watchdog is not None:
                result._test_watchdog.cancel()
                result._test_watchdog = None
            if watchdog is not None:
                watchdog.cancel()
            if alarm_set and old_handler is not None:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)
            if pbar is not None:
                pbar.close()

        total_elapsed = time.monotonic() - start_time
        self._print_summary(result, total_tests, total_elapsed)
        return result

    def run_parallel(self, test: unittest.TestSuite, num_workers: int = 4) -> ProgressBarTestResult:  # noqa: C901
        """
        Executes test suite across isolated parallel worker processes.
        Aggregates events through multiprocessing queue into a single live progress bar.
        """
        import multiprocessing as mp

        def _extract_cases(s):
            out = []
            for item in s:
                if isinstance(item, unittest.TestSuite):
                    out.extend(_extract_cases(item))
                else:
                    out.append(item)
            return out

        raw_cases = _extract_cases(test)
        seen_tids: set[str] = set()
        cases = []
        for c in raw_cases:
            tid = c.id()
            if tid not in seen_tids:
                seen_tids.add(tid)
                cases.append(c)
        total_tests = len(cases)
        if total_tests == 0 or num_workers <= 1 or mp.current_process().name != "MainProcess":
            return self.run(test)

        # Group by class to preserve class fixture cohesion while enabling multi-worker parallelism
        unit_groups: dict[str, list[str]] = {}
        for c in cases:
            tid = c.id()
            unit_key = tid.rsplit(".", 1)[0] if "." in tid else tid
            unit_groups.setdefault(unit_key, []).append(tid)

        # LPT (Longest Processing Time) Test Scheduling
        db_path = get_timing_db_path(ROOT)
        timing_db = TestTimingDB(db_path)

        # Calculate expected duration per test unit
        module_times = []
        for unit_key, tids in unit_groups.items():
            unit_time = sum(timing_db.get_duration(t) for t in tids)
            # fallback to 0.1s per test if unknown
            if unit_time == 0.0:
                unit_time = len(tids) * 0.1
            module_times.append((unit_time, unit_key, tids))

        # Sort descending (LPT)
        module_times.sort(key=lambda x: x[0], reverse=True)

        effective_workers = min(num_workers, len(unit_groups))
        chunks: list[list[str]] = [[] for _ in range(effective_workers)]
        worker_loads = [0.0] * effective_workers

        for mod_time, _mod, tids in module_times:
            # Find worker with least load
            min_idx = worker_loads.index(min(worker_loads))
            chunks[min_idx].extend(tids)
            worker_loads[min_idx] += mod_time

        pbar = None
        is_tty = getattr(self.stream, "isatty", lambda: False)()
        if tqdm is not None and total_tests > 0:
            pbar = tqdm(
                total=total_tests,
                desc=f"Running Tests (Parallel: {effective_workers} workers)",
                unit="test",
                file=self.stream,
                dynamic_ncols=is_tty,
                ncols=80 if not is_tty else None,
                mininterval=0.2 if not is_tty else 0.05,
                bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}{postfix}]",
            )

        result = ProgressBarTestResult(
            stream=self.stream,
            descriptions=True,
            verbosity=self.verbosity,
            pbar=pbar,
            total_tests=total_tests,
            progress_callback=self.progress_callback,
        )
        result.failfast = self.failfast

        event_queue = mp.Queue()
        workers = []
        for i in range(effective_workers):
            if not chunks[i]:
                continue
            p = mp.Process(
                target=_worker_test_runner_process,
                args=(chunks[i], event_queue, self.failfast),
            )
            p.daemon = False
            p.start()
            workers.append(p)

        active_workers = len(workers)
        start_time = time.monotonic()
        last_progress_time = time.monotonic()
        suite_timeout_s = int(self.suite_timeout) if self.suite_timeout else 1800

        try:
            while active_workers > 0:
                now = time.monotonic()
                is_suite_timeout = (now - start_time) > suite_timeout_s
                is_test_timeout = (
                    self.timeout and self.timeout > 0 and (now - last_progress_time) > self.timeout
                )
                if is_suite_timeout or is_test_timeout:
                    timeout_reason = (
                        f"Suite deadline exceeded ({suite_timeout_s}s)"
                        if is_suite_timeout
                        else f"Parallel test execution timed out after {self.timeout}s of inactivity"
                    )
                    for w in workers:
                        if w.is_alive():
                            try:
                                w.terminate()
                                w.join(timeout=1.0)
                            except Exception:
                                pass
                    result.error_count += 1
                    result.errors.append((_NamedTest("parallel_execution_timeout"), timeout_reason))
                    if pbar is not None:
                        pbar.set_description("Timed out!")
                    if hasattr(self.stream, "write"):
                        self.stream.write(f"\n\nERROR: {timeout_reason}!\n")
                    break

                try:
                    event = event_queue.get(timeout=0.1)
                    last_progress_time = time.monotonic()
                except Exception:
                    alive = [w for w in workers if w.is_alive()]
                    if len(alive) < active_workers:
                        if hasattr(self.stream, "write"):
                            self.stream.write(
                                f"\n\nERROR: {active_workers - len(alive)} parallel worker process(es) died unexpectedly!\n"
                            )
                        self._was_successful = False
                        active_workers = len(alive)
                        import sys

                        sys.exit(1)
                    continue

                ev_type, test_id, payload, dur = event
                if ev_type in (
                    "pass",
                    "fail",
                    "error",
                    "skip",
                    "expectedFailure",
                    "unexpectedSuccess",
                ):
                    result.test_timings.append((test_id, dur))

                if ev_type == "start":
                    if pbar is not None:
                        short = test_id.split(".")[-1]
                        pbar.set_description(f"Running {short[:24]}...")
                elif ev_type == "pass":
                    result.passed_count += 1
                    if pbar is not None:
                        pbar.update(1)
                        pbar.set_postfix(
                            {"passed": result.passed_count, "failed": result.failed_count}
                        )
                elif ev_type == "fail":
                    result.failed_count += 1
                    result.failures.append((_NamedTest(test_id), payload))
                    if pbar is not None:
                        pbar.update(1)
                        pbar.set_postfix(
                            {"passed": result.passed_count, "failed": result.failed_count}
                        )
                elif ev_type == "error":
                    result.error_count += 1
                    result.errors.append((_NamedTest(test_id), payload))
                    if pbar is not None:
                        pbar.update(1)
                        pbar.set_postfix(
                            {
                                "passed": result.passed_count,
                                "failed": result.failed_count,
                                "errors": result.error_count,
                            }
                        )
                elif ev_type == "skip":
                    result.skipped_count += 1
                    if pbar is not None:
                        pbar.update(1)
                elif ev_type == "done":
                    active_workers -= 1

        finally:
            for w in workers:
                if w.is_alive():
                    w.join(timeout=1.0)
                if w.is_alive():
                    try:
                        w.terminate()
                        w.join(timeout=1.0)
                    except Exception:
                        pass
            if pbar is not None:
                pbar.close()

        total_elapsed = time.monotonic() - start_time
        self._print_summary(result, total_tests, total_elapsed)
        return result

    def _print_summary(
        self,
        result: ProgressBarTestResult,
        total_tests: int,
        total_elapsed: float,
    ) -> None:
        # Print failures and errors if any
        if result.failures:
            print("\n" + "=" * 70, file=self.stream)
            print("FAILURES:", file=self.stream)
            print("=" * 70, file=self.stream)
            for test, err in result.failures:
                print(f"\nFAIL: {test.id()}", file=self.stream)
                print(err, file=self.stream)

        if result.errors:
            print("\n" + "=" * 70, file=self.stream)
            print("ERRORS:", file=self.stream)
            print("=" * 70, file=self.stream)
            for test, err in result.errors:
                print(f"\nERROR: {test.id()}", file=self.stream)
                print(err, file=self.stream)

        # Print clean tabular summary
        status = "PASS" if result.wasSuccessful() else "FAIL"
        summary_line = (
            f"\nTest Execution Summary: [{status}] in {total_elapsed:.2f}s | "
            f"Total: {total_tests} | "
            f"Passed: {result.passed_count} | "
            f"Failed: {result.failed_count} | "
            f"Errors: {result.error_count} | "
            f"Skipped: {result.skipped_count}"
        )
        print(summary_line, file=self.stream, flush=True)


def _worker_test_runner_process(test_ids: list[str], event_queue, failfast: bool) -> None:
    """Worker child process executing an assigned chunk of tests in an isolated sandbox."""
    import os
    import shutil
    import tempfile
    import time
    import traceback
    import unittest

    temp_sandbox = tempfile.mkdtemp(prefix="worker_test_sandbox_")
    os.environ["STORAGE_DATA_DIR"] = temp_sandbox
    os.environ["TEST_SANDBOX_DIR"] = temp_sandbox
    os.environ["TEMP_SANDBOX_DIR"] = temp_sandbox

    try:
        cwd_str = os.getcwd()
        if cwd_str not in sys.path:
            sys.path.insert(0, cwd_str)
        loader = unittest.TestLoader()
        suite = unittest.TestSuite()
        for tid in test_ids:
            try:
                suite.addTests(loader.loadTestsFromName(tid))
            except Exception:
                tb = traceback.format_exc()
                event_queue.put(("error", tid, tb, 0.0))

        class SubResult(unittest.TestResult):
            def startTest(self, test):
                super().startTest(test)
                self._t0 = time.monotonic()
                event_queue.put(("start", test.id(), "", 0.0))

            def addSuccess(self, test):
                super().addSuccess(test)
                dur = time.monotonic() - getattr(self, "_t0", time.monotonic())
                event_queue.put(("pass", test.id(), "", dur))

            def addFailure(self, test, err):
                super().addFailure(test, err)
                dur = time.monotonic() - getattr(self, "_t0", time.monotonic())
                event_queue.put(("fail", test.id(), self._exc_info_to_string(err, test), dur))

            def addError(self, test, err):
                super().addError(test, err)
                dur = time.monotonic() - getattr(self, "_t0", time.monotonic())
                event_queue.put(("error", test.id(), self._exc_info_to_string(err, test), dur))

            def addSkip(self, test, reason):
                super().addSkip(test, reason)
                dur = time.monotonic() - getattr(self, "_t0", time.monotonic())
                event_queue.put(("skip", test.id(), str(reason), dur))

        res = SubResult()
        res.failfast = failfast
        suite.run(res)
    except Exception:
        tb = traceback.format_exc()
        event_queue.put(("error", "worker_process", tb, 0.0))
    finally:
        shutil.rmtree(temp_sandbox, ignore_errors=True)
        event_queue.put(("done", "", "", 0.0))


def _make_failed_test(item_name: str, exception: Exception) -> unittest.TestSuite:
    """Create a synthetic failing test case so module load failures fail closed in CI."""

    class TestModuleLoadFailure(unittest.TestCase):
        def test_module_import_and_load(self) -> None:
            raise RuntimeError(
                f"Failed to load test module '{item_name}': {type(exception).__name__}: {exception}"
            )

    TestModuleLoadFailure.__name__ = f"FailedToLoad_{item_name.replace('.', '_').replace('/', '_')}"
    return unittest.defaultTestLoader.loadTestsFromTestCase(TestModuleLoadFailure)


def _make_zero_collected_test(item_name: str) -> unittest.TestSuite:
    """Create a synthetic failing test case when an explicitly requested test module produces zero tests."""

    class TestModuleZeroCollected(unittest.TestCase):
        def test_module_must_collect_cases(self) -> None:
            raise RuntimeError(
                f"Test target '{item_name}' collected 0 test cases across both unittest and pytest runners. Zero-collection is a blocking failure."
            )

    TestModuleZeroCollected.__name__ = f"ZeroCollected_{item_name.replace('.', '_').replace('/', '_')}"
    return unittest.defaultTestLoader.loadTestsFromTestCase(TestModuleZeroCollected)


def _has_unittest_testcase_ast(mod_file: Path) -> bool:
    """Checks whether file defines any classes inheriting from TestCase without importing."""
    import ast
    try:
        tree = ast.parse(mod_file.read_text(encoding="utf-8"), filename=str(mod_file))
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    if (isinstance(base, ast.Name) and "TestCase" in base.id) or (
                        isinstance(base, ast.Attribute) and "TestCase" in base.attr
                    ):
                        return True
    except Exception:
        pass
    return False


def _inspect_pytest_module(raw_item: str, mod_item: str) -> tuple[Optional[unittest.TestSuite], Optional[str]]:
    """Analyzes test module via AST to verify test collection without importing into MainProcess."""
    import ast
    from tools.test_module_cache import resolve_test_module_file

    mod_file = resolve_test_module_file(raw_item, ROOT) or resolve_test_module_file(mod_item, ROOT)
    if mod_file is not None and mod_file.is_file():
        try:
            content = mod_file.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(mod_file))
            funcs = [n.name for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
            classes = [n.name for n in tree.body if isinstance(n, ast.ClassDef) and n.name.startswith("Test")]
            if funcs or classes:
                return None, raw_item
            print(
                f"ERROR: Test module '{raw_item}' collected 0 test cases across both unittest and pytest.",
                file=sys.stderr,
            )
            return _make_zero_collected_test(raw_item), None
        except SyntaxError as e:
            print(f"ERROR: Could not load test module '{raw_item}': {e}", file=sys.stderr)
            return _make_failed_test(raw_item, e), None
        except Exception:
            pass

    import importlib
    import inspect
    try:
        mod = importlib.import_module(mod_item)
        funcs = [n for n, v in inspect.getmembers(mod, inspect.isfunction) if n.startswith("test_")]
        classes = [n for n, v in inspect.getmembers(mod, inspect.isclass) if n.startswith("Test")]
        if funcs or classes:
            return None, raw_item
        print(
            f"ERROR: Test module '{raw_item}' collected 0 test cases across both unittest and pytest.",
            file=sys.stderr,
        )
        return _make_zero_collected_test(raw_item), None
    except Exception as e:
        print(f"ERROR: Could not load test module '{raw_item}': {e}", file=sys.stderr)
        return _make_failed_test(raw_item, e), None


def _classify_single_module_target(
    raw_item: str, mod_item: str, loader: unittest.TestLoader
) -> tuple[Optional[unittest.TestSuite], Optional[str]]:
    from tools.test_module_cache import resolve_test_module_file

    mod_file = resolve_test_module_file(raw_item, ROOT) or resolve_test_module_file(mod_item, ROOT)
    if mod_file is not None and mod_file.is_file():
        # Fast AST path: if no TestCase class is present, route straight to pytest without importing
        if not _has_unittest_testcase_ast(mod_file):
            return _inspect_pytest_module(raw_item, mod_item)

    try:
        loaded = loader.loadTestsFromName(mod_item)
        if loaded.countTestCases() > 0:
            return loaded, None
    except Exception:
        pass
    return _inspect_pytest_module(raw_item, mod_item)


def _classify_target_item(
    raw_item: str, start_dir: str, loader: unittest.TestLoader
) -> tuple[Optional[unittest.TestSuite], Optional[str]]:
    raw_path = Path(raw_item)
    if raw_path.is_file():
        if _has_unittest_testcase_ast(raw_path):
            try:
                discovered = loader.discover(start_dir=str(raw_path.parent), pattern=raw_path.name)
                if discovered.countTestCases() > 0:
                    return discovered, None
            except Exception:
                pass
        return _inspect_pytest_module(raw_item, raw_path.stem)

    norm_item = raw_item.replace("\\", "/")
    if norm_item.endswith(".py"):
        norm_item = norm_item[:-3]
    mod_item = norm_item.strip("/").replace("/", ".")
    if mod_item.startswith("tests.") or mod_item.startswith("test_") or "." in mod_item:
        return _classify_single_module_target(raw_item, mod_item, loader)
    discovered = loader.discover(start_dir=start_dir, pattern=raw_item)
    if discovered.countTestCases() > 0:
        return discovered, None
    return None, raw_item


def classify_and_build_suites(
    patterns_or_modules: Sequence[str],
    start_dir: str = "tests",
) -> tuple[unittest.TestSuite, list[str]]:
    """Build a TestSuite for unittest cases and a list of target items for pytest.

    Fails closed if any explicitly requested module fails to load or collects zero tests.
    """
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    pytest_items: list[str] = []

    if not patterns_or_modules:
        suite.addTests(loader.discover(start_dir=start_dir, pattern="test_*.py"))
        return suite, pytest_items

    seen_keys: set[str] = set()
    for raw_item in patterns_or_modules:
        item = raw_item.strip()
        if not item:
            continue
        canon = item.replace("\\", "/")
        if canon.endswith(".py"):
            canon = canon[:-3]
        canon = canon.strip("/").replace("/", ".")
        if canon in seen_keys:
            continue
        seen_keys.add(canon)

        sub_suite, pytest_item = _classify_target_item(item, start_dir, loader)
        if sub_suite:
            suite.addTests(sub_suite)
        if pytest_item and pytest_item not in pytest_items:
            pytest_items.append(pytest_item)

    return suite, pytest_items


def run_pytest(
    items: Sequence[str],
    timeout: Optional[int] = 60,
    parallel_workers: int = 1,
    verbose: int = 1,
    failfast: bool = False,
    timeout_method: Optional[str] = None,
    failed_items_out: Optional[set[str]] = None,
    timings_out: Optional[list[tuple[str, float]]] = None,
    test_counts_out: Optional[dict[str, int]] = None,
) -> int:
    """Execute pytest targets retaining timeout, worker, and live progress reporter controls."""
    import subprocess
    import tempfile
    import xml.etree.ElementTree as ET

    cmd = [sys.executable, "-m", "pytest"]
    if timeout:
        cmd.append(f"--timeout={timeout}")
        method = timeout_method or ("signal" if os.name == "posix" else "thread")
        cmd.append(f"--timeout-method={method}")
    if parallel_workers > 1:
        cmd.extend(["-n", str(parallel_workers)])
    if verbose > 1:
        cmd.append("-v")
    elif verbose <= 0:
        cmd.append("-q")
    if failfast:
        cmd.append("-x")

    junit_tmp = tempfile.NamedTemporaryFile(suffix=".xml", delete=False)
    junit_tmp.close()
    cmd.append(f"--junit-xml={junit_tmp.name}")

    normalized_items: list[str] = []
    for it in items:
        cleaned = it.strip()
        if cleaned.endswith(".py") or "/" in cleaned or "\\" in cleaned:
            normalized_items.append(cleaned)
        else:
            parts = cleaned.split(".")
            candidate = ROOT / Path(*parts).with_suffix(".py")
            if candidate.exists():
                normalized_items.append(str(candidate))
            else:
                normalized_items.append(cleaned)
    cmd.extend(normalized_items)

    try:
        subproc_timeout = (float(timeout) * max(len(items), 1) + 60.0) if timeout else 600.0
        proc = subprocess.run(cmd, env=os.environ.copy(), timeout=subproc_timeout)
        parsed_timings: list[tuple[str, float]] = []
        failed_classes: set[str] = set()
        failed_files: set[str] = set()
        passed_cnt, failed_cnt, skipped_cnt = 0, 0, 0

        if os.path.exists(junit_tmp.name) and os.path.getsize(junit_tmp.name) > 0:
            try:
                tree = ET.parse(junit_tmp.name)
                root = tree.getroot()
                for tc in root.iter("testcase"):
                    is_failed = tc.find("failure") is not None or tc.find("error") is not None
                    is_skipped = tc.find("skipped") is not None
                    if is_failed:
                        failed_cnt += 1
                        if "classname" in tc.attrib:
                            failed_classes.add(tc.attrib["classname"])
                        if "file" in tc.attrib:
                            failed_files.add(tc.attrib["file"])
                    elif is_skipped:
                        skipped_cnt += 1
                    else:
                        passed_cnt += 1

                    classname = tc.attrib.get("classname", "")
                    name = tc.attrib.get("name", "")
                    time_str = tc.attrib.get("time", "0.0")
                    try:
                        dur_s = float(time_str)
                    except (ValueError, TypeError):
                        dur_s = 0.0
                    test_id = f"{classname}.{name}" if classname and name else (name or classname)
                    if test_id:
                        parsed_timings.append((test_id, dur_s))
            except Exception as e:
                if verbose > 1:
                    print(f"Warning: Failed to parse JUnit XML '{junit_tmp.name}': {e}", file=sys.stderr)

        if test_counts_out is not None:
            test_counts_out["passed"] = test_counts_out.get("passed", 0) + passed_cnt
            test_counts_out["failed"] = test_counts_out.get("failed", 0) + failed_cnt
            test_counts_out["skipped"] = test_counts_out.get("skipped", 0) + skipped_cnt

        if parsed_timings:
            _sync_timings_to_dbs(parsed_timings, ROOT)
            if timings_out is not None:
                timings_out.extend(parsed_timings)

        if proc.returncode != 0 and failed_items_out is not None:
            if not failed_files and not failed_classes:
                failed_items_out.update(items)
            else:
                for it in items:
                    norm = it.replace("\\", "/").removesuffix(".py").strip("/")
                    norm_dotted = norm.replace("/", ".")
                    stem = norm.split("/")[-1].split(".")[-1]
                    is_failed = False
                    for ff in failed_files:
                        norm_ff = ff.replace("\\", "/").removesuffix(".py").strip("/")
                        if norm in norm_ff or norm_ff in norm or norm_ff.split("/")[-1] == stem:
                            is_failed = True
                            break
                    if not is_failed:
                        for fc in failed_classes:
                            if fc.startswith(f"{norm_dotted}.") or f".{stem}." in f".{fc}.":
                                is_failed = True
                                break
                    if is_failed:
                        failed_items_out.add(it)
        return proc.returncode
    finally:
        with contextlib.suppress(Exception):
            os.unlink(junit_tmp.name)


def _sync_timings_to_dbs(timings: Sequence[tuple[str, float]], repo_root: Optional[Path] = None) -> None:
    if not timings:
        return
    root = repo_root or ROOT
    try:
        json_path = get_timing_db_path(root)
        db_json = TestTimingDB(json_path)
        for tid, dur in timings:
            db_json.record(tid, dur)
        db_json.save()
    except Exception:
        pass

    try:
        local_dir = root / ".local"
        if local_dir.is_dir() or (root / ".git").is_file():
            local_dir.mkdir(parents=True, exist_ok=True)
            sqlite_path = local_dir / "test_timings.sqlite"
            db_sqlite = TestTimingDB(sqlite_path)
            for tid, dur in timings:
                db_sqlite.record(tid, dur)
            db_sqlite.save()
    except Exception:
        pass


def _print_runtime_breakdown_table(
    *,
    wall_clock_s: float,
    timings: Sequence[tuple[str, float]],
    executed_targets: Sequence[str],
    cached_targets: Sequence[str],
    passed: int,
    failed: int,
    errors: int,
    skipped: int,
    require_live_runtime: bool,
) -> None:
    print("\n" + "=" * 80)
    print("⏱️  Test Execution Runtime & Observable Results Breakdown")
    print("=" * 80)
    mode = "LIVE (require-live-runtime=True)" if require_live_runtime else "STANDARD"
    total_mods = len(executed_targets) + len(cached_targets)
    print(
        f"Execution Mode: {mode} | Total Modules: {total_mods} "
        f"(Executed: {len(executed_targets)}, Cached: {len(cached_targets)})"
    )
    total_tests = passed + failed + errors + skipped
    print(
        f"Suite Wall-Clock: {wall_clock_s:.2f}s | Total Tests: {total_tests} | "
        f"Passed: {passed} | Failed: {failed} | Errors: {errors} | Skipped: {skipped}"
    )

    if timings:
        durations = sorted([dur for _, dur in timings])
        p50 = durations[len(durations) // 2]
        p95_idx = min(len(durations) - 1, int(len(durations) * 0.95))
        p95 = durations[p95_idx]
        min_d = durations[0]
        max_d = durations[-1]
        print(f"Latency Distribution: P50={p50:.3f}s | P95={p95:.3f}s | Min={min_d:.3f}s | Max={max_d:.3f}s")

    if executed_targets:
        print("\nPer-Module Runtime:")
        for target in executed_targets:
            target_norm = target.replace("\\", "/").removesuffix(".py").strip("/").replace("/", ".")
            stem = target_norm.split(".")[-1]
            mod_timings = [dur for tid, dur in timings if target_norm in tid or f".{stem}." in f".{tid}."]
            if mod_timings:
                mod_dur = sum(mod_timings)
                avg_dur = mod_dur / len(mod_timings)
                print(f"  - {target}: {len(mod_timings)} tests in {mod_dur:.3f}s (avg {avg_dur:.3f}s/test)")
            else:
                print(f"  - {target}: executed live")

    if timings:
        slowest = sorted(timings, key=lambda x: x[1], reverse=True)[:5]
        print("\nSlowest Tests:")
        for rank, (tid, dur) in enumerate(slowest, start=1):
            short_id = tid if len(tid) <= 60 else "..." + tid[-57:]
            print(f"  {rank}. {short_id:<60} : {dur:.3f}s")

    print("=" * 80 + "\n", flush=True)


def build_suite(
    patterns_or_modules: Sequence[str],
    start_dir: str = "tests",
) -> unittest.TestSuite:
    """Build a TestSuite from explicit module names or discovery patterns.

    Normalizes file paths and module paths (slashes to dots) and fails closed if
    any explicitly requested test module fails to load.
    """
    suite, _ = classify_and_build_suites(patterns_or_modules, start_dir=start_dir)
    return suite


def _setup_test_env() -> None:
    import os
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    os.environ["RUNNING_TESTS_WITH_PROGRESS"] = "1"
    os.environ["NEE_TEST_MODE"] = "1"
    if hasattr(sys.stdout, "reconfigure"):
        with contextlib.suppress(Exception):
            sys.stdout.reconfigure(line_buffering=True)


def _build_test_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run unittest suite with an informative, continuous progress bar."
    )
    parser.add_argument("tests", nargs="*", help="Optional test module names or patterns")
    parser.add_argument("--profile", choices=list(PRESET_PROFILES.keys()), help="Run a preset test profile")
    parser.add_argument("--runner", choices=["auto", "unittest", "pytest"], default="auto")
    parser.add_argument("--start-dir", default="tests", help="Directory to start test discovery from")
    parser.add_argument("--failfast", action="store_true", help="Stop on the first failure or error")
    parser.add_argument("-v", "--verbose", action="count", default=1, help="Verbosity level")

    def _parse_workers(val: str) -> int:
        if str(val).lower() == "auto":
            return max(1, os.cpu_count() or 4)
        return int(val)

    parser.add_argument("--parallel-workers", "-j", type=_parse_workers, default=1)
    parser.add_argument("--timeout", type=int, default=None, help="Per-test timeout in seconds")
    parser.add_argument(
        "--timeout-method",
        choices=["signal", "thread"],
        default="signal" if os.name == "posix" else "thread",
        help="Timeout mechanism for pytest ('signal' or 'thread')",
    )
    parser.add_argument("--suite-timeout", type=int, default=None, help="Overall test suite deadline in seconds")
    parser.add_argument("--cache-modules", action="store_true", default=False, help="Enable content-addressable per-module pass caching")
    parser.add_argument("--require-live-runtime", action="store_true", default=False, help="Forbid module cache skipping and require live test execution")
    parser.add_argument("--receipt", type=str, default=None, help="Path to write runner execution receipt JSON")
    return parser


def _run_unittest_suite(
    suite: unittest.TestSuite,
    args: argparse.Namespace,
    timeout: int,
    failed_items_out: Optional[set[str]] = None,
    target_items: Optional[Sequence[str]] = None,
    timings_out: Optional[list[tuple[str, float]]] = None,
    test_counts_out: Optional[dict[str, int]] = None,
) -> bool:
    suite_timeout = getattr(args, "suite_timeout", None) or int(os.environ.get("NEE_SUITE_TIMEOUT", "1800"))
    runner = ProgressBarTestRunner(
        verbosity=args.verbose,
        failfast=args.failfast,
        timeout=timeout,
        suite_timeout=suite_timeout,
    )
    if args.parallel_workers > 1:
        result = runner.run_parallel(suite, num_workers=args.parallel_workers)
    else:
        result = runner.run(suite)

    _sync_timings_to_dbs(result.test_timings, ROOT)
    if timings_out is not None:
        timings_out.extend(result.test_timings)

    if test_counts_out is not None:
        test_counts_out["passed"] = test_counts_out.get("passed", 0) + getattr(result, "passed_count", 0)
        test_counts_out["failed"] = test_counts_out.get("failed", 0) + getattr(result, "failed_count", 0)
        test_counts_out["errors"] = test_counts_out.get("errors", 0) + getattr(result, "error_count", 0)
        test_counts_out["skipped"] = test_counts_out.get("skipped", 0) + getattr(result, "skipped_count", 0)

    if failed_items_out is not None and not result.wasSuccessful():
        failed_test_ids: set[str] = set()
        for test, _ in getattr(result, "failures", []):
            tid = test.id() if hasattr(test, "id") else str(test)
            failed_test_ids.add(tid)
        for test, _ in getattr(result, "errors", []):
            tid = test.id() if hasattr(test, "id") else str(test)
            failed_test_ids.add(tid)

        targets = target_items or []
        for it in targets:
            norm_dotted = it.replace("\\", "/").removesuffix(".py").strip("/").replace("/", ".")
            stem = norm_dotted.split(".")[-1]
            for fid in failed_test_ids:
                if fid.startswith(f"{norm_dotted}.") or fid == norm_dotted or f".{stem}." in f".{fid}.":
                    failed_items_out.add(it)
                    break

    return result.wasSuccessful()


def main(argv: Sequence[str] | None = None) -> int:
    _setup_test_env()
    args = _build_test_arg_parser().parse_args(argv)

    target_tests: list[str] = []
    if args.profile:
        target_tests.extend(PRESET_PROFILES[args.profile])
    if args.tests:
        target_tests.extend(args.tests)

    timeout = args.timeout if args.timeout is not None else 60
    start_time = time.monotonic()
    use_module_cache = args.cache_modules or os.environ.get("NEE_TEST_MODULE_CACHE") == "1"
    require_live = bool(getattr(args, "require_live_runtime", False))
    if require_live:
        use_module_cache = False
        print(
            "⚡ Live Runtime Required: Bypassing module cache; executing all test targets live.",
            flush=True,
        )

    cached_skipped: list[str] = []
    executed_targets: list[str] = list(target_tests)
    module_fps: dict[str, str] = {}

    if use_module_cache and target_tests:
        from tools.test_module_cache import (
            compute_module_fingerprint,
            get_cached_module,
            write_runner_receipt,
        )

        filtered_targets: list[str] = []
        for item in target_tests:
            fp, _ = compute_module_fingerprint(item, ROOT)
            if fp and get_cached_module(item, ROOT, fp) is not None:
                cached_skipped.append(item)
            else:
                filtered_targets.append(item)
                if fp:
                    module_fps[item] = fp
        executed_targets = filtered_targets

        if cached_skipped:
            print(
                f"⚡ Module Cache: Reusing {len(cached_skipped)} cached PASS module(s), {len(executed_targets)} to execute."
            )

        if not executed_targets and cached_skipped:
            duration = round(time.monotonic() - start_time, 3)
            print(
                f"Test Execution Summary: [PASS] in {duration}s | Total: {len(target_tests)} | Passed: {len(target_tests)} (all from cache) | Failed: 0 | Errors: 0 | Skipped: 0"
            )
            default_receipt = str(ROOT / ".local" / "test_runner_receipt.json")
            receipt_path = args.receipt or default_receipt
            write_runner_receipt(receipt_path, True, len(target_tests), cached_skipped, [], duration)
            return 0

    failed_items: set[str] = set()
    all_timings: list[tuple[str, float]] = []
    suite_counts: dict[str, int] = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}

    if args.runner == "pytest":
        targets = executed_targets or ["tests"]
        code = run_pytest(
            targets,
            timeout=timeout,
            parallel_workers=args.parallel_workers,
            verbose=args.verbose,
            failfast=args.failfast,
            timeout_method=args.timeout_method,
            failed_items_out=failed_items,
            timings_out=all_timings,
            test_counts_out=suite_counts,
        )
        overall_success = code == 0
        if not overall_success and not failed_items:
            failed_items = set(executed_targets)
    else:
        suite, pytest_items = classify_and_build_suites(executed_targets, start_dir=args.start_dir)
        if args.runner == "unittest":
            pytest_items = []

        if executed_targets and suite.countTestCases() == 0 and not pytest_items:
            print(
                f"ERROR: Explicitly selected test targets {executed_targets} yielded 0 collected test cases.",
                file=sys.stderr,
            )
            return 1

        overall_success = True
        if suite.countTestCases() > 0:
            unittest_success = _run_unittest_suite(
                suite,
                args,
                timeout,
                failed_items_out=failed_items,
                target_items=executed_targets,
                timings_out=all_timings,
                test_counts_out=suite_counts,
            )
            if not unittest_success:
                overall_success = False

        if pytest_items:
            code = run_pytest(
                pytest_items,
                timeout=timeout,
                parallel_workers=args.parallel_workers,
                verbose=args.verbose,
                failfast=args.failfast,
                timeout_method=args.timeout_method,
                failed_items_out=failed_items,
                timings_out=all_timings,
                test_counts_out=suite_counts,
            )
            if code != 0:
                overall_success = False

        if not overall_success and not failed_items:
            failed_items = set(executed_targets)

    if use_module_cache:
        from tools.test_module_cache import save_cached_module

        # Cache every module that passed (0 failures and 0 errors in that module)
        for item, fp in module_fps.items():
            if item not in failed_items:
                save_cached_module(item, ROOT, fp)

    duration = round(time.monotonic() - start_time, 3)

    _print_runtime_breakdown_table(
        wall_clock_s=duration,
        timings=all_timings,
        executed_targets=executed_targets,
        cached_targets=cached_skipped,
        passed=suite_counts.get("passed", 0),
        failed=suite_counts.get("failed", 0),
        errors=suite_counts.get("errors", 0),
        skipped=suite_counts.get("skipped", 0),
        require_live_runtime=require_live,
    )

    total_run_tests = sum(suite_counts.values())
    if total_run_tests == 0 and executed_targets:
        if all_timings:
            total_run_tests = len(all_timings)
            suite_counts["passed"] = len(all_timings) if overall_success else 0
            if not overall_success:
                suite_counts["failed"] = len(all_timings)
        elif overall_success:
            total_run_tests = len(executed_targets)
            suite_counts["passed"] = len(executed_targets)

    status_label = "[PASS]" if overall_success else "[FAIL]"
    print(
        f"Test Execution Summary: {status_label} in {duration}s | Total: {total_run_tests} | "
        f"Passed: {suite_counts.get('passed', 0)} | Failed: {suite_counts.get('failed', 0)} | "
        f"Errors: {suite_counts.get('errors', 0)} | Skipped: {suite_counts.get('skipped', 0)}",
        flush=True,
    )

    if args.receipt or (use_module_cache and target_tests):
        from tools.test_module_cache import write_runner_receipt

        default_receipt = str(ROOT / ".local" / "test_runner_receipt.json")
        receipt_path = args.receipt or default_receipt
        write_runner_receipt(
            receipt_path, overall_success, len(target_tests), cached_skipped, executed_targets, duration
        )

    return 0 if overall_success else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
