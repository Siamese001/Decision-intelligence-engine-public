"""Property-based and adversarial edge-case tests for M5 48GB parallel orchestration."""

from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import time
from typing import Any, Dict, List

from hypothesis import given, settings, strategies as st

from dual_pipeline.canonical_pipeline.parallel_orchestrator import (
    MODE_1_CACHED_AUDIO_FULL_SYNTHESIS,
    MODE_2_DOCUMENT_OUTPUT3_INGESTION,
    atomic_hitl_surfacing_barrier,
    get_m5_hardware_profile,
    resolve_optimal_parallel_workers,
    run_indexed_parallel,
    run_named_tasks_parallel,
    should_enable_parallel_orchestration,
    stage7_publication_mutex,
)
from dual_pipeline.index_core.backends import (
    DENSE_DIMENSIONS,
    FlagEmbeddingBackend,
    _MPS_INFERENCE_LOCK,
)
from dual_pipeline.transcript_pipeline_core.caching import cache_audio_transcript


# ---------------------------------------------------------------------------
# Category 1: Boundary Payloads & Malformed Inputs
# ---------------------------------------------------------------------------
@settings(max_examples=25, deadline=None)
@given(
    short_text=st.text(min_size=0, max_size=20),
    mutate_graph=st.booleans(),
)
def test_property_boundary_payloads_fail_closed(short_text: str, mutate_graph: bool) -> None:
    """Empty paths, 0-byte files, and under-length caches always fail closed."""
    enabled_empty, _ = should_enable_parallel_orchestration("", {}, mutate_graph=mutate_graph)
    assert enabled_empty is False

    with tempfile.TemporaryDirectory(prefix="eval_sandbox_prop_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        zero_wav = tmp_path / "empty.wav"
        zero_wav.write_bytes(b"")
        short_cache = tmp_path / "short_cache.txt"
        short_cache.write_text(short_text, encoding="utf-8")

        enabled_short, reason = should_enable_parallel_orchestration(
            str(zero_wav),
            {"transcript_cache_path": str(short_cache), "outputs": ["01", "02", "03"]},
            mutate_graph=mutate_graph,
        )
        assert enabled_short is False
        assert reason in ("GRAPH_MUTATION_DISABLED", "COLD_AUDIO_ASR_REQUIRED")


# ---------------------------------------------------------------------------
# Category 2: Invariant Bypass Attempts (Dual-Route Exclusivity)
# ---------------------------------------------------------------------------
@settings(max_examples=30, deadline=None)
@given(
    package_only=st.booleans(),
    dry_run=st.booleans(),
    is_live=st.booleans(),
    is_notes=st.booleans(),
    has_flock=st.booleans(),
)
def test_property_invariant_bypass_exclusions_blocked(
    package_only: bool,
    dry_run: bool,
    is_live: bool,
    is_notes: bool,
    has_flock: bool,
) -> None:
    """Any attempt to trigger parallel orchestration under excluded flags fails closed."""
    if not any([package_only, dry_run, is_live, is_notes, has_flock]):
        return

    with tempfile.TemporaryDirectory(prefix="eval_sandbox_bypass_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        wav_path = tmp_path / "meeting.wav"
        wav_path.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 64)
        cache_dir = tmp_path / "cache"
        cache_audio_transcript(
            wav_path,
            "Speaker 1: Verified cached transcript content exceeding fifty characters easily.",
            cache_dir=cache_dir,
        )
        if has_flock:
            wav_path.with_name(f".{wav_path.name}.flock").write_text("locked", encoding="utf-8")

        meta: Dict[str, Any] = {
            "transcript_cache_dir": str(cache_dir),
            "outputs": ["01", "02", "03"],
            "package_only": package_only,
            "dry_run": dry_run,
            "notes": is_notes,
        }
        if is_live:
            meta["capture_mode"] = "live_stream"

        enabled, _ = should_enable_parallel_orchestration(str(wav_path), meta, mutate_graph=True)
        assert enabled is False


def test_only_two_approved_routes_activate_parallel_mode() -> None:
    """Verify exact activation for Route 1 (cached WAV) and Route 2 (document Output 03)."""
    with tempfile.TemporaryDirectory(prefix="eval_sandbox_routes_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        wav_path = tmp_path / "session.wav"
        wav_path.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x01" * 128)
        cache_dir = tmp_path / "tx_cache"

        # Cold WAV without cache -> blocked
        cold_ok, cold_reason = should_enable_parallel_orchestration(
            str(wav_path),
            {"transcript_cache_dir": str(cache_dir), "outputs": ["01", "02", "03"]},
            mutate_graph=True,
        )
        assert cold_ok is False
        assert cold_reason == "COLD_AUDIO_ASR_REQUIRED"

        # Warm cached WAV -> Route 1 enabled
        cache_audio_transcript(
            wav_path,
            "Speaker_Alpha: Welcome everyone to the architecture review session today.",
            cache_dir=cache_dir,
        )
        warm_ok, warm_reason = should_enable_parallel_orchestration(
            str(wav_path),
            {"transcript_cache_dir": str(cache_dir), "outputs": ["01", "02", "03"]},
            mutate_graph=True,
        )
        assert warm_ok is True
        assert warm_reason == MODE_1_CACHED_AUDIO_FULL_SYNTHESIS

        # Warm cached WAV but mutate_graph=False -> blocked
        no_mut_ok, no_mut_reason = should_enable_parallel_orchestration(
            str(wav_path),
            {"transcript_cache_dir": str(cache_dir), "outputs": ["01", "02", "03"]},
            mutate_graph=False,
        )
        assert no_mut_ok is False
        assert no_mut_reason == "GRAPH_MUTATION_DISABLED"

        # Document with Output 03 only + mutate_graph=True -> Route 2 enabled
        doc_path = tmp_path / "strategy_memo.md"
        doc_path.write_text("# Executive Memo\nKey decision content.", encoding="utf-8")
        doc_ok, doc_reason = should_enable_parallel_orchestration(
            str(doc_path),
            {"is_document": True, "output3_only": True, "outputs": ["03"]},
            mutate_graph=True,
        )
        assert doc_ok is True
        assert doc_reason == MODE_2_DOCUMENT_OUTPUT3_INGESTION

        # Document requesting Outputs 01/02 -> blocked
        bad_doc_ok, _ = should_enable_parallel_orchestration(
            str(doc_path),
            {"is_document": True, "outputs": ["01", "02", "03"]},
            mutate_graph=True,
        )
        assert bad_doc_ok is False


# ---------------------------------------------------------------------------
# Category 3: Concurrency, Race Conditions & MPS Serialization
# ---------------------------------------------------------------------------
@settings(max_examples=20, deadline=None)
@given(items=st.lists(st.integers(min_value=-1000, max_value=1000), min_size=0, max_size=24))
def test_property_run_indexed_parallel_preserves_exact_order(items: List[int]) -> None:
    """Parallel indexed execution always preserves deterministic input ordering."""
    def _worker(idx: int, val: int) -> Tuple[int, int]:
        if idx % 2 == 0:
            time.sleep(0.001)
        return (idx, val * 2)

    out = run_indexed_parallel(items, _worker, max_workers=8)
    assert out == [(i, v * 2) for i, v in enumerate(items)]


def test_mps_inference_lock_serializes_concurrent_bge_m3_encode_calls() -> None:
    """Verify FlagEmbeddingBackend.encode_many serializes concurrent MPS encode calls."""
    backend = object.__new__(FlagEmbeddingBackend)
    backend.execution_device = "mps"
    backend.use_fp16 = True
    backend._residency_verified = True
    backend.colbert_dimensions = 16

    active_calls = 0
    max_concurrent_observed = 0
    counter_lock = threading.Lock()
    observed_batch_sizes: List[int] = []

    class _DummyModel:
        def encode(self, values: List[str], *, batch_size: int, **kwargs: Any) -> Dict[str, Any]:
            nonlocal active_calls, max_concurrent_observed
            with counter_lock:
                active_calls += 1
                max_concurrent_observed = max(max_concurrent_observed, active_calls)
                observed_batch_sizes.append(batch_size)
            time.sleep(0.005)
            with counter_lock:
                active_calls -= 1
            n = len(values)
            return {
                "dense_vecs": [[0.1] * DENSE_DIMENSIONS for _ in range(n)],
                "lexical_weights": [{"1": 0.5} for _ in range(n)],
                "colbert_vecs": [[[0.2] * 16] for _ in range(n)],
            }

    backend._model = _DummyModel()

    def _call_encode(idx: int, _: int) -> int:
        res = backend.encode_many([f"text-{idx}-{j}" for j in range(40)])
        return len(res)

    counts = run_indexed_parallel(list(range(6)), _call_encode, max_workers=6)
    assert counts == [40] * 6
    assert max_concurrent_observed == 1
    assert all(bs == 40 for bs in observed_batch_sizes)


def test_hitl_and_stage7_mutexes_enforce_mutual_exclusion() -> None:
    """Verify atomic HITL barrier and Stage 7 publication lock prevent overlapping entry."""
    profile = get_m5_hardware_profile()
    assert profile.cpu_workers >= 1
    assert profile.mps_batch_size == 64

    in_critical = 0
    max_in_critical = 0
    state_lock = threading.Lock()

    def _enter_stage7() -> str:
        nonlocal in_critical, max_in_critical
        with stage7_publication_mutex(), atomic_hitl_surfacing_barrier():
            with state_lock:
                in_critical += 1
                max_in_critical = max(max_in_critical, in_critical)
            time.sleep(0.003)
            with state_lock:
                in_critical -= 1
        return "ok"

    tasks = {f"t_{i}": _enter_stage7 for i in range(6)}
    outcomes = run_named_tasks_parallel(tasks, max_workers=6)
    assert len(outcomes) == 6
    assert max_in_critical == 1


def test_resolve_optimal_parallel_workers_m5_pro_48gb_and_cross_process_lock() -> None:
    """Verify M5 Pro 48GB auto-scaling worker resolution and cross-process Stage 7 file lock."""
    profile = get_m5_hardware_profile()
    assert profile.unified_memory_gb == 48
    assert profile.performance_cores == 12
    assert profile.max_parallel_ingest_workers == 16

    # Auto-scaled workers with no task cap -> between 8 and 16 on 48GB profile
    auto_workers = resolve_optimal_parallel_workers(None)
    assert 4 <= auto_workers <= 16

    # Auto-scaled workers bounded by task_count
    assert resolve_optimal_parallel_workers(None, task_count=3) == 3
    assert resolve_optimal_parallel_workers(99, task_count=20) == 16
    assert resolve_optimal_parallel_workers(6, task_count=2) == 2

    # Cross-process file lock in stage7_publication_mutex(runtime_root)
    with tempfile.TemporaryDirectory(prefix="eval_sandbox_stage7_lock_") as tmp_dir:
        rt_root = Path(tmp_dir)
        active = 0
        max_active = 0
        lock = threading.Lock()

        def _worker() -> str:
            nonlocal active, max_active
            with stage7_publication_mutex(rt_root):
                with lock:
                    active += 1
                    max_active = max(max_active, active)
                time.sleep(0.004)
                with lock:
                    active -= 1
            return "done"

        res = run_named_tasks_parallel({f"w_{i}": _worker for i in range(4)}, max_workers=4)
        assert len(res) == 4
        assert max_active == 1
        assert (rt_root / "stage7_publication.lock").exists()

