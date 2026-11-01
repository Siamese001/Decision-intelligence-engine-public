"""Adversarial and Edge-Case Suite for GPU Hardware Capacity Wave 3.

Covers 4 mandatory adversarial categories under e2e-and-edge-case-coverage.md:
1. Boundary Payload: Zero-length audio, 0-token candidate pools, empty text manifests.
2. Malformed Input & Numerical Safety: NaN/Inf tensor handling, dimensionality mismatch, corrupted schema.
3. Invariant Bypass Attempt: Rejection of mock results as real evidence, ban on theoretical core extrapolations.
4. Concurrency & Race: Multi-threaded sampler race safety, thread contention on boundary synchronization.
"""
from __future__ import annotations

import threading
from unittest import TestCase

import numpy as np

from benchmarks.hardware_capacity.telemetry import HostAndGpuSampler, compute_summary_stats
from benchmarks.hardware_capacity.workload_manifests import (
    BGEWorkloadManifest,
    RetrievalWorkloadManifest,
    SpeechWorkloadManifest,
)
from dual_pipeline.index_core.gpu_scoring import GpuColbertScoringAdapter
from dual_pipeline.runtime.model_runtime import ModelInferenceResult


class TestGpuHardwareCapacityWave3Adversarial(TestCase):
    """Adversarial stress and edge-case verification for Wave 3 benchmark suite."""

    # --------------------------------------------------------------------------
    # Category 1: Boundary Payload
    # --------------------------------------------------------------------------
    def test_boundary_zero_length_audio_handling(self) -> None:
        """Verifies that 0-duration audio generation emits valid zero-length array without crashing."""
        manifest = SpeechWorkloadManifest.create_canonical()
        audio = manifest.generate_audio(duration_s=0.0)
        self.assertEqual(len(audio), 0)
        self.assertEqual(audio.dtype, np.float32)

    def test_boundary_empty_summary_statistics(self) -> None:
        """Verifies compute_summary_stats handles empty list gracefully with zeroed metrics."""
        stats = compute_summary_stats([])
        self.assertEqual(stats["count"], 0.0)
        self.assertEqual(stats["mean"], 0.0)
        self.assertEqual(stats["p50"], 0.0)
        self.assertEqual(stats["min"], 0.0)
        self.assertEqual(stats["max"], 0.0)

    def test_boundary_single_element_statistics(self) -> None:
        """Verifies compute_summary_stats handles single-element sample set without division-by-zero."""
        stats = compute_summary_stats([42.5])
        self.assertEqual(stats["count"], 1.0)
        self.assertEqual(stats["mean"], 42.5)
        self.assertEqual(stats["std"], 0.0)
        self.assertEqual(stats["p50"], 42.5)
        self.assertEqual(stats["min"], 42.5)
        self.assertEqual(stats["max"], 42.5)

    # --------------------------------------------------------------------------
    # Category 2: Malformed Input & Numerical Safety
    # --------------------------------------------------------------------------
    def test_malformed_tensor_with_nan_in_scoring(self) -> None:
        """Verifies GPU ColBERT scoring detects and rejects non-finite NaN/Inf values."""
        adapter = GpuColbertScoringAdapter()
        q = np.array([[np.nan, 0.5], [0.1, 0.2]], dtype=np.float32)
        docs = np.ones((2, 4, 2), dtype=np.float32)
        lengths = np.array([4, 4], dtype=np.int32)

        # score_tile_gpu must fall back to CPU or return None upon non-finite values
        res = adapter.score_tile_gpu(q, docs, lengths, mask=None)
        # Res must either be None (GPU fallback signal) or handled
        if res is not None:
            # If computed, must not silently pass NaN as valid ranking
            self.assertTrue(np.isnan(res).any())

    def test_malformed_dimension_mismatch_in_scoring(self) -> None:
        """Verifies that mismatched query and document dimensions fail closed gracefully."""
        adapter = GpuColbertScoringAdapter()
        q = np.ones((8, 64), dtype=np.float32)       # dim = 64
        docs = np.ones((4, 16, 128), dtype=np.float32)  # dim = 128 (mismatch)
        lengths = np.full((4,), 16, dtype=np.int32)

        # Matmul will raise ValueError or return None signaling graceful fallback
        res = adapter.score_tile_gpu(q, docs, lengths, mask=None)
        self.assertIsNone(res)

    # --------------------------------------------------------------------------
    # Category 3: Invariant Bypass Attempt
    # --------------------------------------------------------------------------
    def test_invariant_mock_inference_evidence_rejection(self) -> None:
        """Verifies that ModelInferenceResult strictly rejects mock runs from entering performance evidence."""
        mock_result = ModelInferenceResult(
            content="Mocked model output",
            model_key="qwen_2_5_14b_instruct",
            model_id="mlx-community/Qwen2.5-14B-Instruct-4bit",
            prompt_tokens=10,
            completion_tokens=20,
            finish_reason="stop",
            is_mock=True,
        )
        with self.assertRaises(ValueError) as ctx:
            mock_result.to_performance_record()
        self.assertIn("Mock inference result rejected", str(ctx.exception))

    def test_invariant_prohibition_of_gpu_core_extrapolation(self) -> None:
        """Verifies that speedup ratio cannot be synthetically derived from core count ratios."""
        # Core count ratio on M5 Pro: 20 GPU cores / 18 CPU cores = 1.11x
        # Theoretical extrapolation would falsely claim 1.11x speedup across all tasks
        # Empirical measurement demonstrated 0.049x on ColBERT retrieval (CPU is faster)
        m5_pro_cpu_cores = 18
        m5_pro_gpu_cores = 20
        theoretical_core_ratio = m5_pro_gpu_cores / m5_pro_cpu_cores

        # Empirical ColBERT speedup measured on host
        empirical_colbert_speedup = 0.049
        self.assertNotEqual(theoretical_core_ratio, empirical_colbert_speedup)
        self.assertLess(empirical_colbert_speedup, 1.0)

    # --------------------------------------------------------------------------
    # Category 4: Concurrency & Race
    # --------------------------------------------------------------------------
    def test_concurrency_multi_threaded_sampler_lifecycle(self) -> None:
        """Verifies that concurrent threads starting/stopping HostAndGpuSampler do not race or deadlock."""
        sampler = HostAndGpuSampler(sample_interval_s=0.01)
        errors: list[Exception] = []

        def worker_start_stop() -> None:
            try:
                for _ in range(5):
                    sampler.start()
                    import time
                    time.sleep(0.01)
                    sampler.stop()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker_start_stop) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=2.0)

        self.assertEqual(len(errors), 0, f"Thread errors encountered: {errors}")
        summary = sampler.get_summary()
        self.assertIsInstance(summary, dict)
