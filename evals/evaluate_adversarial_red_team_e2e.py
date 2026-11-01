#!/usr/bin/env python3
"""Adversarial Red Team End-to-End Orchestrator & Evaluation Suite.

Executes all adversarial red team test suites across:
1. Pipeline A: Whisper hallucination, acoustic spoofing, chunk stitching.
2. Pipeline B: Prompt injection, Sybil entities, temporal inversion, cycles.
3. Pipeline C: Polarity inversion, keyword stuffing, concurrent cache race, homoglyphs.
4. Pipeline D: Prior poisoning, ambiguity squatting, sycophancy, stop-hook spoofing.
5. Governance: C-19 evasion, cryptographic seal bit-flip, score inflation, anti-pollution.

Emits canonical runtime execution receipt to reports/adversarial_red_team_e2e_receipt.json.
"""
from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List

from tqdm import tqdm

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ADVERSARIAL_MODULES = [
    "tests.adversarial.test_pipeline_a_adversarial_intake",
    "tests.adversarial.test_pipeline_b_adversarial_extraction",
    "tests.adversarial.test_pipeline_c_adversarial_retrieval",
    "tests.adversarial.test_pipeline_d_adversarial_decision",
    "tests.adversarial.test_governance_adversarial_red_team",
]


class AdversarialTestProgressCollector:
    """Collects test execution metrics with visual tqdm progress updates."""

    def __init__(self, total_tests: int) -> None:
        self.pbar = tqdm(
            total=total_tests,
            desc="Red Team Adversarial Evals",
            unit="test",
            dynamic_ncols=True,
            leave=True,
        )
        self.results: List[Dict[str, Any]] = []
        self.passed_count = 0
        self.failed_count = 0
        self.error_count = 0

    def record_test(self, test_name: str, status: str, duration_sec: float, err: str = "") -> None:
        if status == "PASS":
            self.passed_count += 1
        elif status == "FAIL":
            self.failed_count += 1
        else:
            self.error_count += 1

        self.results.append({
            "test_name": test_name,
            "status": status,
            "duration_sec": round(duration_sec, 4),
            "error": err,
        })
        self.pbar.set_postfix({
            "pass": self.passed_count,
            "fail": self.failed_count,
            "err": self.error_count,
        })
        self.pbar.update(1)

    def close(self) -> None:
        self.pbar.close()


def load_all_adversarial_tests() -> List[unittest.TestCase]:
    """Dynamically load all test cases from defined adversarial modules."""
    loader = unittest.TestLoader()
    test_cases: List[unittest.TestCase] = []

    for mod_name in ADVERSARIAL_MODULES:
        suite = loader.loadTestsFromName(mod_name)
        for test in _flatten_suite(suite):
            test_cases.append(test)

    return test_cases


def _flatten_suite(suite: unittest.TestSuite) -> List[unittest.TestCase]:
    cases: List[unittest.TestCase] = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            cases.extend(_flatten_suite(item))
        elif isinstance(item, unittest.TestCase):
            cases.append(item)
    return cases


def run_adversarial_red_team_e2e() -> Dict[str, Any]:
    """Execute complete adversarial evaluation and return receipt dictionary."""
    test_cases = load_all_adversarial_tests()
    collector = AdversarialTestProgressCollector(total_tests=len(test_cases))

    overall_start = time.time()

    for test in test_cases:
        test_id = test.id()
        t_start = time.time()
        result = unittest.TestResult()
        test.run(result)
        duration = time.time() - t_start

        if result.wasSuccessful():
            collector.record_test(test_id, "PASS", duration)
        elif result.failures:
            err_msg = "\n".join(str(f[1]) for f in result.failures)
            collector.record_test(test_id, "FAIL", duration, err_msg)
        elif result.errors:
            err_msg = "\n".join(str(e[1]) for e in result.errors)
            collector.record_test(test_id, "ERROR", duration, err_msg)

    collector.close()
    overall_elapsed = round(time.time() - overall_start, 4)

    receipt = {
        "contract": "urn:nee:dual-pipeline:adversarial-red-team-e2e-receipt:1.0.0",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "PASS" if collector.failed_count == 0 and collector.error_count == 0 else "FAIL",
        "total_tests": len(test_cases),
        "passed_tests": collector.passed_count,
        "failed_tests": collector.failed_count,
        "error_tests": collector.error_count,
        "elapsed_seconds": overall_elapsed,
        "suites_covered": {
            "pipeline_a_acoustic_intake": sum(1 for r in collector.results if "pipeline_a" in r["test_name"]),
            "pipeline_b_extraction_hygiene": sum(1 for r in collector.results if "pipeline_b" in r["test_name"]),
            "pipeline_c_retrieval_and_cache": sum(1 for r in collector.results if "pipeline_c" in r["test_name"]),
            "pipeline_d_decision_and_hitl": sum(1 for r in collector.results if "pipeline_d" in r["test_name"]),
            "cross_cutting_governance_c19": sum(1 for r in collector.results if "governance" in r["test_name"]),
        },
        "itemized_test_results": collector.results,
    }

    reports_dir = REPO_ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = reports_dir / "adversarial_red_team_e2e_receipt.json"

    temp_path = reports_dir / "adversarial_red_team_e2e_receipt.json.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(receipt, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    temp_path.replace(receipt_path)

    return receipt


def main() -> int:
    print("=" * 80)
    print("  ADVERSARIAL RED TEAM END-TO-END ORCHESTRATOR")
    print("  Target Suites: Pipeline A, B, C, D & Cross-Cutting Governance")
    print("=" * 80)

    receipt = run_adversarial_red_team_e2e()

    print("\n" + "=" * 80)
    print("  EVALUATION SUMMARY")
    print("=" * 80)
    print(f"  Status:          {receipt['status']}")
    print(f"  Total Tests:     {receipt['total_tests']}")
    print(f"  Passed:          {receipt['passed_tests']}")
    print(f"  Failed:          {receipt['failed_tests']}")
    print(f"  Errors:          {receipt['error_tests']}")
    print(f"  Elapsed Time:    {receipt['elapsed_seconds']}s")
    print("  Suite Breakdown:")
    for suite, cnt in receipt["suites_covered"].items():
        print(f"    - {suite:32s}: {cnt} tests")
    print("  Receipt:         reports/adversarial_red_team_e2e_receipt.json")
    print("=" * 80)

    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
