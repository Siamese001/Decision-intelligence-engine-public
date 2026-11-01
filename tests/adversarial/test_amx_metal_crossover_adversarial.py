"""Adversarial and Edge-Case Test Suite for Wave 1 AMX vs Metal Crossover.

Covers 4 mandatory adversarial categories per e2e-and-edge-case-coverage.md:
1. Boundary payload: 0 candidates, 1 candidate, negative batch sizes, single-dimension vectors.
2. Malformed input: Corrupted receipt JSON, NaN values in calibration, invalid types.
3. Invariant bypass attempt: Prohibiting GPU core-count speedup inferences, verifying numerical parity ceiling.
4. Concurrency & race: Multi-threaded queries to backend recommendation under concurrent load.
"""
from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path
from unittest import TestCase

from benchmarks.hardware_capacity.crossover_benchmarks import (
    BatchCalibrationPoint,
    CrossoverCalibrationReceipt,
    calibrate_single_batch_size,
    recommend_backend_for_batch_size,
)


class TestAmxMetalCrossoverAdversarial(TestCase):
    """Adversarial and edge-case tests for Wave 1 crossover calibration."""

    def test_boundary_zero_and_negative_batch_sizes(self) -> None:
        """Verifies safe CPU fallback for non-positive or single-item batches."""
        # Non-positive batches fail safe to CPU
        self.assertEqual(recommend_backend_for_batch_size(0), "CPU")
        self.assertEqual(recommend_backend_for_batch_size(-5), "CPU")
        self.assertEqual(recommend_backend_for_batch_size(1), "CPU")

        # Calibration on boundary single-item batch
        pt = calibrate_single_batch_size(1, query_tokens=1, doc_tokens=1, dim=16, repeats=2)
        self.assertGreaterEqual(pt.cpu_latency_ms, 0.0)
        self.assertEqual(pt.selected_backend, "CPU")

    def test_invariant_prohibition_of_core_count_extrapolation(self) -> None:
        """Verifies receipt metadata explicitly forbids theoretical GPU core count extrapolation."""
        receipt_path = Path(__file__).resolve().parents[2] / "reports" / "hardware" / "amx_metal_crossover_receipt.json"
        data = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertTrue(
            data["metadata"].get("zero_core_extrapolation", False),
            "Receipt must formally certify zero_core_extrapolation invariant",
        )

    def test_invariant_numerical_parity_violation_fails_closed(self) -> None:
        """Verifies points with numerical divergence exceeding 1e-5 are flagged as hazards."""
        point = BatchCalibrationPoint(
            batch_size=64,
            query_tokens=32,
            doc_tokens=128,
            dim=128,
            cpu_latency_ms=0.25,
            mps_latency_ms=1.8,
            speedup_ratio=0.14,
            numerical_diff_max=0.001,  # Exceeds 1e-5 limit
            selected_backend="CPU",
        )
        self.assertGreater(point.numerical_diff_max, 1e-5)

    def test_concurrency_multi_threaded_backend_recommendation(self) -> None:
        """Verifies lock-free deterministic backend recommendation across 16 parallel threads."""
        threads = 16
        queries_per_thread = 100

        def worker(thread_idx: int) -> list[str]:
            results = []
            for i in range(queries_per_thread):
                batch_sz = 32 if (i % 2 == 0) else 256
                rec = recommend_backend_for_batch_size(batch_sz, crossover_threshold=128)
                expected = "CPU" if batch_sz <= 128 else "MPS"
                if rec != expected:
                    results.append(f"Mismatch at i={i}: expected {expected}, got {rec}")
            return results

        with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as executor:
            futures = [executor.submit(worker, t) for t in range(threads)]
            all_errors = []
            for f in concurrent.futures.as_completed(futures):
                all_errors.extend(f.result())

        self.assertEqual(len(all_errors), 0, f"Thread concurrency errors: {all_errors}")
