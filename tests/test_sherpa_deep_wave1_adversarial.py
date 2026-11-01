"""Adversarial and edge-case test suite for Wave 1 audio and container hardening."""
import json
import os
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.audio_ingestion_core.asr_deduplicator import (
    deduplicate_consecutive_asr_segments,
    trim_chunk_boundary_overlap,
)
from dual_pipeline.audio_ingestion_core.diarization_engine import (
    DiarizationEngine,
    get_diarization_engine,
)
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.master_asr_seeder import (
    _parse_segment_entry,
    seed_master_asr_from_recorder,
)
from dual_pipeline.naming import (
    is_live_meeting_container,
    resolve_meeting_container_dir,
    resolve_meeting_container_slug,
    standardize_meeting_slug,
)


class TestSherpaDeepWave1Adversarial:
    """Adversarial tests covering boundary, malformed, concurrency, and exhaustion edge cases."""

    def test_chunk_boundary_overlap_adversarial_patterns(self):
        """Verifies boundary overlap trimmer against exact, partial, empty, and single-word tokens."""
        # Exact suffix-prefix overlap (3 words)
        prev = "we need to align on the technical roadmap"
        curr = "the technical roadmap will be reviewed tomorrow"
        trimmed = trim_chunk_boundary_overlap(prev, curr, min_overlap_words=2)
        assert trimmed == "will be reviewed tomorrow"

        # Complete duplicate across chunk boundary
        prev_dup = "this is completely repeated text"
        curr_dup = "this is completely repeated text"
        trimmed_dup = trim_chunk_boundary_overlap(prev_dup, curr_dup, min_overlap_words=2)
        assert trimmed_dup == ""

        # Single word overlap below threshold (min_overlap_words=2)
        prev_single = "and that concludes the meeting"
        curr_single = "meeting minutes will follow"
        trimmed_single = trim_chunk_boundary_overlap(prev_single, curr_single, min_overlap_words=2)
        assert trimmed_single == "meeting minutes will follow"

        # Empty inputs
        assert trim_chunk_boundary_overlap("", "some text") == "some text"
        assert trim_chunk_boundary_overlap("some text", "") == ""
        assert trim_chunk_boundary_overlap("", "") == ""

    def test_asr_deduplicator_handles_boundary_trimming_and_word_drops(self):
        """Verifies deduplicate_consecutive_asr_segments drops leading words when trimming."""
        segments = [
            {
                "start": 0.0,
                "end": 2.0,
                "text": "let us begin our discussion",
                "words": [
                    {"word": "let", "start": 0.0, "end": 0.5},
                    {"word": "us", "start": 0.5, "end": 0.8},
                    {"word": "begin", "start": 0.8, "end": 1.4},
                    {"word": "our", "start": 1.4, "end": 1.7},
                    {"word": "discussion", "start": 1.7, "end": 2.0},
                ],
            },
            {
                "start": 1.5,
                "end": 3.5,
                "text": "our discussion covers cloud migration",
                "words": [
                    {"word": "our", "start": 1.5, "end": 1.8},
                    {"word": "discussion", "start": 1.8, "end": 2.2},
                    {"word": "covers", "start": 2.2, "end": 2.8},
                    {"word": "cloud", "start": 2.8, "end": 3.1},
                    {"word": "migration", "start": 3.1, "end": 3.5},
                ],
            },
        ]

        deduped = deduplicate_consecutive_asr_segments(segments)
        assert len(deduped) == 2
        assert deduped[0]["text"] == "let us begin our discussion"
        assert deduped[1]["text"] == "covers cloud migration"
        assert len(deduped[1]["words"]) == 3
        assert [w["word"] for w in deduped[1]["words"]] == ["covers", "cloud", "migration"]

    def test_seeder_boundary_and_malformed_entries(self, tmp_path: Path):
        """Verifies seeder ignores malformed or missing fields without throwing."""
        # Empty string
        assert _parse_segment_entry("") is None
        assert _parse_segment_entry("   \n") is None

        # Missing words or timestamps
        entry = _parse_segment_entry(json.dumps({"text": "Just text"}))
        assert entry is not None
        assert entry["text"] == "Just text"
        assert entry["start"] == 0.0
        assert "words" not in entry

        # Empty segments file
        empty_jsonl = tmp_path / "empty.jsonl"
        empty_jsonl.write_text("", encoding="utf-8")
        rec = MagicMock()
        rec.segments_jsonl = empty_jsonl
        receipt = MagicMock()
        receipt.master_wav_sha256 = "1234567890abcdef"
        assert seed_master_asr_from_recorder(rec, receipt) is False

    def test_diarization_engine_lazy_and_offline_safety(self):
        """Verifies DiarizationEngine(lazy=True) does not perform network operations."""
        import huggingface_hub.constants
        assert huggingface_hub.constants.HF_HUB_OFFLINE is True

        engine = DiarizationEngine(lazy=True)
        assert engine._initialized is False
        assert engine.pipeline is None

        # Verify get_diarization_engine with lazy=True
        eng = get_diarization_engine(force_refresh=True, lazy=True)
        assert eng._initialized is False

    def test_naming_adversarial_slugs_and_deconfliction(self, tmp_path: Path):
        """Verifies container detection on deep nested paths and forbidden tokens."""
        # Forbidden tokens
        assert is_live_meeting_container(tmp_path / "10-14-2026") is False
        assert is_live_meeting_container(tmp_path / ".cache") is False
        assert is_live_meeting_container(tmp_path / "chunks") is False
        assert is_live_meeting_container(tmp_path / "transcript_cache") is False

        # Non-directory path
        assert is_live_meeting_container(tmp_path / "non_existent") is False

        # Standardize slug length and characters
        slug = standardize_meeting_slug("A" * 100, project_name="test_proj")
        assert len(slug) <= 40
        assert not slug.startswith(".")

    def test_recorder_lock_release_idempotence(self, tmp_path: Path):
        """Verifies _release_slug_lock() is idempotent and handles multiple calls."""
        rec = LiveMeetingRecorder.__new__(LiveMeetingRecorder)
        rec._slug_lock_fd = None
        rec._release_slug_lock() # Should not raise

        lock_file = tmp_path / ".flock"
        lock_file.write_text("lock", encoding="utf-8")
        fd = os.open(str(lock_file), os.O_RDWR)
        rec._slug_lock_fd = fd
        rec._release_slug_lock()
        assert rec._slug_lock_fd is None

        # Second call must be a safe no-op
        rec._release_slug_lock()
        assert rec._slug_lock_fd is None
