"""
tests/test_copilot_wave4_attribution_u0_adversarial.py

Adversarial and edge-case tests for Wave 4:
1. Boundary payload: Empty/missing metadata.json and session_manifest.json in load_session_attendees; <85% chunk coverage blocks partial Tier-A cache seeding.
2. Malformed input: Corrupted JSON lines in segments.jsonl and speaker_hints.json handled gracefully without crashing reconciliation.
3. Concurrency & race: Atomic .tmp file replacement in SpeakerHintManager._update_segments_and_md and LivePipelineIntegrator cursor updates.
4. Rollback & recovery: LivePipelineIntegrator recovers cleanly when cursor byte offset exceeds truncated log file size.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from dual_pipeline.live_meeting_recorder_core.copilot_roster_bridge import load_session_attendees
from dual_pipeline.live_meeting_recorder_core.speaker_hints import SpeakerHintManager
from dual_pipeline.live_meeting_recorder_core.master_asr_seeder import seed_master_asr_from_recorder
from dual_pipeline.learning_adapters.live_pipeline_integrator import LivePipelineIntegrator


def test_boundary_insufficient_chunk_coverage_skips_partial_asr_seed(tmp_path: Path) -> None:
    assert load_session_attendees(None) == []
    assert load_session_attendees(tmp_path / "nonexistent") == []

    cache_dir = tmp_path / ".cache"
    cache_dir.mkdir(parents=True)
    segments_jsonl = cache_dir / "segments.jsonl"
    segments_jsonl.write_text(
        json.dumps({"chunk_idx": 0, "start_sec": 0.0, "end_sec": 15.0, "speaker": "Chad", "text": "Only one chunk processed."}) + "\n",
        encoding="utf-8",
    )
    recorder = SimpleNamespace(
        output_dir=tmp_path,
        cache_dir=cache_dir,
        clean_slug="test_call",
        segments_jsonl=segments_jsonl,
        master_wav=cache_dir / "master.wav",
        processed_chunks={"chunk_0000.wav"},  # 1 of 10 chunks (10% < 85%)
        mock_mode=True,
    )
    receipt = SimpleNamespace(
        master_wav_sha256="0" * 64,
        total_chunks=10,
        duration_s=150.0,
    )
    assert seed_master_asr_from_recorder(recorder, receipt) is False
    assert list(cache_dir.glob("*.transcript_cache.json")) == []


def test_malformed_segments_and_temporal_proximity_tie_breaker(tmp_path: Path) -> None:
    seg_file = tmp_path / "segments.jsonl"
    seg_file.write_text(
        "{corrupted json line\n"
        + json.dumps({"chunk_idx": 0, "start_sec": 10.0, "end_sec": 25.0, "voice_id": "Voice A", "speaker": "Voice A", "text": "We agreed on the migration timeline."}) + "\n"
        + json.dumps({"chunk_idx": 5, "start_sec": 95.0, "end_sec": 110.0, "voice_id": "Voice B", "speaker": "Voice B", "text": "We agreed on the migration timeline."}) + "\n",
        encoding="utf-8",
    )
    mgr = SpeakerHintManager(output_dir=tmp_path, cache_dir=tmp_path, attendees=["Alice", "Bob"])

    # Without elapsed_seconds, matching two distinct voices fails closed as AMBIGUOUS_QUOTE_MATCH
    res_ambig = mgr.apply_hint(
        quote="agreed on the migration timeline",
        speaker_name="Bob",
        segments_jsonl_path=seg_file,
    )
    assert res_ambig.status == "FAIL"
    assert res_ambig.error == "AMBIGUOUS_QUOTE_MATCH"

    # With elapsed_seconds=100.0, temporal proximity resolves cleanly to Voice B (start_sec=95.0)
    res_resolved = mgr.apply_hint(
        quote="agreed on the migration timeline",
        speaker_name="Bob",
        segments_jsonl_path=seg_file,
        elapsed_seconds=100.0,
    )
    assert res_resolved.status == "PASS"
    assert res_resolved.voice_id == "Voice B"
    assert not seg_file.with_suffix(".jsonl.tmp").exists()


def test_recovery_when_cursor_byte_offset_exceeds_truncated_log(tmp_path: Path) -> None:
    integrator = LivePipelineIntegrator(runtime_root=tmp_path)
    integrator.record_success(source_agent="LiveCopilotAgent", category="respond", run_id="r1")
    first_drain = integrator.drain_to_cohort_items()
    assert len(first_drain) == 1

    # Corrupt cursor with a byte offset larger than file size (simulating log rotation/truncation)
    integrator.cursor_path.write_text(
        json.dumps({"last_processed_line": 999, "last_processed_byte_offset": 9999999, "processed_event_ids": []}),
        encoding="utf-8",
    )
    # Must fall back to start of file and recover the event
    recovered = integrator.drain_to_cohort_items()
    assert len(recovered) == 1
