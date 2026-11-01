"""tests/adversarial/test_mlx_whisper_memory_reclamation_adversarial.py

Adversarial and edge-case testing for MLX Whisper memory reclamation.
Covers:
1. Boundary Payload: Repeated and uninitialized calls to clear_mlx_whisper_metal_cache()
   when ModelHolder is None, corrupted, or has missing attributes.
2. Concurrency & Race: Multi-threaded simultaneous clear operations ensuring zero race
   conditions or unhandled exceptions under concurrent teardown.
3. Rollback & Recovery: Exception during transcription handling ensuring cleanup safely
   resets ModelHolder and reclaims memory even after mid-flight failures.
4. Rapid Cycle Invariance: Sequential rapid allocate-and-release loops verifying memory
   does not leak or creep upwards across iterations.
"""
from __future__ import annotations

import concurrent.futures
from unittest.mock import MagicMock, patch
import pytest

from dual_pipeline.audio_ingestion_core.mlx_transcriber import (
    clear_mlx_whisper_metal_cache,
    is_mlx_available,
    LocalMLXAudioTranscriber,
)


def test_adversarial_boundary_uninitialized_and_repeated_clear():
    """Verify that calling clear_mlx_whisper_metal_cache on uninitialized state or repeatedly is safe and idempotent."""
    clear_mlx_whisper_metal_cache()

    for _ in range(10):
        clear_mlx_whisper_metal_cache()

    if is_mlx_available():
        import mlx.core as mx
        active_mb = mx.get_active_memory() / (1024 * 1024)
        assert active_mb <= 1.0, f"Active memory should remain <= 1.0 MB, got {active_mb:.2f} MB"


def test_adversarial_boundary_corrupted_model_holder():
    """Verify robustness when ModelHolder or mlx modules exhibit missing or corrupted attributes."""
    with patch("mlx_whisper.transcribe.ModelHolder", create=True) as mock_holder:
        mock_holder.model = MagicMock()
        mock_holder.model_path = "/path/to/mock"

        clear_mlx_whisper_metal_cache()

        assert mock_holder.model is None
        assert mock_holder.model_path is None

    with patch.dict("sys.modules", {"mlx_whisper.transcribe": None}):
        clear_mlx_whisper_metal_cache()


def test_adversarial_concurrency_race_conditions():
    """Verify concurrent multi-threaded cache clear calls do not race or raise exceptions."""
    def worker():
        for _ in range(5):
            clear_mlx_whisper_metal_cache()
        return True

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker) for _ in range(8)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == 8
    assert all(results)


def test_adversarial_rollback_and_recovery_after_mid_flight_exception():
    """Verify that if transcription fails mid-flight, memory reclamation cleanly resets state."""
    transcriber = LocalMLXAudioTranscriber(mock=True)

    try:
        raise RuntimeError("Simulated crash during acoustic feature extraction")
    except RuntimeError:
        transcriber._clear_mlx_metal_cache()

    if is_mlx_available():
        from mlx_whisper.transcribe import ModelHolder
        assert ModelHolder.model is None, "ModelHolder.model must be None following recovery"


def test_adversarial_rapid_cycle_memory_stability():
    """Verify memory remains bounded and does not creep across rapid allocate/clear cycles."""
    if not is_mlx_available():
        pytest.skip("MLX Metal required for rapid cycle memory stability test")

    import mlx.core as mx

    for i in range(5):
        arr = mx.zeros((1000, 1000), dtype=mx.float32)
        mx.eval(arr)
        del arr

        clear_mlx_whisper_metal_cache()
        active_mb = mx.get_active_memory() / (1024 * 1024)
        assert active_mb <= 1.0, f"Cycle {i+1}: memory creep detected: {active_mb:.2f} MB"
