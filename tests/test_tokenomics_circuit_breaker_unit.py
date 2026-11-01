"""
tests/test_tokenomics_circuit_breaker_unit.py

Unit tests for Agent 5 CandidateCircuitBreaker and checkpoint memoizer adapter.
"""
from __future__ import annotations

import unittest
from typing import Any, Dict

from dual_pipeline.canonical_pipeline.checkpoint_memoizer import check_circuit_breaker
from dual_pipeline.token_optimization.circuit_breaker import (
    CandidateCircuitBreaker,
    CircuitBreakerTrippedError,
)


class DummyCandidate:
    def __init__(self, o1: str = "t", o2: str = "r", o3: str = "g") -> None:
        self.output_1 = o1
        self.output_2 = o2
        self.output_3 = o3


class DummyEvalResult:
    def __init__(self, authorized: bool, failures: list[str] | None = None) -> None:
        self.authoritative_write_authorized = authorized
        self.final_decision = {"status": "PASS" if authorized else "FAIL"}
        self.qa_report = {"failures": failures or []}
        self.judge_report = {"reasons": failures or []}


class TokenomicsCircuitBreakerUnitTests(unittest.TestCase):
    """Unit tests validating error hashing, threshold tracking, and tripping logic."""

    def test_01_error_hash_normalization_and_stability(self) -> None:
        cb = CandidateCircuitBreaker(max_consecutive_failures=2)
        err1 = {"qa_failures": ["AST entity mismatch"], "timestamp": 12345.0, "status": "FAIL"}
        err2 = {"status": "FAIL", "timestamp": 99999.0, "qa_failures": ["AST entity mismatch"]}

        h1 = cb.compute_error_hash(err1)
        h2 = cb.compute_error_hash(err2)
        self.assertEqual(h1, h2, "Error hashes must normalize timestamps and key ordering.")

    def test_02_consecutive_identical_failure_tracking(self) -> None:
        cb = CandidateCircuitBreaker(max_consecutive_failures=2)
        err_a = "Syntax error in candidate JSON"
        err_b = "Missing required summary section"

        # First failure of type A
        tripped = cb.record_failure(err_a)
        self.assertFalse(tripped)
        self.assertEqual(cb.consecutive_failures, 1)

        # Failure of type B resets consecutive count
        tripped = cb.record_failure(err_b)
        self.assertFalse(tripped)
        self.assertEqual(cb.consecutive_failures, 1)

    def test_03_circuit_breaker_trips_at_threshold(self) -> None:
        cb = CandidateCircuitBreaker(max_consecutive_failures=2)
        err = {"gate_failures": ["GroundingFloorGate failed: score 45 < 70"]}

        # Attempt 1: failure recorded, not tripped
        self.assertFalse(cb.record_failure(err))
        self.assertFalse(cb.is_tripped())
        self.assertEqual(cb.consecutive_failures, 1)

        # Attempt 2: identical failure, trips!
        tripped = cb.record_failure(err)
        self.assertTrue(tripped)
        self.assertTrue(cb.is_tripped())
        self.assertEqual(cb.consecutive_failures, 2)
        self.assertIn("identical consecutive", str(cb.trip_reason))

    def test_04_success_resets_consecutive_counter(self) -> None:
        cb = CandidateCircuitBreaker(max_consecutive_failures=2)
        err = "Temporary parsing glitch"

        cb.record_failure(err)
        self.assertEqual(cb.consecutive_failures, 1)

        cb.record_success()
        self.assertEqual(cb.consecutive_failures, 0)
        self.assertIsNone(cb.last_error_hash)

    def test_05_serialization_roundtrip(self) -> None:
        cb = CandidateCircuitBreaker(max_consecutive_failures=3)
        cb.record_failure("Err1")
        cb.record_failure("Err1")

        state = cb.to_dict()
        restored = CandidateCircuitBreaker.from_dict(state)

        self.assertEqual(restored.max_consecutive_failures, 3)
        self.assertEqual(restored.consecutive_failures, 2)
        self.assertEqual(restored.total_failures, 2)
        self.assertEqual(restored.last_error_hash, cb.last_error_hash)

    def test_06_check_circuit_breaker_adapter_integration(self) -> None:
        cb = CandidateCircuitBreaker(max_consecutive_failures=2)
        cand = DummyCandidate()
        fail_eval = DummyEvalResult(authorized=False, failures=["Dangling edge error"])

        # Attempt 1: not tripped
        halt = check_circuit_breaker(cb, fail_eval, cand, attempt=1, max_attempts=3)
        self.assertFalse(halt)
        self.assertFalse(cb.is_tripped())

        # Attempt 2: identical failure, trips!
        halt = check_circuit_breaker(cb, fail_eval, cand, attempt=2, max_attempts=3)
        self.assertTrue(halt)
        self.assertTrue(cb.is_tripped())


if __name__ == "__main__":
    unittest.main()
