"""tests/end_to_end/test_mlx_whisper_memory_reclamation_e2e.py

End-to-End lifecycle test verifying MLX Whisper Metal VRAM reclamation and transcript parity.
Validates:
1. Active memory reclamation from ~2.94 GB down to <= 1.0 MB post-transcription.
2. Complete transcript parity across consecutive reload and reclamation cycles.
3. Clean ModelHolder state reset.
"""
from __future__ import annotations

import glob
from pathlib import Path
import pytest

from dual_pipeline.audio_ingestion_core.mlx_transcriber import (
    LocalMLXAudioTranscriber,
    clear_mlx_whisper_metal_cache,
    is_mlx_available,
)


def _find_sample_wav() -> Path | None:
    p = Path(__file__).resolve().parent.parent.parent
    if ".worktrees" in p.parts:
        idx = p.parts.index(".worktrees")
        primary_root = Path(*p.parts[:idx])
    else:
        primary_root = p

    wavs = glob.glob(str(primary_root / "output" / "transcript_cache" / "*.wav"))
    if not wavs:
        return None
    return Path(min(wavs, key=lambda f: Path(f).stat().st_size))


@pytest.mark.skipif(not is_mlx_available(), reason="Apple Silicon Metal GPU required for MLX memory test")
def test_mlx_whisper_memory_reclamation_lifecycle():
    """Verify active Metal memory drops <= 1 MB post-transcription and output remains 100% identical."""
    import mlx.core as mx
    import mlx_whisper
    from mlx_whisper.transcribe import ModelHolder

    wav_file = _find_sample_wav()
    assert wav_file is not None and wav_file.is_file(), "Authentic WAV fixture must exist"

    # Pre-clean
    clear_mlx_whisper_metal_cache()
    pre_active = mx.get_active_memory() / (1024 * 1024)
    assert pre_active <= 5.0, f"Pre-test active memory should be near zero, got {pre_active:.2f} MB"

    transcriber = LocalMLXAudioTranscriber(mock=False)

    # First cycle
    res1 = mlx_whisper.transcribe(str(wav_file), path_or_hf_repo=transcriber.model_name)
    text1 = res1.get("text", "")
    assert len(text1) > 0, "Transcription should return non-empty text"

    mid_active = mx.get_active_memory() / (1024 * 1024)
    assert mid_active >= 500.0, f"Model weights should occupy Metal active memory, got {mid_active:.2f} MB"

    # Execute reclamation
    transcriber._clear_mlx_metal_cache()
    post_clean_active = mx.get_active_memory() / (1024 * 1024)
    assert ModelHolder.model is None, "ModelHolder.model must be None after clear"
    assert post_clean_active <= 1.0, f"Active memory must drop <= 1.0 MB, got {post_clean_active:.2f} MB"

    # Second cycle (verify on-demand reload and parity)
    res2 = mlx_whisper.transcribe(str(wav_file), path_or_hf_repo=transcriber.model_name)
    text2 = res2.get("text", "")
    assert text1 == text2, "Second transcription output must be identical to first (zero degradation)"

    # Final reclamation
    clear_mlx_whisper_metal_cache()
    final_active = mx.get_active_memory() / (1024 * 1024)
    assert final_active <= 1.0, f"Final active memory must drop <= 1.0 MB, got {final_active:.2f} MB"
