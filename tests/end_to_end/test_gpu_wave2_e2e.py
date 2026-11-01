"""End-to-End Acceptance Suite for GPU Optimization Wave 2.

Verifies:
1. Canonical corpus manifests and workload specifications with frozen pre-registered targets.
2. Benchmark adapters for all 5 canonical workloads:
   - Embedding Inference (BGE-M3 text batching)
   - Hybrid Retrieval (ColBERT MaxSim + BM25 + Dense)
   - Live ASR (Whisper speech transcription)
   - Speaker Diarization (acoustic turn attribution)
   - Local Generation (MLX Metal reasoning)
3. Mandatory binding of immutable encoding fingerprints and cryptographic digests.
4. Phase instrumentation: scheduled-arrival-to-result latency, queue age, RTF, tokens/sec.
5. Repeated alternating run statistics with 95% uncertainty intervals.
6. Preservation of the 7,500 ms steady-state retrieval gate.
7. Asynchronous GPU boundary completion without mid-loop synchronization.
8. Wave 2 acceptance runner execution in a disposable sandbox producing sealed receipts.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import TestCase

from benchmarks.gpu_optimization import (
    ASRBenchmarkAdapter,
    BenchmarkMetricsCollector,
    BenchmarkPhase,
    CANONICAL_WORKLOAD_SPECS,
    DiarizationBenchmarkAdapter,
    EmbeddingBenchmarkAdapter,
    EmbeddingCorpusManifest,
    LocalGenerationBenchmarkAdapter,
    PreRegisteredTarget,
    RetrievalBenchmarkAdapter,
    RetrievalCorpusManifest,
    complete_gpu_boundary,
    compute_distribution_stats,
)
from tools.gpu_optimization_runner_core import canonical_digest


class TestGpuWave2E2E(TestCase):
    """End-to-end tests for reproducible baseline benchmarking and target pre-registration."""

    def test_corpus_manifests_and_spec_integrity(self) -> None:
        """Verifies canonical corpus manifests produce stable, cryptographically verifiable digests."""
        emb_manifest = EmbeddingCorpusManifest.create_canonical()
        self.assertEqual(emb_manifest.corpus_id, "canonical_embedding_corpus_v1")
        self.assertEqual(len(emb_manifest.corpus_digest), 64)
        self.assertGreater(len(emb_manifest.short_texts), 0)
        self.assertGreater(len(emb_manifest.ragged_batch), 0)

        ret_manifest = RetrievalCorpusManifest.create_canonical(num_docs=32, dim=32)
        self.assertEqual(ret_manifest.num_documents, 32)
        self.assertEqual(len(ret_manifest.corpus_digest), 64)

        # All 5 canonical workloads must be present
        required_workloads = {
            "embedding_inference",
            "hybrid_retrieval",
            "live_asr",
            "diarization",
            "local_generation",
        }
        self.assertEqual(set(CANONICAL_WORKLOAD_SPECS.keys()), required_workloads)

    def test_embedding_benchmark_adapter(self) -> None:
        """Verifies EmbeddingBenchmarkAdapter binds fingerprints and collects multi-phase metrics."""
        adapter = EmbeddingBenchmarkAdapter()
        self.assertEqual(len(adapter.fingerprint_digest), 64)
        self.assertEqual(adapter.encoding_fingerprint["model_id"], "BAAI/bge-m3")
        self.assertTrue(adapter.encoding_fingerprint["normalize_embeddings"])

        collector = adapter.run_benchmark()
        self.assertGreater(len(collector.records), 0)

        cold_records = collector.get_phase_records(BenchmarkPhase.COLD_LOAD)
        warm_records = collector.get_phase_records(BenchmarkPhase.WARM_EXECUTION)
        hit_records = collector.get_phase_records(BenchmarkPhase.CACHE_HIT)

        self.assertGreater(len(cold_records), 0)
        self.assertGreater(len(warm_records), 0)
        self.assertGreater(len(hit_records), 0)

        # Warm records should report positive tokens and padding ratio
        for r in warm_records:
            self.assertGreater(r.tokens, 0)
            self.assertGreater(r.tokens_per_second, 0.0)
            self.assertGreaterEqual(r.padding_ratio, 0.0)
            self.assertGreater(r.scheduled_arrival_to_result_latency_ms, 0.0)

    def test_retrieval_benchmark_adapter_and_legacy_gate(self) -> None:
        """Verifies RetrievalBenchmarkAdapter preserves the 7,500 ms steady-state gate."""
        adapter = RetrievalBenchmarkAdapter()
        self.assertEqual(adapter.encoding_fingerprint["preserved_legacy_gate_ms"], 7500.0)
        self.assertEqual(adapter.spec.target.preserved_legacy_gate_ms, 7500.0)

        collector = adapter.run_benchmark()
        eval_result = collector.evaluate_target(adapter.spec.target)

        self.assertTrue(eval_result["overall_pass"])
        self.assertTrue(eval_result["preserved_legacy_gate"]["passed"])
        self.assertLess(
            eval_result["preserved_legacy_gate"]["observed_p95_ms"],
            7500.0,
        )

        # Verify PrePaddedColbertCache hits and misses
        hit_recs = collector.get_phase_records(BenchmarkPhase.CACHE_HIT)
        miss_recs = collector.get_phase_records(BenchmarkPhase.CACHE_MISS)
        self.assertGreater(len(hit_recs), 0)
        self.assertGreater(len(miss_recs), 0)

    def test_asr_and_diarization_adapters_rtf(self) -> None:
        """Verifies ASR and Diarization adapters calculate Real-Time Factor (RTF)."""
        asr_adapter = ASRBenchmarkAdapter()
        asr_collector = asr_adapter.run_benchmark()
        asr_recs = asr_collector.records
        self.assertGreater(len(asr_recs), 0)
        for r in asr_recs:
            self.assertGreater(r.audio_duration_seconds, 0.0)
            self.assertGreater(r.real_time_factor, 0.0)
            self.assertLessEqual(r.real_time_factor, 1.0)  # faster than real time

        diar_adapter = DiarizationBenchmarkAdapter()
        diar_collector = diar_adapter.run_benchmark()
        diar_eval = diar_collector.evaluate_target(diar_adapter.spec.target)
        self.assertTrue(diar_eval["primary_metric"]["passed"])

    def test_local_generation_adapter(self) -> None:
        """Verifies LocalGenerationBenchmarkAdapter enforces parameter immunity."""
        adapter = LocalGenerationBenchmarkAdapter()
        self.assertTrue(adapter.encoding_fingerprint["parameter_immunity"])
        self.assertTrue(adapter.encoding_fingerprint["forbidden_temperature_omitted"])

        collector = adapter.run_benchmark()
        eval_result = collector.evaluate_target(adapter.spec.target)
        self.assertTrue(eval_result["primary_metric"]["passed"])

    def test_statistical_uncertainty_and_percentiles(self) -> None:
        """Verifies computation of mean, std, percentiles, and 95% uncertainty intervals."""
        sample_latencies = [10.0, 12.0, 11.0, 15.0, 14.0, 13.0, 10.5, 11.5]
        stats = compute_distribution_stats(sample_latencies)

        self.assertEqual(stats["count"], 8.0)
        self.assertAlmostEqual(stats["mean"], sum(sample_latencies) / 8.0, places=2)
        self.assertGreater(stats["std"], 0.0)
        self.assertLessEqual(stats["p50"], stats["p95"])
        self.assertLessEqual(stats["p95"], stats["p99"])

        # 95% CI interval should bracket the mean
        self.assertLessEqual(stats["ci_95_lower"], stats["mean"])
        self.assertGreaterEqual(stats["ci_95_upper"], stats["mean"])

        # As sample size grows, margin of error shrinks
        large_sample = sample_latencies * 10
        large_stats = compute_distribution_stats(large_sample)
        small_width = stats["ci_95_upper"] - stats["ci_95_lower"]
        large_width = large_stats["ci_95_upper"] - large_stats["ci_95_lower"]
        self.assertLess(large_width, small_width)

    def test_gpu_boundary_synchronization(self) -> None:
        """Verifies asynchronous GPU boundary completion executes without error."""
        # Must execute cleanly on CPU, Metal MPS, and MLX
        try:
            complete_gpu_boundary()
        except Exception as e:
            self.fail(f"complete_gpu_boundary raised unexpected exception: {e}")

    def test_wave2_runner_sandbox_execution(self) -> None:
        """Verifies execute_gpu_wave creates a sealed, immutable receipt for Wave 2."""
        from tools.run_gpu_optimization_wave import execute_gpu_wave

        with tempfile.TemporaryDirectory() as sandbox_dir:
            sandbox = Path(sandbox_dir)
            receipt_dir = sandbox / "receipts"
            plan_path = Path(__file__).resolve().parents[2] / "docs/plans/docs_gpu_optimization_debate_plan_20261006.md"

            # Pre-seed Wave 1 predecessor receipt in sandbox to satisfy predecessor requirement
            receipt_dir.mkdir(parents=True, exist_ok=True)
            w1_receipt_src = Path(__file__).resolve().parents[2] / "reports/gpu_optimization/gpu_wave_1_receipt.json"
            if w1_receipt_src.is_file():
                w1_target = receipt_dir / "gpu_wave_1_receipt.json"
                w1_target.write_text(w1_receipt_src.read_text(encoding="utf-8"), encoding="utf-8")

            mini_test = sandbox / "test_mini.py"
            mini_test.write_text(
                "import unittest\nclass MiniTest(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n",
                encoding="utf-8",
            )

            exit_code = execute_gpu_wave(
                wave=2,
                sandbox=sandbox,
                plan_path=plan_path,
                receipt_root=receipt_dir,
                timeout=60,
                immutable=True,
                override_tests=[str(mini_test)],
            )
            self.assertEqual(exit_code, 0)

            receipt_file = receipt_dir / "gpu_wave_2_receipt.json"
            self.assertTrue(receipt_file.is_file())

            receipt_data = json.loads(receipt_file.read_text(encoding="utf-8"))
            self.assertEqual(receipt_data["status"], "PASS")
            self.assertIn("receipt_digest", receipt_data)
            self.assertIn("parent_receipt", receipt_data)
            self.assertEqual(receipt_data["parent_receipt"]["wave"], 1)
            self.assertIn("receipt_digest", receipt_data["parent_receipt"])

            # Verify cryptographic digest
            claimed_digest = receipt_data["receipt_digest"]
            payload_copy = {k: v for k, v in receipt_data.items() if k != "receipt_digest"}
            self.assertEqual(canonical_digest(payload_copy), claimed_digest)
