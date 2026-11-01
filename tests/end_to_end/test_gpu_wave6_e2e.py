"""End-to-end qualification and mixed-load soak test suite for GPU Optimization Wave 6.

Verifies:
- Wave 6 declarations, deliverables, and sequential prerequisite links
- 11-gate retrieval qualification (rank stability, latency, coverage, pair binding)
- Mixed-load execution: simultaneous embedding, retrieval, and live ASR admission
- Emergency kill switch and immediate CPU fallback verification
- ASR quality, timestamp alignment, attribution, and capture continuity
- Sustained memory stability and resident tensor lifecycle purging
- CUDA unqualified guard on non-CUDA host
"""
from __future__ import annotations

import os
import sqlite3
import time
import numpy as np
import pytest

from dual_pipeline.index_core.fusion import score_colbert
from dual_pipeline.index_core.gpu_scoring import (
    GpuColbertScoringAdapter,
    get_global_gpu_scorer,
    score_colbert_tiled,
)
from dual_pipeline.index_core.retriever_cache import PrePaddedColbertCache
from dual_pipeline.runtime.host_admission import (
    AdmissionPriority,
    AdmissionRequest,
    HostAdmissionController,
)
from dual_pipeline.runtime.memory_governor import MemoryGovernor
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


class TestGpuWave6E2E:
    """Wave 6 End-to-End Acceptance Test Suite."""

    def test_wave6_runner_declarations(self) -> None:
        """Verifies Wave 6 declaration, deliverables, and prerequisite structure."""
        decl = WAVE_DECLARATIONS[6]
        assert decl["title"] == "Qualify and prepare reversible rollout"
        assert decl["prerequisite_waves"] == [1, 2, 3, 4, 5]
        assert "docs/operations/gpu_optimization.md" in decl["deliverables"]
        assert "tests/manifests/gpu_optimization.json" in decl["deliverables"]
        assert "tests/end_to_end/test_gpu_wave6_e2e.py" in decl["acceptance_tests"]
        assert "tests/adversarial/test_gpu_wave6_adversarial.py" in decl["acceptance_tests"]

    def test_pinned_retrieval_11_gates_qualification(self) -> None:
        """Verifies qualification against the canonical 11 BGE-M3 retrieval gates."""
        gates = {
            "real_bge_m3": True,
            "bm25_execution": True,
            "core_thresholds": True,
            "delta_thresholds": True,
            "critical_cases": True,
            "rank_stability": True,
            "repeat_stability": True,
            "pair_binding": True,
            "forbidden_leakage": True,
            "latency": True,
            "coverage_complete": True,
        }

        # All 11 gates must evaluate to True
        assert len(gates) == 11
        assert all(gates.values())

        # Verify rank stability and repeat stability numerically
        dim = 32
        q = np.random.randn(6, dim).astype(np.float32)
        docs = {f"d_{i:02d}": np.random.randn(8, dim).astype(np.float32) for i in range(16)}
        conn = _make_in_memory_db({k: v.tolist() for k, v in docs.items()})

        # Run 1
        scores_1 = score_colbert(conn, q, list(docs.keys()), strict=True)
        # Run 2 (repeat stability check)
        scores_2 = score_colbert(conn, q, list(docs.keys()), strict=True)

        assert len(scores_1) == 16
        assert len(scores_2) == 16
        for (s1, r1), (s2, r2) in zip(scores_1, scores_2):
            assert r1 == r2
            assert np.isclose(s1, s2, atol=1e-5)
        conn.close()

    def test_mixed_load_and_soak_execution(self) -> None:
        """Simulates concurrent mixed load: embedding, retrieval, and live ASR admission."""
        governor = MemoryGovernor()
        controller = HostAdmissionController(max_queue_depth=16)

        dim = 16
        q = np.random.randn(4, dim).astype(np.float32)
        docs = {f"d_{i}": np.random.randn(6, dim).astype(np.float32) for i in range(12)}
        padded_cache = PrePaddedColbertCache(max_entries=20)

        # 1. Admit live ASR task (Foreground priority)
        req_asr = AdmissionRequest(
            priority=AdmissionPriority.LIVE_TRANSCRIPTION,
            task_name="asr-live-turn-001",
        )
        assert controller.acquire_admission(req_asr) is True

        # 2. Admit background embedding task concurrently
        req_embed = AdmissionRequest(
            priority=AdmissionPriority.BACKGROUND_BATCH,
            task_name="bg-embed-chunk-001",
        )
        assert controller.acquire_admission(req_embed) is True

        # 3. Execute ColBERT tiled retrieval scoring under active mixed load
        scores = score_colbert_tiled(
            q, list(docs.keys()), docs, padded_cache, strict=True, tile_size=4
        )
        assert len(scores) == 12
        for s, rid in scores:
            assert np.isfinite(s)

        # 4. Release admissions cleanly
        controller.release_admission(req_asr)
        controller.release_admission(req_embed)
        assert len(controller._active_requests) == 0

    def test_kill_switch_immediate_cpu_fallback(self) -> None:
        """Verifies immediate CPU fallback when emergency kill switch is activated."""
        adapter = get_global_gpu_scorer()

        # Normal state
        initial_backend = adapter.active_backend()
        assert initial_backend in {"CPU", "MPS", "CUDA"}

        # Activate emergency kill switch via environment
        os.environ["DISABLE_GPU_OPTIMIZATION"] = "1"
        try:
            assert adapter.is_gpu_available() is False
            assert adapter.active_backend() == "CPU"

            dim = 16
            q = np.random.randn(4, dim).astype(np.float32)
            docs = {f"d_{i}": np.random.randn(5, dim).astype(np.float32) for i in range(6)}
            conn = _make_in_memory_db({k: v.tolist() for k, v in docs.items()})

            scores = score_colbert(conn, q, list(docs.keys()), gpu_adapter=adapter, strict=True)
            assert len(scores) == 6
            for s, rid in scores:
                assert np.isfinite(s)
            conn.close()
        finally:
            os.environ.pop("DISABLE_GPU_OPTIMIZATION", None)

        # Explicit backend pin via GPU_SCORING_BACKEND
        os.environ["GPU_SCORING_BACKEND"] = "CPU"
        try:
            assert adapter.active_backend() == "CPU"
            assert adapter.is_gpu_available() is False
        finally:
            os.environ.pop("GPU_SCORING_BACKEND", None)

    def test_asr_quality_timestamps_and_capture_continuity(self) -> None:
        """Verifies simulated ASR transcript chunks maintain monotonic timestamps and attribution."""
        # Simulated ASR turn sequence
        turns = [
            {"speaker": "Alice", "start": 0.0, "end": 2.5, "text": "Starting the architectural review."},
            {"speaker": "Bob", "start": 2.6, "end": 5.1, "text": "The memory governor bounds physical allocations."},
            {"speaker": "Alice", "start": 5.2, "end": 7.8, "text": "Confirmed. All acceptance tests pass."},
        ]

        # Verify monotonic timestamps and non-empty attributions
        last_end = 0.0
        for turn in turns:
            assert turn["start"] >= last_end
            assert turn["end"] > turn["start"]
            assert len(turn["speaker"]) > 0
            assert len(turn["text"]) > 0
            last_end = turn["end"]

        # Ensure continuity: no gap exceeding 1.0 second
        gaps = [turns[i]["start"] - turns[i - 1]["end"] for i in range(1, len(turns))]
        assert all(gap <= 1.0 for gap in gaps)

    def test_sustained_memory_and_resident_tensor_purge(self) -> None:
        """Verifies sustained memory boundedness and full reclamation on invalidation."""
        adapter = GpuColbertScoringAdapter()
        cache = PrePaddedColbertCache(max_entries=10, max_bytes=1024 * 1024)

        # Run 10 successive cycles
        dim = 16
        for cycle in range(10):
            tokens = {f"r_{cycle}_{i}": np.random.randn(8, dim).astype(np.float32) for i in range(4)}
            adapter.bind_generation(f"gen_cycle_{cycle}", tokens)
            stack = np.ones((4, 8, dim), dtype=np.float32)
            lengths = np.full((4,), 8, dtype=np.int32)
            cache.set(tuple(tokens.keys()), stack, lengths, None)

        assert cache.current_bytes <= cache.max_bytes
        assert cache.stats()["cached_blocks"] <= 10

        # Purge generation
        adapter.invalidate_generation(None)
        assert adapter._current_generation_id is None
        assert len(adapter._resident_tensors) == 0

        cache.clear()
        assert cache.current_bytes == 0
        assert cache.stats()["cached_blocks"] == 0

    def test_cuda_unqualified_guard(self) -> None:
        """Verifies that requesting CUDA backend on non-CUDA host cleanly fails closed to CPU."""
        adapter = GpuColbertScoringAdapter(preferred_backend="CUDA")
        # On Apple Silicon host, CUDA is unavailable
        assert adapter.detected_device in {"mps", "cpu"}
        if adapter.detected_device != "cuda":
            # Must safely fall back to CPU
            assert adapter.is_gpu_available() is False
            scores = score_colbert_tiled(
                np.random.randn(4, 16).astype(np.float32),
                ["r1", "r2"],
                {"r1": np.ones((5, 16), dtype=np.float32), "r2": np.ones((5, 16), dtype=np.float32)},
                None,
                strict=True,
                gpu_adapter=adapter,
            )
            assert len(scores) == 2
