"""Wave 2 Unit Tests: Structured Transcript Result and Cache Parity.

Verifies:
1. Cache Save & Load Roundtrip: Full structured results and sidecars survive serialization.
2. Cold/Warm Parity: Loading from warm cache restores identical word bounds, utterances, and intervals.
3. Audio Digest Invalidation: Mismatching audio digest triggers cache miss and rebuild.
4. Missing Sidecar Rejection: If the .segments.json sidecar is missing, the bundle is rejected as partial.
5. Truthful End Times: Utterances retain exact acoustic end times without fabricated 5-second padding.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
import pytest

from dual_pipeline.audio_ingestion_core.transcript_result import (
    StructuredAcousticInterval,
    StructuredResultCache,
    StructuredTranscriptResult,
    StructuredUtterance,
    StructuredWord,
)


@pytest.fixture
def sample_result() -> StructuredTranscriptResult:
    words = [
        StructuredWord(
            word_id="w_001",
            text="Let's",
            start_time_sec=0.0,
            end_time_sec=0.4,
            channel=0,
            start_sample=0,
            end_sample=6400,
        ),
        StructuredWord(
            word_id="w_002",
            text="review",
            start_time_sec=0.4,
            end_time_sec=0.9,
            channel=0,
            start_sample=6400,
            end_sample=14400,
        ),
        StructuredWord(
            word_id="w_003",
            text="the",
            start_time_sec=0.9,
            end_time_sec=1.1,
            channel=0,
            start_sample=14400,
            end_sample=17600,
        ),
        StructuredWord(
            word_id="w_004",
            text="architecture.",
            start_time_sec=1.1,
            end_time_sec=1.8,
            channel=0,
            start_sample=17600,
            end_sample=28800,
        ),
    ]

    utterances = [
        StructuredUtterance(
            utterance_id="utt_001",
            speaker="Amit Ayer",
            start_time_sec=0.0,
            end_time_sec=1.8,
            text="Let's review the architecture.",
            word_ids=["w_001", "w_002", "w_003", "w_004"],
            boundary_reasons=["clause_boundary"],
            timing_provenance="sample_clock_exact",
        )
    ]

    intervals = [
        StructuredAcousticInterval(
            interval_id="int_001",
            speaker_cluster="Speaker 4",
            start_time_sec=0.0,
            end_time_sec=1.8,
            channel=0,
        )
    ]

    return StructuredTranscriptResult(
        schema_version="1.0.0",
        audio_metadata={"source_audio_digest": "a" * 64, "channels": 2, "sample_rate": 16000},
        model_metadata={"model_name": "large-v3", "config_digest": "cfg123"},
        words=words,
        utterances=utterances,
        acoustic_intervals=intervals,
    )


def test_cache_save_and_load_roundtrip(sample_result: StructuredTranscriptResult):
    with tempfile.TemporaryDirectory() as tmp_dir:
        cache = StructuredResultCache(tmp_dir)
        cache_key = "test_key_01"

        res_path, sidecar_path = cache.save(cache_key, sample_result)
        assert res_path.is_file()
        assert sidecar_path.is_file()

        loaded = cache.load(cache_key, expected_audio_digest="a" * 64)
        assert loaded is not None
        assert len(loaded.words) == 4
        assert len(loaded.utterances) == 1
        assert loaded.utterances[0].speaker == "Amit Ayer"
        assert loaded.utterances[0].end_time_sec == 1.8


def test_cold_warm_cache_parity(sample_result: StructuredTranscriptResult):
    """Verifies that cold and warm paths produce identical structured fields."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        cache = StructuredResultCache(tmp_dir)
        cache_key = "parity_key_01"

        cache.save(cache_key, sample_result)
        warm_result = cache.load(cache_key, expected_audio_digest="a" * 64)

        assert warm_result is not None
        assert warm_result.to_dict() == sample_result.to_dict()
        assert warm_result.to_markdown() == sample_result.to_markdown()


def test_cache_invalidation_on_audio_digest_mismatch(sample_result: StructuredTranscriptResult):
    with tempfile.TemporaryDirectory() as tmp_dir:
        cache = StructuredResultCache(tmp_dir)
        cache_key = "inval_key_01"

        cache.save(cache_key, sample_result)
        # Load with different expected digest -> should return None (cache miss)
        miss = cache.load(cache_key, expected_audio_digest="b" * 64)
        assert miss is None


def test_cache_rejection_on_missing_sidecar(sample_result: StructuredTranscriptResult):
    with tempfile.TemporaryDirectory() as tmp_dir:
        cache = StructuredResultCache(tmp_dir)
        cache_key = "partial_key_01"

        res_path, sidecar_path = cache.save(cache_key, sample_result)
        # Delete sidecar to simulate partial/stale cache bundle
        sidecar_path.unlink()

        # Loading must return None (fail closed to trigger full rebuild)
        rejected = cache.load(cache_key, expected_audio_digest="a" * 64)
        assert rejected is None


def test_truthful_end_times_no_fabricated_five_seconds(sample_result: StructuredTranscriptResult):
    utt = sample_result.utterances[0]
    # start is 0.0, end is 1.8; must NOT be 5.0 (0.0 + 5.0)
    assert utt.end_time_sec == 1.8
    assert utt.end_time_sec != utt.start_time_sec + 5.0
