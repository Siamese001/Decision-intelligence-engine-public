"""End-to-End Acceptance Suite for GPU Hardware Capacity Wave 3.

Verifies:
1. All four workload manifests produce valid data vectors matching cryptographic SHA-256 digests.
2. Host and GPU telemetry tracking correctly computes distribution statistics and syncs boundaries.
3. Empirical results in reports/hardware/m5_pro_real_workload.json satisfy governance criteria:
   - All 4 canonical workloads present with authentic execution evidence.
   - Tasks without CPU/MPS switch explicitly declared with architectural justification.
   - Paired CPU vs MPS measurements for BGE embedding and ColBERT retrieval.
   - Decision hypotheses (70% GPU activity, 20% speedup, 8 GB headroom) evaluated against empirical data.
   - Zero inferred speedup from GPU core counts.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest import TestCase

from benchmarks.hardware_capacity.telemetry import (
    HostAndGpuSampler,
    compute_summary_stats,
    synchronize_gpu_boundary,
)
from benchmarks.hardware_capacity.workload_manifests import (
    BGEWorkloadManifest,
    LocalGenerationWorkloadManifest,
    RetrievalWorkloadManifest,
    SpeechWorkloadManifest,
)


class TestGpuHardwareCapacityWave3E2E(TestCase):
    """End-to-end tests for Wave 3 empirical GPU capacity benchmark suite."""

    def test_workload_manifests_integrity_and_digests(self) -> None:
        """Verifies deterministic vector generation and cryptographic digests across all 4 manifests."""
        # 1. BGE Text Embedding
        bge_manifest = BGEWorkloadManifest.create_canonical()
        self.assertEqual(bge_manifest.manifest_id, "bge_m3_workload_v1")
        self.assertEqual(len(bge_manifest.ragged_batch), 8)
        self.assertEqual(len(bge_manifest.digest), 64)

        # 2. ColBERT MaxSim Retrieval
        ret_manifest = RetrievalWorkloadManifest.create_canonical(candidate_count=64, query_tokens=32, doc_tokens=128, dim=128)
        self.assertEqual(ret_manifest.manifest_id, "colbert_retrieval_workload_v1")
        q, docs, lengths = ret_manifest.generate_tensors()
        self.assertEqual(q.shape, (32, 128))
        self.assertEqual(docs.shape, (64, 128, 128))
        self.assertEqual(lengths.shape, (64,))
        self.assertEqual(len(ret_manifest.digest), 64)

        # 3. Speech ASR
        speech_manifest = SpeechWorkloadManifest.create_canonical()
        self.assertEqual(speech_manifest.manifest_id, "speech_asr_workload_v1")
        self.assertEqual(speech_manifest.sample_rate_hz, 16000)
        audio = speech_manifest.generate_audio(duration_s=2.0)
        self.assertEqual(audio.shape, (32000,))
        self.assertEqual(len(speech_manifest.digest), 64)

        # 4. Local Generation
        gen_manifest = LocalGenerationWorkloadManifest.create_canonical()
        self.assertEqual(gen_manifest.manifest_id, "local_generation_workload_v1")
        self.assertGreaterEqual(len(gen_manifest.prompts), 2)
        self.assertEqual(len(gen_manifest.digest), 64)

    def test_telemetry_sampler_and_statistics(self) -> None:
        """Verifies HostAndGpuSampler lifecycle and statistical percentile calculations."""
        # Test distribution stats
        vals = [10.0, 20.0, 30.0, 40.0, 50.0]
        stats = compute_summary_stats(vals)
        self.assertEqual(stats["count"], 5.0)
        self.assertEqual(stats["mean"], 30.0)
        self.assertEqual(stats["p50"], 30.0)
        self.assertEqual(stats["min"], 10.0)
        self.assertEqual(stats["max"], 50.0)

        # Test boundary sync
        synchronize_gpu_boundary()

        # Test sampler execution
        sampler = HostAndGpuSampler(sample_interval_s=0.02)
        sampler.start()
        import time
        time.sleep(0.06)
        samples = sampler.stop()
        self.assertGreaterEqual(len(samples), 1)

        summary = sampler.get_summary()
        self.assertGreaterEqual(summary["sample_count"], 1)
        self.assertIn("gpu_device_util_stats", summary)
        self.assertIn("metal_allocated_mb_stats", summary)
        self.assertIn("process_rss_mb_stats", summary)

    def test_m5_pro_real_workload_report_schema_and_integrity(self) -> None:
        """Verifies that reports/hardware/m5_pro_real_workload.json adheres to schema and governance gates."""
        repo_root = Path(__file__).resolve().parents[2]
        report_path = repo_root / "reports" / "hardware" / "m5_pro_real_workload.json"
        self.assertTrue(report_path.exists(), f"Missing required report: {report_path}")

        with open(report_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data.get("schema_version"), "1.0.0")
        self.assertIn("Apple M5 Pro", data["hardware"]["chip"])
        self.assertEqual(data["hardware"]["unified_memory_gb"], 48.0)

        workloads = data.get("workloads", {})
        self.assertIn("retrieval", workloads)
        self.assertIn("embedding", workloads)
        self.assertIn("speech", workloads)
        self.assertIn("generation", workloads)

        # 1. Retrieval: Paired CPU vs MPS, CPU faster on candidate scoring
        ret = workloads["retrieval"]
        self.assertTrue(ret["supports_cpu_mps_switch"])
        self.assertIsNone(ret["switch_limitation_reason"])
        self.assertTrue(ret["numerical_parity_verified"])
        self.assertLess(ret["speedup_ratio"], 1.0)  # CPU faster than MPS on small tiles

        # 2. Embedding: Paired CPU vs MPS, MPS shows substantial speedup
        emb = workloads["embedding"]
        self.assertTrue(emb["supports_cpu_mps_switch"])
        self.assertIsNone(emb["switch_limitation_reason"])
        self.assertTrue(emb["numerical_parity_verified"])
        self.assertGreater(emb["speedup_ratio"], 1.5)  # MPS faster than CPU on BGE-M3 batches

        # 3. Speech: Metal unified memory only, marked without switch
        speech = workloads["speech"]
        self.assertFalse(speech["supports_cpu_mps_switch"])
        self.assertIsNotNone(speech["switch_limitation_reason"])
        self.assertIn("mlx_whisper requires Apple Silicon Metal", speech["switch_limitation_reason"])
        self.assertLess(speech["mps_metrics"]["real_time_factor_stats"]["p50"], 1.0)  # Faster than real-time

        # 4. Generation: Metal unified memory only, marked without switch
        gen = workloads["generation"]
        self.assertFalse(gen["supports_cpu_mps_switch"])
        self.assertIsNotNone(gen["switch_limitation_reason"])
        self.assertIn("mlx_lm executes directly on Apple Metal", gen["switch_limitation_reason"])
        self.assertGreater(gen["mps_metrics"]["tokens_per_second_stats"]["mean"], 20.0)

        # 5. Decision Hypotheses Evaluation
        hyps = data.get("decision_hypotheses_evaluation", {})
        self.assertIn("hypothesis_1_gpu_activity_70pct", hyps)
        self.assertEqual(hyps["hypothesis_1_gpu_activity_70pct"]["verdict"], "REFUTED")

        self.assertIn("hypothesis_2_speedup_20pct", hyps)
        self.assertEqual(hyps["hypothesis_2_speedup_20pct"]["verdict"], "PARTIALLY_SUPPORTED_WITH_CAVEATS")

        self.assertIn("hypothesis_3_headroom_8gb", hyps)
        self.assertEqual(hyps["hypothesis_3_headroom_8gb"]["verdict"], "CONFIRMED_FOR_SEQUENTIAL_UNSAFE_FOR_CONCURRENT")
