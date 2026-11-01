"""End-to-End Acceptance Suite for GPU Optimization Wave 1.

Verifies:
1. Trustworthy ColBERT decoding: legacy float32 and supported quantized records
   decode correctly and produce valid MaxSim scores.
2. Numerical agreement between float32 references and quantized vectors.
3. LocalModelRuntime explicit real-load failure with governor reservation rollback.
4. Mock exclusion from performance evidence and benchmark receipts.
5. Wave 1 runner execution on an isolated disposable sandbox producing sealed receipts.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest import TestCase, mock

import numpy as np

from dual_pipeline.index_core.constants import _pack
from dual_pipeline.index_core.fusion import score_colbert
from dual_pipeline.index_core.retriever_cache import PrePaddedColbertCache
from dual_pipeline.runtime.memory_governor import MemoryGovernor
from dual_pipeline.runtime.model_runtime import LocalModelRuntime, ModelInferenceResult
from dual_pipeline.storage.quantized_colbert_packer import pack_colbert_tensor
from tools.gpu_optimization_runner_core import canonical_digest


class TestGpuWave1E2E(TestCase):
    """End-to-end tests for Wave 1 execution correctness and scoring."""

    def setUp(self) -> None:
        self.dim = 32
        np.random.seed(42)

    def _create_sidecar_db(self, records: dict[str, list[list[float]]], use_quantization: bool = False) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO metadata VALUES ('colbert_dimensions', ?)", (str(self.dim),))
        conn.execute(
            """CREATE TABLE colbert_token (
                record_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                token TEXT NOT NULL,
                dimensions INTEGER NOT NULL,
                vector BLOB NOT NULL,
                PRIMARY KEY (record_id, position)
            )"""
        )

        for rid, tokens in records.items():
            for pos, vec in enumerate(tokens):
                if use_quantization:
                    blob = pack_colbert_tensor(vec)
                else:
                    blob = _pack(vec)
                conn.execute(
                    "INSERT INTO colbert_token VALUES (?, ?, ?, ?, ?)",
                    (rid, pos, f"t_{pos}", self.dim, blob),
                )
        conn.commit()
        return conn

    def test_legacy_float32_and_quantized_decoding_parity(self) -> None:
        """Verifies float32 and quantized records decode and score with high numerical agreement."""
        num_docs = 10
        tokens_per_doc = 8
        raw_docs = {
            f"doc_{i}": np.random.randn(tokens_per_doc, self.dim).astype(np.float32).tolist()
            for i in range(num_docs)
        }
        query = np.random.randn(4, self.dim).astype(np.float32)

        # Database with uncompressed float32 vectors
        conn_fp32 = self._create_sidecar_db(raw_docs, use_quantization=False)
        cache_fp32: dict[str, np.ndarray] = {}
        scores_fp32 = score_colbert(conn_fp32, query, list(raw_docs.keys()), colbert_cache=cache_fp32)

        # Database with supported quantized int8 vectors (scale + int8)
        conn_quant = self._create_sidecar_db(raw_docs, use_quantization=True)
        cache_quant: dict[str, np.ndarray] = {}
        scores_quant = score_colbert(conn_quant, query, list(raw_docs.keys()), colbert_cache=cache_quant)

        self.assertEqual(len(scores_fp32), num_docs)
        self.assertEqual(len(scores_quant), num_docs)

        # Rankings should align closely with high rank correlation
        fp32_order = [rid for _, rid in scores_fp32]
        quant_order = [rid for _, rid in scores_quant]
        self.assertEqual(fp32_order[:3], quant_order[:3])

        # Scores should agree within quantization error bounds
        map_fp32 = {rid: s for s, rid in scores_fp32}
        map_quant = {rid: s for s, rid in scores_quant}
        for rid in raw_docs:
            self.assertAlmostEqual(map_fp32[rid], map_quant[rid], delta=0.5)

        conn_fp32.close()
        conn_quant.close()

    def test_batched_scoring_and_cache_acceleration(self) -> None:
        """Verifies batched MaxSim scoring produces identical results with PrePaddedColbertCache."""
        raw_docs = {
            f"doc_{i}": np.random.randn(np.random.randint(3, 12), self.dim).astype(np.float32).tolist()
            for i in range(16)
        }
        query = np.random.randn(5, self.dim).astype(np.float32)
        conn = self._create_sidecar_db(raw_docs, use_quantization=True)

        token_cache: dict[str, np.ndarray] = {}
        padded_cache = PrePaddedColbertCache(max_entries=32)
        target_ids = list(raw_docs.keys())

        # First run populates caches
        scores_1 = score_colbert(conn, query, target_ids, colbert_cache=token_cache, padded_cache=padded_cache)
        # Second run hits pre-padded cache
        scores_2 = score_colbert(conn, query, target_ids, colbert_cache=token_cache, padded_cache=padded_cache)

        self.assertEqual(len(scores_1), len(target_ids))
        self.assertEqual(scores_1, scores_2)
        stats = padded_cache.stats()
        self.assertGreater(stats["hits"], 0)
        conn.close()

    def test_real_load_failure_rollback_and_no_mock_fallback(self) -> None:
        """Verifies failed real load raises RuntimeError, rolls back governor allocation, and never falls back to mock."""
        gov = MemoryGovernor()
        runtime = LocalModelRuntime(governor=gov, mock_mode=False)

        # Mock mlx_lm.load to simulate real load failure (e.g. CUDA/MPS allocation error or missing weights)
        with mock.patch("mlx_lm.load", side_effect=OSError("Weight tensors corrupt or missing on disk")):
            with self.assertRaises(RuntimeError) as ctx:
                runtime.load_model("qwen_2_5_14b_instruct")

            self.assertIn("Failed loading model 'qwen_2_5_14b_instruct' on Metal GPU", str(ctx.exception))

        # Memory reservation MUST be rolled back to 0.0 GB
        self.assertEqual(gov.current_allocated_gb, 0.0)
        self.assertNotIn("qwen_2_5_14b_instruct", runtime._loaded_models)

    def test_mock_exclusion_from_performance_evidence(self) -> None:
        """Verifies mock results fail closed when attempted to be used as performance evidence."""
        gov = MemoryGovernor()
        # LocalModelRuntime rejects mock_mode fail-closed
        with self.assertRaises(RuntimeError) as ctx_mock:
            LocalModelRuntime(governor=gov, mock_mode=True)
        self.assertIn("MOCK_MODEL_PROHIBITED", str(ctx_mock.exception))

        runtime = LocalModelRuntime(governor=gov, mock_mode=False)
        mock_result = ModelInferenceResult(
            content="MOCK_OUTPUT",
            model_key="qwen_2_5_14b_instruct",
            model_id="mlx-community/Qwen2.5-14B-Instruct-4bit",
            prompt_tokens=10,
            completion_tokens=20,
            finish_reason="stop",
            is_mock=True,
        )
        self.assertTrue(mock_result.is_mock)

        # 1. to_performance_record fails closed
        with self.assertRaises(ValueError) as ctx1:
            mock_result.to_performance_record()
        self.assertIn("Mock inference result rejected", str(ctx1.exception))

        # 2. validate_for_performance_evidence fails closed
        with self.assertRaises(ValueError) as ctx2:
            runtime.validate_for_performance_evidence(mock_result)
        self.assertIn("Mock inference result rejected", str(ctx2.exception))

        # 3. record_performance_evidence fails closed
        with self.assertRaises(ValueError) as ctx3:
            runtime.record_performance_evidence(mock_result, elapsed_seconds=0.125)
        self.assertIn("Mock inference result rejected", str(ctx3.exception))

        # 4. Verified real execution result succeeds
        real_result = ModelInferenceResult(
            content="Verified hardware generation",
            model_key="qwen_2_5_14b_instruct",
            model_id="mlx-community/Qwen2.5-14B-Instruct-4bit",
            prompt_tokens=15,
            completion_tokens=25,
            finish_reason="stop",
            is_mock=False,
        )
        perf_record = runtime.record_performance_evidence(real_result, elapsed_seconds=0.5)
        self.assertFalse(perf_record["is_mock"])
        self.assertEqual(perf_record["tokens_per_second"], 50.0)
        self.assertEqual(perf_record["elapsed_seconds"], 0.5)

    def test_wave_runner_sandbox_execution(self) -> None:
        """Verifies execute_gpu_wave creates sealed, immutable receipt in disposable sandbox."""
        from tools.run_gpu_optimization_wave import execute_gpu_wave

        with tempfile.TemporaryDirectory() as sandbox_dir:
            sandbox = Path(sandbox_dir)
            receipt_dir = sandbox / "receipts"
            plan_path = Path(__file__).resolve().parents[2] / "docs/plans/docs_gpu_optimization_debate_plan_20261006.md"

            mock_run_res = mock.MagicMock()
            mock_run_res.total_tests = 10
            mock_run_res.passed_count = 10
            mock_run_res.failed_count = 0
            mock_run_res.error_count = 0
            mock_run_res.skipped_count = 0
            mock_run_res.elapsed_seconds = 1.25
            mock_run_res.collected_ids = [f"test_{i}" for i in range(10)]
            mock_run_res.wasSuccessful.return_value = True

            with mock.patch("tools.run_gpu_optimization_wave.run_pytest_with_progress", return_value=mock_run_res):
                exit_code = execute_gpu_wave(
                    wave=1,
                    sandbox=sandbox,
                    plan_path=plan_path,
                    receipt_root=receipt_dir,
                    timeout=60,
                    immutable=True,
                )
            self.assertEqual(exit_code, 0)

            receipt_file = receipt_dir / "gpu_wave_1_receipt.json"
            self.assertTrue(receipt_file.is_file())

            receipt_data = json.loads(receipt_file.read_text(encoding="utf-8"))
            self.assertEqual(receipt_data["status"], "PASS")
            self.assertEqual(receipt_data["wave"], 1)
            self.assertIn("receipt_digest", receipt_data)

            # Verify cryptographic digest
            claimed_digest = receipt_data["receipt_digest"]
            payload_copy = {k: v for k, v in receipt_data.items() if k != "receipt_digest"}
            self.assertEqual(canonical_digest(payload_copy), claimed_digest)
