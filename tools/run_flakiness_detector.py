#!/usr/bin/env python3
"""Automated multi-iteration test flakiness detector & quarantine engine.

Executes test targets across N randomized/shuffled iterations to detect:
1. Race conditions and non-deterministic state leakage
2. Unseeded random generation or temporal dependency flaws
3. Slow Class A unit tests exceeding the 3.0s fast-feedback threshold
4. Flaky tests requiring immediate quarantine recommendations
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
import unittest
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover
    tqdm = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_MAX_DURATION_SECONDS: float = 3.0
DEFAULT_ITERATIONS: int = 10


@dataclass
class TestFlakinessSummary:
    __test__ = False
    test_id: str
    total_runs: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    durations: List[float] = field(default_factory=list)
    min_duration: float = 0.0
    max_duration: float = 0.0
    avg_duration: float = 0.0
    flakiness_rate: float = 0.0
    is_flaky: bool = False
    is_consistently_failing: bool = False
    exceeds_duration_limit: bool = False
    recommendation: str = "STABLE"
    failure_samples: List[str] = field(default_factory=list)


def normalize_target_to_module(target: str, repo_root: Path) -> str:
    """Normalize a filesystem path or test specifier into a loadable module path."""
    target_clean = target.strip()
    if "::" in target_clean:
        parts = target_clean.split("::")
        file_part = parts[0]
        remainder = ".".join(parts[1:])
        mod_prefix = normalize_target_to_module(file_part, repo_root)
        return f"{mod_prefix}.{remainder}"

    p = Path(target_clean)
    if p.suffix == ".py":
        try:
            rel = p.resolve().relative_to(repo_root.resolve())
            return str(rel.with_suffix("")).replace("/", ".").replace("\\", ".")
        except ValueError:
            return str(p.with_suffix("")).replace("/", ".").replace("\\", ".")

    return target_clean


def flatten_test_suite(suite_or_case: Any) -> List[unittest.TestCase]:
    """Recursively extract all TestCase instances from a TestSuite."""
    tests: List[unittest.TestCase] = []
    if isinstance(suite_or_case, unittest.TestCase):
        tests.append(suite_or_case)
    elif hasattr(suite_or_case, "__iter__"):
        for item in suite_or_case:
            tests.extend(flatten_test_suite(item))
    return tests


def discover_test_descriptors(
    targets: Sequence[str],
    repo_root: Path,
) -> List[Tuple[type[unittest.TestCase], str, str]]:
    """Discover all test descriptors (class, method_name, test_id) for the given targets."""
    loader = unittest.defaultTestLoader
    descriptors: List[Tuple[type[unittest.TestCase], str, str]] = []

    for target in targets:
        mod_name = normalize_target_to_module(target, repo_root)
        suite = loader.loadTestsFromName(mod_name)
        for tc in flatten_test_suite(suite):
            descriptors.append((tc.__class__, tc._testMethodName, tc.id()))

    return descriptors


def _run_single_test_iteration(
    cls: type[unittest.TestCase],
    method_name: str,
) -> Tuple[str, float, Optional[str]]:
    """Execute a single test case instance and return (status, duration, error_msg)."""
    case = cls(method_name)
    result = unittest.TestResult()
    start_t = time.monotonic()
    case(result)
    duration = time.monotonic() - start_t

    if result.wasSuccessful():
        return ("PASS", duration, None)
    if result.failures:
        return ("FAIL", duration, str(result.failures[0][1]))
    if result.errors:
        return ("ERROR", duration, str(result.errors[0][1]))
    return ("SKIP", duration, None)


def _compute_summary_metrics(
    summary: TestFlakinessSummary,
    max_duration: float,
) -> None:
    """Compute aggregated statistical metrics and recommendations for a single test."""
    if summary.durations:
        summary.min_duration = min(summary.durations)
        summary.max_duration = max(summary.durations)
        summary.avg_duration = sum(summary.durations) / len(summary.durations)
    summary.exceeds_duration_limit = summary.max_duration > max_duration

    non_skipped = summary.passed + summary.failed + summary.errors
    if non_skipped > 0:
        if summary.passed > 0 and (summary.failed > 0 or summary.errors > 0):
            summary.is_flaky = True
            summary.flakiness_rate = round((summary.failed + summary.errors) / non_skipped, 4)
            summary.recommendation = "QUARANTINE_FLAKY"
        elif summary.passed == 0 and (summary.failed > 0 or summary.errors > 0):
            summary.is_consistently_failing = True
            summary.recommendation = "DEFECT_CONSISTENT_FAILURE"
        elif summary.exceeds_duration_limit:
            summary.recommendation = "PERFORMANCE_SLOW"
        else:
            summary.recommendation = "STABLE"


def _record_execution_outcome(
    s: TestFlakinessSummary,
    status: str,
    duration: float,
    err_msg: Optional[str],
) -> None:
    s.total_runs += 1
    s.durations.append(duration)
    if status == "PASS":
        s.passed += 1
    elif status == "FAIL":
        s.failed += 1
        if err_msg and len(s.failure_samples) < 3:
            s.failure_samples.append(err_msg)
    elif status == "ERROR":
        s.errors += 1
        if err_msg and len(s.failure_samples) < 3:
            s.failure_samples.append(err_msg)
    elif status == "SKIP":
        s.skipped += 1


def _execute_iteration(
    descriptors: List[Tuple[type[unittest.TestCase], str, str]],
    summaries: Dict[str, TestFlakinessSummary],
    pbar: Any,
    iter_idx: int,
    total_iters: int,
) -> None:
    for cls, method_name, test_id in descriptors:
        status, duration, err_msg = _run_single_test_iteration(cls, method_name)
        _record_execution_outcome(summaries[test_id], status, duration, err_msg)
        if pbar is not None:
            pbar.update(1)
            pbar.set_postfix(iter=f"{iter_idx + 1}/{total_iters}")


def run_flakiness_assessment(
    descriptors: List[Tuple[type[unittest.TestCase], str, str]],
    iterations: int = DEFAULT_ITERATIONS,
    shuffle: bool = True,
    seed: int = 42,
    max_duration: float = DEFAULT_MAX_DURATION_SECONDS,
    show_progress: bool = True,
) -> Dict[str, TestFlakinessSummary]:
    """Execute all test descriptors across N iterations and collect flakiness metrics."""
    summaries: Dict[str, TestFlakinessSummary] = {
        test_id: TestFlakinessSummary(test_id=test_id) for _, _, test_id in descriptors
    }

    total_executions = len(descriptors) * iterations
    pbar = None
    if show_progress and tqdm is not None and total_executions > 0:
        pbar = tqdm(total=total_executions, desc="Flakiness Stress Scan", dynamic_ncols=True)

    for i in range(iterations):
        iter_descriptors = list(descriptors)
        if shuffle:
            rng = random.Random(seed + i)
            rng.shuffle(iter_descriptors)
        _execute_iteration(iter_descriptors, summaries, pbar, i, iterations)

    if pbar is not None:
        pbar.close()

    for s in summaries.values():
        _compute_summary_metrics(s, max_duration)

    return summaries


def build_runtime_receipt(
    targets: List[str],
    iterations: int,
    summaries: Dict[str, TestFlakinessSummary],
    max_duration: float,
) -> Dict[str, Any]:
    """Build a deterministic, RFC-8259 verifiable flakiness runtime receipt."""
    flaky_tests = [s.test_id for s in summaries.values() if s.is_flaky]
    failing_tests = [s.test_id for s in summaries.values() if s.is_consistently_failing]
    slow_tests = [s.test_id for s in summaries.values() if s.exceeds_duration_limit]
    overall_status = "PASS" if not flaky_tests and not failing_tests else "FAIL"

    metrics_payload = {
        "total_test_definitions": len(summaries),
        "iterations_evaluated": iterations,
        "total_test_runs": sum(s.total_runs for s in summaries.values()),
        "flaky_test_count": len(flaky_tests),
        "failing_test_count": len(failing_tests),
        "slow_test_count": len(slow_tests),
    }

    digest = hashlib.sha256(json.dumps(metrics_payload, sort_keys=True).encode("utf-8")).hexdigest()

    return {
        "receipt_contract": "urn:nee:dual-pipeline:flakiness-detector-receipt:1.0.0",
        "wave_id": "Wave-3",
        "wave_title": "Test Harness Flakiness Quarantine & Fast-Feedback Optimization",
        "status": overall_status,
        "targets": targets,
        "max_duration_threshold_seconds": max_duration,
        "metrics": metrics_payload,
        "quarantine_recommendations": flaky_tests,
        "canonical_digest": digest,
    }


def print_flakiness_report(
    summaries: Dict[str, TestFlakinessSummary],
    iterations: int,
    max_duration: float,
) -> int:
    """Print user-facing diagnostics report and return exit status code."""
    flaky = [s for s in summaries.values() if s.is_flaky]
    failing = [s for s in summaries.values() if s.is_consistently_failing]
    slow = [s for s in summaries.values() if s.exceeds_duration_limit]

    print("=" * 80)
    print("  TEST FLAKINESS & FAST-FEEDBACK BENCHMARK REPORT")
    print("=" * 80)
    print(f"Evaluated {len(summaries)} test(s) across {iterations} iteration(s).")
    print(f"Duration limit for Class A fast-feedback: <= {max_duration:.1f}s.\n")

    if flaky:
        print(f"❌ FLAKY TESTS DETECTED ({len(flaky)}): Immediate Quarantine Recommended:")
        for s in sorted(flaky, key=lambda x: x.flakiness_rate, reverse=True):
            print(f"  - {s.test_id} (flake rate: {s.flakiness_rate * 100:.1f}%, {s.failed} fails, {s.errors} errs)")
    else:
        print("✅ Zero flaky tests detected across all iterations.")

    if failing:
        print(f"\n❌ CONSISTENT TEST FAILURES ({len(failing)}):")
        for s in failing:
            print(f"  - {s.test_id}")

    if slow:
        print(f"\n⚠️  SLOW UNIT TESTS EXCEEDING {max_duration:.1f}s ({len(slow)}):")
        for s in sorted(slow, key=lambda x: x.max_duration, reverse=True):
            print(f"  - {s.test_id}: max {s.max_duration:.2f}s (avg {s.avg_duration:.2f}s)")

    print("=" * 80)
    return 1 if flaky or failing else 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Multi-iteration test flakiness and performance detector.")
    parser.add_argument("--targets", nargs="+", required=True, help="Test file paths or module specifiers to evaluate")
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS, help="Number of randomized iterations")
    parser.add_argument("--no-shuffle", action="store_true", help="Disable test execution order shuffling")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for ordering")
    parser.add_argument("--max-duration", type=float, default=DEFAULT_MAX_DURATION_SECONDS, help="Max test duration")
    parser.add_argument("--json-report", type=Path, default=None, help="Path to write JSON flakiness report")
    parser.add_argument("--receipt", type=Path, default=None, help="Path to write runtime receipt JSON")
    parser.add_argument("--repo-root", type=Path, default=ROOT, help="Repository root path")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress bar")

    args = parser.parse_args(argv)

    descriptors = discover_test_descriptors(args.targets, args.repo_root)
    if not descriptors:
        print(f"ERROR: No tests discovered for targets: {args.targets}", file=sys.stderr)
        return 2

    summaries = run_flakiness_assessment(
        descriptors=descriptors,
        iterations=args.iterations,
        shuffle=not args.no_shuffle,
        seed=args.seed,
        max_duration=args.max_duration,
        show_progress=not args.quiet,
    )

    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        report_data = {
            "iterations": args.iterations,
            "tests": {tid: asdict(s) for tid, s in summaries.items()},
        }
        with open(args.json_report, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2)

    receipt = build_runtime_receipt(args.targets, args.iterations, summaries, args.max_duration)
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        with open(args.receipt, "w", encoding="utf-8") as f:
            json.dump(receipt, f, indent=2)

    return print_flakiness_report(summaries, args.iterations, args.max_duration)


if __name__ == "__main__":
    sys.exit(main())
