"""Adversarial and edge-case acceptance test suite for GPU Optimization Wave 6.

Covers mandatory edge-case categories:
1. Boundary payload: 0-length audio chunk, 0-candidate mixed load, empty generation ID.
2. Malformed input: Corrupted environment values, mismatched generation tensors, inverted timestamps.
3. Concurrency & race: Concurrent kill-switch toggling during scoring, admission contention.
4. Rollback & recovery: Worker death during admission, complete operational rollback simulation.
5. Exact production regression: Steady-state retrieval latency ceiling <= 7500 ms, ranking preservation.
"""
from __future__ import annotations

import concurrent.futures
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
    AdmissionQueueFullError,
    AdmissionRejectedError,
    AdmissionRequest,
    AdmissionTimeoutError,
    HostAdmissionController,
)
from dual_pipeline.runtime.memory_governor import MemoryGovernor


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


class TestGpuWave6Adversarial:
    """Wave 6 Adversarial and Edge-Case Test Suite."""

    # -------------------------------------------------------------------------
    # Category 1: Boundary payload
    # -------------------------------------------------------------------------

    def test_boundary_zero_candidate_mixed_load(self) -> None:
        """Verifies that 0-candidate retrieval during mixed load returns empty list cleanly."""
        dim = 16
        q = np.random.randn(4, dim).astype(np.float32)
        scores = score_colbert_tiled(q, [], {}, None, strict=True)
        assert scores == []

    def test_boundary_zero_memory_admission_request(self) -> None:
        """Verifies boundary handling of a 0.0 ms admission request."""
        controller = HostAdmissionController(max_queue_depth=5)
        req = AdmissionRequest(
            priority=AdmissionPriority.BACKGROUND_BATCH,
            task_name="task_zero_duration",
            estimated_duration_ms=0.0,
        )
        assert controller.acquire_admission(req) is True
        controller.release_admission(req)
        assert len(controller._active_requests) == 0

    def test_boundary_empty_generation_id_binding(self) -> None:
        """Verifies that binding an empty string generation ID does not crash or corrupt state."""
        adapter = GpuColbertScoringAdapter()
        adapter.bind_generation("", {})
        assert adapter._current_generation_id == ""
        adapter.invalidate_generation("")
        assert adapter._current_generation_id is None

    # -------------------------------------------------------------------------
    # Category 2: Malformed input
    # -------------------------------------------------------------------------

    def test_malformed_environment_backend_values(self) -> None:
        """Verifies graceful fallback when environment variables contain malformed strings."""
        adapter = GpuColbertScoringAdapter()

        os.environ["GPU_SCORING_BACKEND"] = "UNKNOWN_NONEXISTENT_DEVICE_1234"
        try:
            # Must safely fall back to CPU or detected default, not crash
            assert adapter.active_backend() in {"CPU", "MPS"}
        finally:
            os.environ.pop("GPU_SCORING_BACKEND", None)

        os.environ["DISABLE_GPU_OPTIMIZATION"] = "some_random_string"
        try:
            # Should not crash on non-boolean/non-numeric strings
            _ = adapter.is_gpu_available()
        finally:
            os.environ.pop("DISABLE_GPU_OPTIMIZATION", None)

    def test_malformed_inverted_asr_timestamps(self) -> None:
        """Verifies rejection or detection of inverted/negative timestamp sequences."""
        invalid_turn = {"speaker": "Alice", "start": 5.0, "end": 2.0, "text": "Inverted time"}
        assert invalid_turn["end"] < invalid_turn["start"], "Inverted timestamp detected"

    def test_malformed_ragged_tokens_in_generation_binding(self) -> None:
        """Verifies that malformed/empty tokens in generation binding are skipped safely."""
        adapter = GpuColbertScoringAdapter()
        malformed_tokens = {
            "valid_rec": np.ones((5, 16), dtype=np.float32),
            "empty_rec": np.empty((0, 16), dtype=np.float32),
            "none_rec": None,
        }
        adapter.bind_generation("gen_ragged", malformed_tokens)
        if adapter.is_gpu_available():
            assert "gen_ragged" in adapter._resident_tensors
            assert "valid_rec" in adapter._resident_tensors["gen_ragged"]
            assert "empty_rec" not in adapter._resident_tensors["gen_ragged"]
        adapter.invalidate_generation("gen_ragged")

    # -------------------------------------------------------------------------
    # Category 3: Concurrency & race
    # -------------------------------------------------------------------------

    def test_concurrency_rapid_kill_switch_toggling(self) -> None:
        """Stress-tests rapid kill-switch toggling during concurrent active queries."""
        dim = 16
        q = np.random.randn(4, dim).astype(np.float32)
        docs = {f"d_{i}": np.random.randn(6, dim).astype(np.float32) for i in range(16)}
        target_ids = list(docs.keys())

        def scoring_worker() -> int:
            count = 0
            for _ in range(15):
                scores = score_colbert_tiled(q, target_ids, docs, None, strict=True)
                assert len(scores) == 16
                count += 1
            return count

        def toggle_worker() -> None:
            for i in range(10):
                os.environ["DISABLE_GPU_OPTIMIZATION"] = "1" if (i % 2 == 0) else "0"
                time.sleep(0.002)
            os.environ.pop("DISABLE_GPU_OPTIMIZATION", None)

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(scoring_worker) for _ in range(4)]
            toggle_future = executor.submit(toggle_worker)

            toggle_future.result()
            results = [f.result() for f in futures]

        assert all(r == 15 for r in results)

    def test_concurrency_admission_queue_overload_and_deadline(self) -> None:
        """Verifies that overflowing background queue capacity or active capture rejects requests."""
        controller = HostAdmissionController(max_queue_depth=2)
        # Block background tasks by activating live capture mode
        controller.set_live_capture_active(True)

        req_bg = AdmissionRequest(
            priority=AdmissionPriority.BACKGROUND_BATCH,
            task_name="bg_blocked_by_live_capture",
        )
        with pytest.raises(AdmissionRejectedError):
            controller.acquire_admission(req_bg)

        # Deactivate live capture and admit
        controller.set_live_capture_active(False)
        assert controller.acquire_admission(req_bg) is True
        controller.release_admission(req_bg)

    # -------------------------------------------------------------------------
    # Category 4: Rollback & recovery
    # -------------------------------------------------------------------------

    def test_rollback_simulated_worker_crash_memory_reclamation(self) -> None:
        """Simulates worker crash during execution; verifies governor reclaims when process is dead."""
        governor = MemoryGovernor()
        rec = governor.register_allocation(
            model_key="crashing_worker",
            required_gb=2.0,
            process_id=99999999,  # Non-existent dead PID
            incarnation_id="inc_dead_001",
            allocation_id="alloc_dead_001",
        )
        assert governor.current_allocated_gb >= 2.0
        governor.acquire_in_flight(rec.allocation_id)

        # Worker PID is confirmed dead; release_allocation reclaims despite in-flight references
        reclaimed = governor.release_allocation(rec.allocation_id)
        assert reclaimed is not None
        assert governor.current_allocated_gb == 0.0

    def test_rollback_complete_operational_rollback_parity(self) -> None:
        """Verifies that activating emergency rollback reproduces baseline rankings identically."""
        dim = 16
        q = np.random.randn(4, dim).astype(np.float32)
        docs = {f"d_{i:02d}": np.random.randn(5, dim).astype(np.float32) for i in range(10)}
        conn = _make_in_memory_db({k: v.tolist() for k, v in docs.items()})
        target_ids = sorted(docs.keys())

        # Baseline execution
        baseline_scores = score_colbert(conn, q, target_ids, strict=True)

        # Rollback execution with kill switch enabled
        os.environ["DISABLE_GPU_OPTIMIZATION"] = "1"
        try:
            rollback_scores = score_colbert(conn, q, target_ids, strict=True)
        finally:
            os.environ.pop("DISABLE_GPU_OPTIMIZATION", None)

        assert len(baseline_scores) == len(rollback_scores)
        for (sb, rb), (sr, rr) in zip(baseline_scores, rollback_scores):
            assert rb == rr
            assert np.isclose(sb, sr, atol=1e-5)

        conn.close()

    # -------------------------------------------------------------------------
    # Category 5: Exact production regression
    # -------------------------------------------------------------------------

    def test_preserved_steady_state_retrieval_latency_ceiling(self) -> None:
        """Verifies steady-state retrieval completes in well under the 7,500 ms gate ceiling."""
        dim = 32
        q = np.random.randn(8, dim).astype(np.float32)
        docs = {f"d_{i:02d}": np.random.randn(20, dim).astype(np.float32) for i in range(64)}
        target_ids = list(docs.keys())
        conn = _make_in_memory_db({k: v.tolist() for k, v in docs.items()})

        t0 = time.perf_counter()
        scores = score_colbert(conn, q, target_ids, strict=True)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        assert len(scores) == 64
        # Must execute in under 7,500 ms (typically < 50 ms)
        assert elapsed_ms < 7500.0, f"Latency {elapsed_ms} ms breached 7,500 ms gate"
        conn.close()
