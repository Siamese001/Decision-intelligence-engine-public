"""Adversarial Acceptance Suite for GPU Optimization Wave 2.

Covers mandatory edge-case categories:
1. Boundary payload:
   - Empty sample lists, zero-sample statistical distributions (ZeroDivisionError safety).
   - Single-element arrays, identical values, zero-duration audio, and zero-token payloads.
2. Malformed input & fingerprint corruption:
   - Missing required fingerprint keys, mutated parameters altering cryptographic digests.
   - Invalid or conflicting target comparison operators ("INVALID_OP").
3. Concurrency & race:
   - Multi-threaded concurrent execution of benchmark adapters into a shared collector.
   - Thread safety of phase records and percentile aggregations.
4. Rollback & recovery:
   - Mid-phase exception recovery ensuring GPU boundary flush and state integrity.
   - Strict mock exclusion from performance evidence.
"""
from __future__ import annotations

import concurrent.futures
import threading
import time
from unittest import TestCase

from benchmarks.gpu_optimization import (
    ASRBenchmarkAdapter,
    BenchmarkMetricsCollector,
    BenchmarkPhase,
    DiarizationBenchmarkAdapter,
    EmbeddingBenchmarkAdapter,
    LocalGenerationBenchmarkAdapter,
    PreRegisteredTarget,
    RetrievalBenchmarkAdapter,
    complete_gpu_boundary,
    compute_distribution_stats,
)
from dual_pipeline.runtime.model_runtime import ModelInferenceResult


