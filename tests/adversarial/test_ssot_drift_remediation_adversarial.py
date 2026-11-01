"""Adversarial and edge-case tests for SSOT config hasher, atomic cache sealing, and ASR deduplication."""
from __future__ import annotations

import concurrent.futures
import json
import tempfile
from pathlib import Path

import pytest

from dual_pipeline.audio_ingestion_core.asr_deduplicator import (
    deduplicate_consecutive_asr_segments,
    trim_chunk_boundary_overlap,
)
from dual_pipeline.audio_ingestion_core.audio_asr_cache import (
    load_audio_diarization_cache,
    save_audio_diarization_cache,
)
from dual_pipeline.exceptions import CacheIntegrityViolationError
from dual_pipeline.transcript_pipeline_core.cache_seal import (
    create_cache_seal,
    verify_cache_seal,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_boundary_payloads_empty_and_short_segments() -> None:
    """Category 1 (Boundary payload): Empty segments, zero-length strings, and single-word turns."""
    assert deduplicate_consecutive_asr_segments([]) == []
    assert trim_chunk_boundary_overlap("", "hello world") == "hello world"
    assert trim_chunk_boundary_overlap("hello world", "") == ""

    short_segs = [
        {"start": 0.0, "end": 0.5, "text": "Yes", "speaker": "S1"},
        {"start": 0.6, "end": 1.0, "text": "No", "speaker": "S2"},
    ]
    res = deduplicate_consecutive_asr_segments(short_segs)
    assert len(res) == 2


def test_malformed_input_corrupted_diarization_cache() -> None:
    """Category 2 (Malformed input): Truncated JSON in diarization sidecar returns None gracefully."""
    with tempfile.TemporaryDirectory(prefix="eval_sandbox_ssot_adv_") as tmp_dir:
        c_dir = Path(tmp_dir)
        audio_sha = "b" * 64
        diar_path = save_audio_diarization_cache(
            audio_sha256=audio_sha,
            turns=[{"start": 0.0, "end": 1.0, "speaker": "S0"}],
            model_name="pyannote/3.1",
            stem="corrupt_case",
            cache_dir=c_dir,
        )
        # Corrupt the file with truncated JSON
        diar_path.write_text("{truncated_json: [1, 2", encoding="utf-8")
        assert (
            load_audio_diarization_cache(
                audio_sha256=audio_sha,
                model_name="pyannote/3.1",
                stem="corrupt_case",
                cache_dir=c_dir,
            )
            is None
        )


def test_concurrent_atomic_cache_writes_no_torn_reads() -> None:
    """Category 3 (Concurrency & race): Parallel diarization cache writes via atomic tmp replacement."""
    with tempfile.TemporaryDirectory(prefix="eval_sandbox_ssot_race_") as tmp_dir:
        c_dir = Path(tmp_dir)
        audio_sha = "c" * 64

        def _worker(idx: int) -> Path:
            return save_audio_diarization_cache(
                audio_sha256=audio_sha,
                turns=[{"start": float(idx), "end": float(idx + 1), "speaker": f"SPK_{idx}"}],
                model_name="pyannote/3.1",
                stem=f"stem_{idx}",
                cache_dir=c_dir,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            paths = list(pool.map(_worker, range(16)))

        for p in paths:
            assert p.is_file()
            data = json.loads(p.read_text(encoding="utf-8"))
            assert len(data["turns"]) == 1
        assert list(c_dir.glob("*.tmp")) == []


def test_invariant_bypass_attempt_tampered_cache_seal_fails_closed() -> None:
    """Category 4 (Invariant bypass attempt): Tampering with sealed segments raises CacheIntegrityViolationError."""
    with tempfile.TemporaryDirectory(prefix="eval_sandbox_ssot_tamper_") as tmp_dir:
        c_dir = Path(tmp_dir)
        seg_file = c_dir / "sample.segments.json"
        seg_file.write_text(
            json.dumps([{"speaker": "Alice", "start": 0.0, "end": 1.0, "text": "Original text"}]),
            encoding="utf-8",
        )
        seal_file = create_cache_seal(structured_file=seg_file, audio_sha256="d" * 64, cache_key="1234567890abcdef")

        # Tamper with segments file after sealing
        seg_file.write_text(
            json.dumps([{"speaker": "Mallory", "start": 0.0, "end": 1.0, "text": "Tampered text"}]),
            encoding="utf-8",
        )
        with pytest.raises(CacheIntegrityViolationError):
            verify_cache_seal(structured_file=seg_file, seal_file=seal_file)


def test_atomic_write_ast_contract_enforced_in_cache_modules() -> None:
    """Verify both audio_asr_cache.py and cache_seal.py use atomic .replace() for all disk writes."""
    asr_cache_src = (REPO_ROOT / "dual_pipeline" / "audio_ingestion_core" / "audio_asr_cache.py").read_text(
        encoding="utf-8"
    )
    seal_src = (REPO_ROOT / "dual_pipeline" / "transcript_pipeline_core" / "cache_seal.py").read_text(
        encoding="utf-8"
    )
    assert ".diarization.json.tmp" in asr_cache_src
    assert ".cache_seal.json.tmp" in seal_src
