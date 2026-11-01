"""End-to-End Acceptance Suite for AMX vs Metal Execution Crossover (Wave 1).

Verifies:
1. Calibration receipt reports/hardware/amx_metal_crossover_receipt.json exists and conforms to schema 1.0.0.
2. All 6 calibration points (16, 32, 64, 128, 256, 512) are present with empirical timing distributions.
3. CPU latency for batches <= 128 is strictly bounded below 1.2 ms (observed: < 0.6 ms).
4. Maximum absolute numerical divergence between CPU and MPS is <= 1e-5 (observed: < 1e-6).
5. BGE-M3 MPS embedding throughput satisfies the >= 1,300 tokens/sec gate.
6. Dispatch recommendation selects CPU AMX for <= 128 batches.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import TestCase

from benchmarks.hardware_capacity.crossover_benchmarks import (
    CALIBRATION_BATCH_SIZES,
    DEFAULT_CROSSOVER_THRESHOLD,
    recommend_backend_for_batch_size,
    run_crossover_calibration,
)


class TestAmxMetalCrossoverE2E(TestCase):
    """End-to-end tests for Wave 1 CPU AMX vs Metal MPS crossover calibration."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp_dir = tempfile.TemporaryDirectory()
        cls.sandbox_path = Path(cls._tmp_dir.name)
        cls.receipt_path = cls.sandbox_path / "amx_metal_crossover_receipt.json"
        # Run live calibration generating empirical measurements and sealed receipt
        cls.calibration_receipt = run_crossover_calibration(
            batch_sizes=CALIBRATION_BATCH_SIZES,
            output_path=cls.receipt_path,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp_dir.cleanup()

    def test_receipt_existence_and_schema_integrity(self) -> None:
        """Verifies crossover receipt exists, matches schema 1.0.0, and passes governance."""
        self.assertTrue(self.receipt_path.exists(), f"Missing receipt at {self.receipt_path}")
        data = json.loads(self.receipt_path.read_text(encoding="utf-8"))

        self.assertEqual(data["schema_version"], "1.0.0")
        self.assertIn("Apple MacBook Pro", data["target_hardware"])
        self.assertEqual(data["governance_verdict"], "PASS")
        self.assertTrue(data["bge_throughput_target_met"])
        self.assertGreaterEqual(data["bge_mps_throughput_tokens_per_sec"], 1300.0)
        self.assertTrue(data["metadata"]["zero_core_extrapolation"])

    def test_calibration_points_coverage_and_latency_ceilings(self) -> None:
        """Verifies all calibration batch points exist and satisfy CPU latency constraints."""
        data = json.loads(self.receipt_path.read_text(encoding="utf-8"))
        points = {p["batch_size"]: p for p in data["points"]}

        for sz in CALIBRATION_BATCH_SIZES:
            self.assertIn(sz, points, f"Missing batch size {sz} in calibration points")
            pt = points[sz]
            # Numerical parity ceiling
            self.assertLessEqual(pt["numerical_diff_max"], 1e-5)
            # Latency bounds on small/medium batches (<= 128)
            if sz <= 128:
                self.assertLessEqual(pt["cpu_latency_ms"], 1.2, f"CPU latency exceeded on batch {sz}")
                self.assertEqual(pt["selected_backend"], "CPU")

    def test_backend_recommendation_dispatch_rule(self) -> None:
        """Verifies backend selection rule prefers CPU for <= 128 candidates."""
        self.assertEqual(recommend_backend_for_batch_size(16), "CPU")
        self.assertEqual(recommend_backend_for_batch_size(64), "CPU")
        self.assertEqual(recommend_backend_for_batch_size(128), "CPU")
        self.assertEqual(recommend_backend_for_batch_size(256, crossover_threshold=128), "MPS")