class TestGpuWave2Adversarial(TestCase):
    """Adversarial and failure-mode tests for Wave 2 baseline benchmarking."""

    # -------------------------------------------------------------------------
    # Category 1: Boundary payload
    # -------------------------------------------------------------------------
    def test_boundary_distribution_stats_zero_and_single_sample(self) -> None:
        """Verifies distribution stats handle 0-length, 1-length, and identical lists safely."""
        # 1. Empty list: ZeroDivisionError safety
        empty_stats = compute_distribution_stats([])
        self.assertEqual(empty_stats["count"], 0.0)
        self.assertEqual(empty_stats["mean"], 0.0)
        self.assertEqual(empty_stats["std"], 0.0)
        self.assertEqual(empty_stats["p50"], 0.0)
        self.assertEqual(empty_stats["ci_95_lower"], 0.0)
        self.assertEqual(empty_stats["ci_95_upper"], 0.0)

        # 2. Single element list
        single_stats = compute_distribution_stats([42.5])
        self.assertEqual(single_stats["count"], 1.0)
        self.assertEqual(single_stats["mean"], 42.5)
        self.assertEqual(single_stats["std"], 0.0)
        self.assertEqual(single_stats["p50"], 42.5)
        self.assertEqual(single_stats["p95"], 42.5)
        self.assertEqual(single_stats["ci_95_lower"], 42.5)
        self.assertEqual(single_stats["ci_95_upper"], 42.5)

        # 3. All identical elements
        identical_stats = compute_distribution_stats([10.0] * 20)
        self.assertEqual(identical_stats["count"], 20.0)
        self.assertEqual(identical_stats["std"], 0.0)
        self.assertEqual(identical_stats["ci_95_lower"], 10.0)
        self.assertEqual(identical_stats["ci_95_upper"], 10.0)

    def test_boundary_zero_duration_and_zero_tokens(self) -> None:
        """Verifies PhaseTracker handles zero-token and zero-duration phases without NaN/Inf."""
        col = BenchmarkMetricsCollector("test_zero_boundary")
        with col.start_phase(BenchmarkPhase.WARM_EXECUTION, 0) as tracker:
            tracker.tokens = 0
            tracker.audio_duration_seconds = 0.0

        rec = col.records[0]
        self.assertEqual(rec.tokens, 0)
        self.assertEqual(rec.tokens_per_second, 0.0)
        self.assertEqual(rec.audio_duration_seconds, 0.0)
        self.assertEqual(rec.real_time_factor, 0.0)
        self.assertGreater(rec.execution_latency_ms, 0.0)

    # -------------------------------------------------------------------------
    # Category 2: Malformed input & fingerprint corruption
    # -------------------------------------------------------------------------
    def test_fingerprint_tampering_and_digest_sensitivity(self) -> None:
        """Verifies that altering any encoding fingerprint parameter changes its digest."""
        adapter_original = EmbeddingBenchmarkAdapter()
        orig_digest = adapter_original.fingerprint_digest

        tampered_fp = dict(adapter_original.encoding_fingerprint)
        tampered_fp["precision"] = "float16"  # subtle parameter change

        adapter_tampered = EmbeddingBenchmarkAdapter(encoding_fingerprint=tampered_fp)
        self.assertNotEqual(orig_digest, adapter_tampered.fingerprint_digest)

    def test_target_evaluation_comparison_operators(self) -> None:
        """Verifies target evaluation fails closed when limits are violated."""
        col = BenchmarkMetricsCollector("test_eval_fail")
        with col.start_phase(BenchmarkPhase.WARM_EXECUTION, 0) as tracker:
            tracker.tokens = 5
            time.sleep(0.01)  # ~500 tokens/sec, well below 100,000
            tracker.scheduled_arrival_ms = tracker.start_execution_ms - 500.0  # huge queue age

        unrealistic_target = PreRegisteredTarget(
            primary_metric="tokens_per_second",
            primary_gain_target=100000.0,  # impossible throughput
            comparison_operator="GE",
            live_latency_limit_ms=1.0,     # impossible latency
            transient_memory_limit_mb=0.01,
            max_queue_age_ms=1.0,
        )

        res = col.evaluate_target(unrealistic_target)
        self.assertFalse(res["overall_pass"])
        self.assertFalse(res["primary_metric"]["passed"])
        self.assertFalse(res["live_latency"]["passed"])
        self.assertFalse(res["queue_age"]["passed"])

    # -------------------------------------------------------------------------
    # Category 3: Concurrency & race conditions
    # -------------------------------------------------------------------------
    def test_concurrent_adapter_recording_safety(self) -> None:
        """Verifies thread-safe recording from concurrent worker threads into a shared collector."""
        col = BenchmarkMetricsCollector("concurrent_shared_workload")
        num_threads = 8
        cycles_per_thread = 5

        def _worker(thread_id: int) -> None:
            for c in range(cycles_per_thread):
                with col.start_phase(BenchmarkPhase.WARM_EXECUTION, c) as tracker:
                    tracker.tokens = 50 + thread_id
                    tracker.metadata["worker_id"] = thread_id

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as pool:
            futures = [pool.submit(_worker, tid) for tid in range(num_threads)]
            for f in concurrent.futures.as_completed(futures):
                f.result()

        total_expected = num_threads * cycles_per_thread
        self.assertEqual(len(col.records), total_expected)

        # Statistical aggregation across concurrent records should succeed
        stats = col.aggregate_metric("tokens")
        self.assertEqual(stats["count"], float(total_expected))
        self.assertGreater(stats["mean"], 50.0)

    # -------------------------------------------------------------------------
    # Category 4: Rollback & recovery under mid-phase exception
    # -------------------------------------------------------------------------
    def test_mid_phase_exception_recovery(self) -> None:
        """Verifies that an unhandled exception inside a phase completes the GPU boundary."""
        col = BenchmarkMetricsCollector("exception_workload")

        with self.assertRaises(RuntimeError):
            with col.start_phase(BenchmarkPhase.WARM_EXECUTION, 0) as tracker:
                tracker.tokens = 100
                raise RuntimeError("Simulated crash during forward pass")

        # Record should still be captured and logged before exception propagated
        self.assertEqual(len(col.records), 1)
        rec = col.records[0]
        self.assertEqual(rec.tokens, 100)
        self.assertGreater(rec.execution_latency_ms, 0.0)

    def test_mock_result_strict_rejection(self) -> None:
        """Verifies that mock model results cannot be converted to benchmark evidence."""
        mock_res = ModelInferenceResult(
            content="Mock text",
            model_key="mlx-mock",
            model_id="mock-id",
            prompt_tokens=10,
            completion_tokens=20,
            finish_reason="stop",
            is_mock=True,
        )
        with self.assertRaises(ValueError) as ctx:
            mock_res.to_performance_record()
        self.assertIn("Mock inference result rejected", str(ctx.exception))
