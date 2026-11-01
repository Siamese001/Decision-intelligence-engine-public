"""
tests/adversarial/test_circuit_breaker_adversarial.py

Adversarial edge-case tests for Agent 5 CandidateCircuitBreaker:
Covering boundary payloads, malformed inputs, multi-threaded races,
overflow/exhaustion, invariant bypass attempts, and the LENS production incident regression.
"""
from __future__ import annotations

import concurrent.futures
import threading
import unittest
from typing import Any, Dict

from dual_pipeline.token_optimization.circuit_breaker import CandidateCircuitBreaker


class CustomUnhashableObject:
    def __init__(self, val: str) -> None:
        self.val = val

    def __repr__(self) -> str:
        return f"<CustomUnhashable {self.val}>"


class CircuitBreakerAdversarialTests(unittest.TestCase):
    """Adversarial test suite for candidate circuit breaker."""

    def test_01_boundary_payload_nulls_and_empty_inputs(self) -> None:
        """Category: Boundary payload - None, empty strings, null structures, zero thresholds."""
        # Test zero or negative threshold defaults safely to 1
        cb = CandidateCircuitBreaker(max_consecutive_failures=0)
        self.assertEqual(cb.max_consecutive_failures, 1)

        # None error payload produces deterministic hash
        h_none = cb.compute_error_hash(None)
        self.assertTrue(isinstance(h_none, str) and len(h_none) == 64)

        # Empty structures produce stable hashes
        h_dict = cb.compute_error_hash({})
        h_list = cb.compute_error_hash([])
        self.assertTrue(isinstance(h_dict, str))
        self.assertTrue(isinstance(h_list, str))

        # None candidate produces deterministic candidate hash
        c_none = cb.compute_candidate_hash(None)
        self.assertTrue(isinstance(c_none, str) and len(c_none) == 64)

    def test_02_malformed_input_unhashable_and_nested_types(self) -> None:
        """Category: Malformed input - unhashable objects, circular references, non-string keys."""
        cb = CandidateCircuitBreaker(max_consecutive_failures=2)

        # Unhashable object should be converted via string representation without throwing
        obj = CustomUnhashableObject("corrupt_ast_node")
        h1 = cb.compute_error_hash(obj)
        h2 = cb.compute_error_hash(obj)
        self.assertEqual(h1, h2)

        # Error payload with non-serializable objects inside dict
        complex_err = {
            "status": "FAIL",
            "nested_unhashable": CustomUnhashableObject("nested"),
            123: "non_string_key",
        }
        h_complex = cb.compute_error_hash(complex_err)
        self.assertTrue(isinstance(h_complex, str) and len(h_complex) == 64)

    def test_03_concurrency_and_race_multi_threaded_failures(self) -> None:
        """Category: Concurrency & race - parallel worker execution updating failure state."""
        cb = CandidateCircuitBreaker(max_consecutive_failures=100)
        lock = threading.Lock()
        trip_events: list[bool] = []

        def worker(err_id: int) -> None:
            # Alternate between two errors to test state churn
            err = f"Threaded failure {err_id % 2}"
            with lock:
                tripped = cb.record_failure(err)
                trip_events.append(tripped)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker, i) for i in range(50)]
            concurrent.futures.wait(futures)

        self.assertEqual(cb.total_failures, 50)
        self.assertEqual(len(cb.failure_history), 50)

    def test_04_overflow_and_exhaustion_rapid_retry_loops(self) -> None:
        """Category: Overflow & exhaustion - rapid retry loops with thousands of events."""
        cb = CandidateCircuitBreaker(max_consecutive_failures=2)

        # First failure
        self.assertFalse(cb.record_failure("ErrA"))
        # Second failure trips
        self.assertTrue(cb.record_failure("ErrA"))
        self.assertTrue(cb.is_tripped())

        # Further calls continue returning True (tripped state locked)
        for i in range(1000):
            res = cb.record_failure("ErrA")
            self.assertTrue(res)

        self.assertEqual(cb.total_failures, 1002)
        self.assertTrue(cb.is_tripped())

    def test_05_invariant_bypass_attempt_state_recovery(self) -> None:
        """Category: Invariant bypass attempt - verify record_success does not un-trip a tripped breaker."""
        cb = CandidateCircuitBreaker(max_consecutive_failures=2)
        cb.record_failure("Fatal")
        cb.record_failure("Fatal")
        self.assertTrue(cb.is_tripped())

        # Attempting to bypass by calling record_success()
        cb.record_success()
        self.assertTrue(cb.is_tripped(), "record_success must not silently reset a tripped circuit breaker.")

        # Only explicit reset() restores operational state
        cb.reset()
        self.assertFalse(cb.is_tripped())
        self.assertEqual(cb.consecutive_failures, 0)

    def test_06_exact_production_regression_lens_session_futile_loop(self) -> None:
        """Category: Exact production regression - Oct 7 LENS incident 37-attempt loop."""
        # In LENS session: candidate failed with identical schema violation 37 times
        cb = CandidateCircuitBreaker(max_consecutive_failures=2)
        lens_error = {
            "status": "FAIL",
            "qa_failures": ["SchemaValidationError: 'entities' array contains missing required 'id' attribute"],
            "judge_failures": ["GroundingFloorGate: extraction contains ungrounded entity IDs"],
        }

        attempts_executed = 0
        max_loop_limit = 37  # Replicating original attempt count

        for attempt in range(1, max_loop_limit + 1):
            attempts_executed += 1
            tripped = cb.record_failure(lens_error)
            if tripped:
                break

        # Must terminate in exactly 2 attempts instead of 37!
        self.assertEqual(attempts_executed, 2, "Circuit breaker must halt doomed loop at attempt 2.")
        self.assertTrue(cb.is_tripped())
        self.assertIn("identical consecutive candidate failures", cb.trip_reason)


if __name__ == "__main__":
    unittest.main()
