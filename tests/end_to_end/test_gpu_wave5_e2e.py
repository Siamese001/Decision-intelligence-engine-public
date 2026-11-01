"""End-to-end acceptance tests for GPU Optimization Wave 5.

Verifies:
- Bounded-tile candidate document scoring for ColBERT MaxSim
- PrePaddedColbertCache byte budgeting, capacity bounds, and eviction tracking
- GPU hardware execution with numerical tolerance (atol <= 1e-4)
- Generation-bound resident tensor binding and lifecycle invalidation
- Graceful CPU fallback on GPU failure or unavailability
- Empirical crossover evaluation and documented backend decision
"""
from __future__ import annotations

import sqlite3
import numpy as np
import pytest

from dual_pipeline.index_core.fusion import score_colbert
from dual_pipeline.index_core.gpu_scoring import (
    DEFAULT_COLBERT_TILE_SIZE,
    GpuColbertScoringAdapter,
    ScoringDecision,
    get_global_gpu_scorer,
    score_colbert_tiled,
)
from dual_pipeline.index_core.retriever_cache import PrePaddedColbertCache
from tools.gpu_optimization_runner_core.declarations import WAVE_DECLARATIONS


def _make_in_memory_db(docs: dict[str, list[list[float]]]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE colbert_token (
            record_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            token TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            vector BLOB NOT NULL
        )"""
    )
    for rid, mat in docs.items():
        for pos, vec in enumerate(mat):
            blob = np.array(vec, dtype=np.float32).tobytes()
            conn.execute(
                "INSERT INTO colbert_token VALUES (?, ?, ?, ?, ?)",
                (rid, pos, f"tok_{pos}", len(vec), blob),
            )
    conn.commit()
    return conn


class TestGpuWave5E2E:
    """Wave 5 End-to-End Acceptance Test Suite."""

    def test_wave5_runner_declarations(self) -> None:
        """Verifies Wave 5 declaration and prerequisite structure."""
        decl = WAVE_DECLARATIONS[5]
        assert decl["title"] == "Optimize retrieval at the measured crossover"
        assert decl["prerequisite_waves"] == [1, 2, 3, 4]
        assert "dual_pipeline/index_core/retriever_cache.py" in decl["deliverables"]
        assert "dual_pipeline/index_core/fusion.py" in decl["deliverables"]
        assert "dual_pipeline/index_core/gpu_scoring.py" in decl["deliverables"]
        assert "tests/end_to_end/test_gpu_wave5_e2e.py" in decl["acceptance_tests"]
        assert "tests/adversarial/test_gpu_wave5_adversarial.py" in decl["acceptance_tests"]

    def test_bounded_tile_colbert_scoring_parity(self) -> None:
        """Verifies that tiled scoring across tile boundaries matches exact serial MaxSim."""
        dim = 32
        candidate_count = 100
        tile_size = 32  # Will trigger 4 tiles: 32, 32, 32, 4
        np.random.seed(501)
        q = np.random.randn(6, dim).astype(np.float32)

        docs = {
            f"doc_{i:03d}": np.random.randn(np.random.randint(4, 20), dim).astype(np.float32)
            for i in range(candidate_count)
        }
        conn = _make_in_memory_db({k: v.tolist() for k, v in docs.items()})
        cache: dict[str, np.ndarray] = {}
        target_ids = sorted(docs.keys())

        # Score with bounded tile size = 32
        scores = score_colbert(
            conn, q, target_ids, colbert_cache=cache, tile_size=tile_size, strict=True
        )
        assert len(scores) == candidate_count

        # Parity check against serial calculation
        for score_val, rid in scores:
            doc_mat = docs[rid]
            expected = float(np.sum(np.max(np.matmul(q, doc_mat.T), axis=1)))
            assert np.isclose(score_val, expected, atol=1e-4)

        conn.close()

    def test_prepadded_cache_byte_budget_and_eviction(self) -> None:
        """Verifies that PrePaddedColbertCache enforces byte budgets and tracks evictions."""
        # 4 records, 10 tokens, 32 dims = 4 * 10 * 32 * 4 = 5,120 bytes for doc_stack
        dim = 32
        stack = np.ones((4, 10, dim), dtype=np.float32)
        lengths = np.full((4,), 10, dtype=np.int32)
        mask = None
        single_item_bytes = PrePaddedColbertCache._calculate_bytes(stack, lengths, mask)
        assert single_item_bytes > 5000

        # Create cache with budget for only ~2 entries
        max_bytes = int(single_item_bytes * 2.5)
        cache = PrePaddedColbertCache(max_entries=10, max_bytes=max_bytes)

        k1 = ("r1", "r2", "r3", "r4")
        k2 = ("r5", "r6", "r7", "r8")
        k3 = ("r9", "r10", "r11", "r12")

        cache.set(k1, stack, lengths, mask)
        assert cache.current_bytes == single_item_bytes
        assert cache.stats()["cached_blocks"] == 1
        assert cache.evictions_count == 0

        cache.set(k2, stack, lengths, mask)
        assert cache.current_bytes == single_item_bytes * 2
        assert cache.stats()["cached_blocks"] == 2
        assert cache.evictions_count == 0

        # Adding 3rd item exceeds byte budget; must evict k1
        cache.set(k3, stack, lengths, mask)
        assert cache.current_bytes <= max_bytes
        assert cache.stats()["cached_blocks"] == 2
        assert cache.evictions_count == 1
        assert cache.get(k1) is None
        assert cache.get(k2) is not None
        assert cache.get(k3) is not None

        detailed = cache.stats(detailed=True)
        assert detailed["current_bytes"] == single_item_bytes * 2
        assert detailed["max_bytes"] == max_bytes
        assert detailed["evictions_count"] == 1

    def test_gpu_hardware_scoring_numerical_equivalence(self) -> None:
        """Verifies that GPU scoring matches CPU batched NumPy within numerical tolerance."""
        adapter = GpuColbertScoringAdapter()
        if not adapter.is_gpu_available():
            pytest.skip("No GPU acceleration available on current host.")

        dim = 32
        candidate_count = 64
        q_tokens = 8
        doc_tokens = 20
        np.random.seed(502)

        q_mat = np.random.randn(q_tokens, dim).astype(np.float32)
        doc_stack = np.random.randn(candidate_count, doc_tokens, dim).astype(np.float32)
        lengths = np.full((candidate_count,), doc_tokens, dtype=np.int32)
        mask = None

        # Reference CPU
        sim_cpu = np.matmul(doc_stack, q_mat.T)
        cpu_scores = np.sum(np.max(sim_cpu, axis=1), axis=1)

        # GPU execution
        gpu_scores = adapter.score_tile_gpu(q_mat, doc_stack, lengths, mask)
        assert gpu_scores is not None
        assert len(gpu_scores) == candidate_count

        max_diff = float(np.max(np.abs(cpu_scores - gpu_scores)))
        assert max_diff <= 1e-4, f"GPU difference {max_diff} exceeded tolerance 1e-4"

        # Deterministic ranking check
        cpu_rank = np.argsort(-cpu_scores)
        gpu_rank = np.argsort(-gpu_scores)
        assert np.array_equal(cpu_rank, gpu_rank), "GPU produced differing ranking order"

    def test_generation_bound_resident_tensors_lifecycle(self) -> None:
        """Verifies generation binding, tensor preloading, and lifecycle invalidation."""
        adapter = GpuColbertScoringAdapter()
        dim = 32
        tokens = {
            "rec_1": np.random.randn(12, dim).astype(np.float32),
            "rec_2": np.random.randn(16, dim).astype(np.float32),
        }

        # Bind generation 1
        adapter.bind_generation("gen-alpha-2026", tokens)
        assert adapter._current_generation_id == "gen-alpha-2026"
        if adapter.is_gpu_available():
            assert "gen-alpha-2026" in adapter._resident_tensors
            assert "rec_1" in adapter._resident_tensors["gen-alpha-2026"]

        # Invalidate generation 1
        adapter.invalidate_generation("gen-alpha-2026")
        assert adapter._current_generation_id is None
        assert "gen-alpha-2026" not in adapter._resident_tensors

        # Switch to generation 2
        adapter.bind_generation("gen-beta-2026", tokens)
        assert adapter._current_generation_id == "gen-beta-2026"

        # Global invalidation
        adapter.invalidate_generation(None)
        assert adapter._current_generation_id is None
        assert len(adapter._resident_tensors) == 0

    def test_cpu_fallback_on_gpu_error(self) -> None:
        """Verifies transparent CPU fallback when GPU scoring encounters an error."""
        # Adapter with invalid device to trigger fallback
        adapter = GpuColbertScoringAdapter(preferred_backend="MPS")
        adapter._detected_device = "invalid_device_name"

        dim = 16
        q_mat = np.random.randn(4, dim).astype(np.float32)
        doc_stack = np.random.randn(8, 10, dim).astype(np.float32)
        lengths = np.full((8,), 10, dtype=np.int32)

        # score_tile_gpu should catch device error and return None
        res = adapter.score_tile_gpu(q_mat, doc_stack, lengths, None)
        assert res is None

        # score_colbert_tiled should fall back to CPU seamlessly
        token_cache = {f"d_{i}": doc_stack[i] for i in range(8)}
        target_ids = [f"d_{i}" for i in range(8)]
        scores = score_colbert_tiled(
            q_mat, target_ids, token_cache, None, strict=True, gpu_adapter=adapter
        )
        assert scores is not None
        assert len(scores) == 8
        for s, rid in scores:
            assert np.isfinite(s)
            assert s > 0.0

    def test_crossover_evaluation_and_documented_retention_decision(self) -> None:
        """Verifies crossover evaluation and documented CPU retention decision."""
        adapter = GpuColbertScoringAdapter()
        decision = adapter.evaluate_crossover(
            candidate_count=64, query_tokens=8, doc_tokens=24, dim=32, repeats=3
        )

        assert isinstance(decision, ScoringDecision)
        assert decision.crossover_evaluated is True
        assert decision.selected_backend in {"CPU", "MPS", "CUDA"}
        assert len(decision.reason) > 0
        assert decision.cpu_latency_ms is not None
        assert decision.cpu_latency_ms > 0.0

        if adapter.is_gpu_available():
            assert decision.gpu_latency_ms is not None
            assert decision.max_score_abs_diff is not None
            assert decision.max_score_abs_diff <= 1e-4
            assert decision.deterministic_rank_match is True
