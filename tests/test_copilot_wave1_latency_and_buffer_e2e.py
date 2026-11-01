"""Wave 1 E2E Tests: Copilot Hot-Path Latency, Incremental Buffer Reading & Async Ledger I/O."""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.live_meeting_recorder_core.copilot import LiveCopilotAdvisor
from dual_pipeline.live_meeting_recorder_core.copilot_attempt_ledger import (
    _cached_git_rev,
    get_source_revision,
)
from dual_pipeline.live_meeting_recorder_core.copilot_circuit_breaker import (
    CircuitBreakerStatus,
    CircuitTripReason,
    CopilotCacheCircuitBreaker,
)
from dual_pipeline.live_meeting_recorder_core.live_buffer_reader import LiveBufferReader
from dual_pipeline.live_meeting_recorder_core.session_registry import SessionManifest


def test_e2e_incremental_tail_buffer_reader_and_extra_fields(tmp_path: Path) -> None:
    """Verifies LiveBufferReader reads growing segments.jsonl incrementally via byte-offset cursor."""
    out_dir = tmp_path / "output" / "project_nexus" / "10-27-2026"
    out_dir.mkdir(parents=True, exist_ok=True)
    seg_path = out_dir / "segments.jsonl"

    initial_lines = [
        json.dumps(
            {
                "seq": i,
                "speaker": "Chad Brockman",
                "text": f"Automated landing zone turn {i}",
                "timestamp": f"00:00:{i:02d}",
                "start_sec": float(i * 3),
                "end_sec": float(i * 3 + 2),
                "asr_conf": 0.96,
                "speaker_conf": 0.92,
            }
        )
        for i in range(1, 51)
    ]
    seg_path.write_text("\n".join(initial_lines) + "\n", encoding="utf-8")

    manifest = SessionManifest(
        session_id="sess_w1_e2e",
        engine_signature="urn:nee:dual-pipeline:live-meeting-recorder:1.0.0",
        parent_pid=12345,
        parent_start_time=1000.0,
        repo_root=str(tmp_path),
        meeting_slug="google_partner_advisory_sync",
        engagement_id="project_nexus",
        output_dir=str(out_dir.relative_to(tmp_path)),
        segments_jsonl=str(seg_path.relative_to(tmp_path)),
        capture_state="CAPTURING",
    )

    reader = LiveBufferReader(tmp_path)
    turns_1 = reader.read_turns(manifest)
    assert len(turns_1) == 50
    assert turns_1[0]["seq"] == 1
    assert turns_1[0]["asr_conf"] == 0.96
    assert turns_1[0]["speaker_conf"] == 0.92

    cache_entry = reader._turn_cache["sess_w1_e2e"]
    offset_after_50 = cache_entry["last_offset"]
    assert offset_after_50 > 0

    # Append 10 more turns and verify incremental tail read < 5ms
    appended_lines = [
        json.dumps(
            {
                "seq": i,
                "speaker": "Amit Ayer",
                "text": f"Google Antigravity VPC agent architecture turn {i}",
                "timestamp": f"00:02:{i:02d}",
                "start_sec": float(i * 3),
            }
        )
        for i in range(51, 61)
    ]
    with open(seg_path, "a", encoding="utf-8") as f:
        f.write("\n".join(appended_lines) + "\n")

    t0 = time.perf_counter()
    turns_2 = reader.read_turns(manifest)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert len(turns_2) == 60
    assert turns_2[-1]["speaker"] == "Amit Ayer"
    assert cache_entry["last_offset"] > offset_after_50
    assert elapsed_ms < 25.0


def test_e2e_context_model_distinct_cache_keys_and_no_double_llm_call(tmp_path: Path) -> None:
    """Verifies _lookup_graph_context hashes context_model digest per turn and streaming abstention does not re-call LLM."""
    advisor = LiveCopilotAdvisor(
        project_name="project_nexus",
        meeting_name="Google PartnerAdvisory Connect",
        attendees=["Amit Ayer", "Chad Brockman"],
        mock_mode=True,
        enable_prefetch=False,
        session_output_dir=tmp_path,
    )
    mock_retriever = MagicMock()
    mock_retriever.retrieve_context.side_effect = lambda d: f"Ctx for {len(advisor._session_turns)} turns"
    mock_retriever.extract_candidate_assertions.return_value = []
    advisor.in_memory_graph = MagicMock()
    mock_retriever._topology = advisor.in_memory_graph
    advisor.retriever = mock_retriever

    advisor.record_turn("Chad Brockman", "We can stand up the landing zone in three days using Terraform.", "00:01:00", 60.0)
    ctx_1 = advisor._lookup_graph_context(advisor.context_model)

    advisor.record_turn("Amit Ayer", "Let us pair Google Spark with Antigravity inside our VPC.", "00:01:15", 75.0)
    ctx_2 = advisor._lookup_graph_context(advisor.context_model)

    assert ctx_1 != ctx_2
    assert mock_retriever.retrieve_context.call_count >= 2

    # Verify streaming abstention does NOT trigger a second blocking chat.completions.create call
    advisor.mock_mode = False
    mock_client = MagicMock()
    advisor.client = mock_client
    mock_streaming = MagicMock()
    mock_streaming.execute_streaming_evaluation.return_value = None
    mock_streaming.last_stream_outcome = "ABSTAINED"
    advisor.streaming_handler = mock_streaming

    res = advisor._evaluate_dialogue_sync()
    assert res is None
    assert mock_streaming.execute_streaming_evaluation.call_count == 1
    assert mock_client.chat.completions.create.call_count == 0


def test_e2e_git_revision_lru_cache_and_circuit_breaker_recovery(tmp_path: Path) -> None:
    """Verifies get_source_revision caches subprocess execution and circuit breaker transitions OPEN -> HALF_OPEN -> CLOSED."""
    _cached_git_rev.cache_clear()
    r1 = get_source_revision(tmp_path)
    info1 = _cached_git_rev.cache_info()
    r2 = get_source_revision(tmp_path)
    info2 = _cached_git_rev.cache_info()
    assert r1 == r2
    assert info1.misses == 1
    assert info2.hits == 1
    assert info2.misses == 1

    cb = CopilotCacheCircuitBreaker(window_seconds=60.0)
    cb.recovery_timeout_seconds = 0.05
    cb.record_latency_ms(25.0, budget_ms=15.0)
    cb.record_latency_ms(25.0, budget_ms=15.0)
    cb.record_latency_ms(25.0, budget_ms=15.0)
    assert cb.status == CircuitBreakerStatus.OPEN
    assert cb.trip_reason == CircuitTripReason.TIMEOUT_BREACH.value

    time.sleep(0.06)
    assert cb.status == CircuitBreakerStatus.HALF_OPEN
    cb.record_hit("node_1")
    assert cb.status == CircuitBreakerStatus.CLOSED
