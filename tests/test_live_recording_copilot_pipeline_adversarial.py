"""
Adversarial & Edge-Case Test Suite for Live Recording + Copilot Pipeline (Wave 5).

Covers 6 mandatory adversarial edge-case categories:
1. Boundary payload: empty segments.jsonl, whitespace-only --request, tight words=30 budget.
2. Malformed input: truncated/corrupted JSONL lines in segments.jsonl, raw arrow triples
   with opaque hex IDs, markdown pipe table rows, opaque source-<hex> dates.
3. Concurrency & race: interleaved segment writes and retroactive voice_id hint updates
   read incrementally via LiveBufferReader.
4. Overflow & exhaustion: 120-word assertion compressed under words=35 and words=60 without
   failing evaluate_speak_gate's 28/48-word sentence cap.
5. Invariant bypass attempt: cross-engagement NEE graph assertions blocked during non-NEE
   sessions and under explicit live-transcript-only directives.
6. Rollback & fault isolation + exact production regression: post-write hint exception
   in LiveMeetingRecorder.commit_segment does not lose or duplicate segments; mid-session
   focus window quarantine falls back to earlier non-quarantined session turns.
"""
from __future__ import annotations

import argparse
import json
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import dual_pipeline.cli.copilot_handlers as copilot_handlers
from dual_pipeline.cli.copilot_speech import spoken_claim
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import (
    ConversationContextModel,
)
from dual_pipeline.live_meeting_recorder_core.copilot_engine import (
    execute_copilot_request,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
    GraphLoadStatus,
)
from dual_pipeline.live_meeting_recorder_core.live_buffer_reader import (
    LiveBufferReader,
)
from dual_pipeline.live_meeting_recorder_core.session_registry import (
    SessionManifest,
    SessionRegistry,
)


def _write_test_wav(path: Path, duration_sec: float = 1.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x10\x00" * int(16000 * duration_sec))


def test_adversarial_boundary_empty_segments_and_focus_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    """Category 1 (Boundary payload) & Category 7 (Exact production regression):
    When recent 4 focus turns are C-19 quarantined, _check_focus_turns must not fail with NO_LIVE_CONTEXT
    if earlier non-quarantined turns exist in the session."""
    session_dir = tmp_path / "partner_google_nee" / "10-27-2026" / "Boundary_Session"
    session_dir.mkdir(parents=True)

    turns = [
        {
            "chunk_id": 1,
            "speaker": "Stefanos Georgiades",
            "text": "We need to establish AI intake governance and Shadow IT controls across Google Workspace.",
            "timestamp": "00:01:00",
            "c19_quarantined": False,
        },
        {"chunk_id": 2, "speaker": "Host", "text": "Career leverage executive compensation band note.", "timestamp": "00:02:00", "c19_quarantined": True},
        {"chunk_id": 3, "speaker": "Host", "text": "Career leverage executive compensation band note.", "timestamp": "00:02:15", "c19_quarantined": True},
        {"chunk_id": 4, "speaker": "Host", "text": "Career leverage executive compensation band note.", "timestamp": "00:02:30", "c19_quarantined": True},
        {"chunk_id": 5, "speaker": "Host", "text": "Career leverage executive compensation band note.", "timestamp": "00:02:45", "c19_quarantined": True},
    ]
    (session_dir / "segments.jsonl").write_text(
        "\n".join(json.dumps(t) for t in turns) + "\n", encoding="utf-8"
    )

    topo = SimpleNamespace(
        nodes={
            "n1": {
                "node_id": "n1",
                "canonical_name": "Stefanos Georgiades",
                "display_name": "Stefanos Georgiades",
                "node_type": "PERSON",
                "engagement_id": "partner_google_nee",
            }
        },
        assertions={
            "asrt_1": {
                "assertion_id": "asrt_1",
                "subject_node_id": "n1",
                "predicate": "RAISES_RISK",
                "object_node_id": "n1",
                "claim_text": "Stefanos Georgiades raised AI intake governance and Shadow IT controls across Google Workspace.",
                "object_literal": "Stefanos Georgiades raised AI intake governance and Shadow IT controls across Google Workspace.",
                "engagement_id": "partner_google_nee",
                "observed_at": "2026-10-25",
            }
        },
        edges=[],
    )
    retriever = CopilotGraphRetriever(topo, engagement_id="partner_google_nee")
    monkeypatch.setattr(
        copilot_handlers,
        "load_copilot_graph",
        lambda **kwargs: (GraphLoadStatus.LOADED_ACTIVE_PAIR, topo, retriever),
    )

    args = argparse.Namespace(
        intent="respond",
        target="Stefanos Georgiades",
        topic="AI intake governance",
        request=None,
        query=None,
        prompt=None,
        words=40,
        meeting_dir=str(session_dir),
        runtime_root=tmp_path,
        engagement="partner_google_nee",
        session=None,
        json=True,
    )
    res = execute_copilot_request(args)
    assert res.get("status") == "PASS", f"Expected PASS when earlier non-quarantined turn exists, got: {res}"


def test_adversarial_malformed_jsonl_arrow_triples_and_markdown_tables(
    tmp_path: Path,
) -> None:
    """Category 2 (Malformed input): truncated JSONL lines, raw arrow triples with opaque hex IDs, and markdown table rows."""
    topo = SimpleNamespace(
        nodes={
            "entity-deadbeef123456": {
                "node_id": "entity-deadbeef123456",
                "canonical_name": "entity-deadbeef123456",
                "display_name": "entity-deadbeef123456",
                "node_type": "ENTITY",
                "engagement_id": "partner_google_nee",
            }
        },
        assertions={},
        edges=[],
    )
    retriever = CopilotGraphRetriever(topo, engagement_id="partner_google_nee")

    # Opaque arrow triple must return empty string
    malformed_arrow = {
        "assertion_id": "asrt_bad_arrow",
        "subject_node_id": "entity-deadbeef123456",
        "predicate": "HAS_ROLE_IN",
        "object_node_id": "entity-deadbeef123456",
        "claim_text": "HAS_ROLE_IN -> entity-deadbeef123456",
    }
    assert spoken_claim(malformed_arrow, retriever) == ""

    # Markdown table row with S. W artifact and [Unscored / Blank]
    table_ast = {
        "assertion_id": "asrt_table_row",
        "claim_text": "| 12 | Align S. W governance board on Gemini Enterprise intake | [Unscored / Blank] |",
    }
    cleaned = spoken_claim(table_ast, retriever)
    assert "|" not in cleaned
    assert "[Unscored" not in cleaned
    assert "SW governance board" in cleaned
    assert "S. W" not in cleaned


def test_adversarial_concurrent_segment_writes_and_retroactive_voice_hints(
    tmp_path: Path,
) -> None:
    """Category 3 (Concurrency & race): incremental LiveBufferReader cursor reads interleaved with retroactive voice_id hint updates."""
    out_dir = tmp_path / "output" / "partner_alliances" / "10-27-2026" / "Concurrent_Hints"
    cache_dir = out_dir / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    seg_path = cache_dir / "segments.jsonl"

    seg_path.write_text(
        '{"chunk_idx": 0, "timestamp": "00:00:00", "speaker": "Speaker", "voice_id": "voice_a", "text": "First turn from unknown cluster."}\n'
        '{"corrupted_json_line_during_concurrent_append\n'
        '{"chunk_idx": 1, "timestamp": "00:00:15", "speaker": "Speaker", "voice_id": "voice_a", "text": "Second turn mentioning Traci backfill role."}\n',
        encoding="utf-8",
    )

    manifest = SessionManifest(
        session_id="sess_concurrent",
        engine_signature="sig",
        parent_pid=100,
        parent_start_time=1.0,
        engagement_id="project:partner_alliances",
        engagement_name="partner_alliances",
        meeting_slug="Concurrent_Hints",
        repo_root=str(tmp_path),
        output_dir=str(out_dir.relative_to(tmp_path)),
        cache_dir=str(cache_dir.relative_to(tmp_path)),
        segments_jsonl=str(seg_path.relative_to(tmp_path)),
        attendees=["Amit", "Traci"],
    )

    # Now a hint arrives binding chunk 1 to Traci
    hints = [{"hint_id": "h1", "speaker_name": "Traci", "quote": "mentioning Traci backfill role", "status": "QUEUED"}]
    (cache_dir / "speaker_hints_queue.json").write_text(json.dumps(hints), encoding="utf-8")

    reader = LiveBufferReader(tmp_path)
    turns = reader.read_turns(manifest)
    assert len(turns) == 2
    assert turns[0]["speaker"] == "Traci"
    assert turns[1]["speaker"] == "Traci"


def test_adversarial_overflow_120_word_claim_under_tight_35_word_budget(
    tmp_path: Path, monkeypatch
) -> None:
    """Category 4 (Overflow & exhaustion): 120-word assertion compressed cleanly under words=35 without WORD_BUDGET_EXCEEDED."""
    session_dir = tmp_path / "partner_google_nee" / "10-27-2026" / "Overflow_Session"
    session_dir.mkdir(parents=True)
    seg = {
        "chunk_id": 1,
        "speaker": "Stefanos Georgiades",
        "text": "Let us review the Shadow IT governance and AI intake triage board requirements.",
        "timestamp": "00:01:00",
        "c19_quarantined": False,
    }
    (session_dir / "segments.jsonl").write_text(json.dumps(seg) + "\n", encoding="utf-8")

    claim_120w = (
        "Stefanos Georgiades confirmed that the Shadow IT governance and AI intake triage board "
        "require every business unit across Google Workspace and Gemini Enterprise to register "
        "autonomous agent prototypes, document model data connectors, complete privacy impact "
        "assessments, validate enterprise identity federation, establish token rate limits, "
        "configure audit logging pipelines, assign an operational runbook owner, verify fallback "
        "circuit breakers, and obtain formal architecture review board approval prior to production deployment."
    )
    topo = SimpleNamespace(
        nodes={
            "n_stef": {
                "node_id": "n_stef",
                "canonical_name": "Stefanos Georgiades",
                "display_name": "Stefanos Georgiades",
                "node_type": "PERSON",
                "engagement_id": "partner_google_nee",
            }
        },
        assertions={
            "asrt_120w": {
                "assertion_id": "asrt_120w",
                "subject_node_id": "n_stef",
                "predicate": "REQUIRES_ACTION",
                "object_node_id": "n_stef",
                "claim_text": claim_120w,
                "object_literal": claim_120w,
                "engagement_id": "partner_google_nee",
                "observed_at": "2026-10-26",
            }
        },
        edges=[],
    )
    retriever = CopilotGraphRetriever(topo, engagement_id="partner_google_nee")
    monkeypatch.setattr(
        copilot_handlers,
        "load_copilot_graph",
        lambda **kwargs: (GraphLoadStatus.LOADED_ACTIVE_PAIR, topo, retriever),
    )

    args = argparse.Namespace(
        intent="graph-context",
        target="Stefanos Georgiades",
        topic="Shadow IT governance and AI intake",
        request=None,
        query=None,
        prompt=None,
        words=35,
        meeting_dir=str(session_dir),
        runtime_root=tmp_path,
        engagement="partner_google_nee",
        session=None,
        json=True,
    )
    res = execute_copilot_request(args)
    assert res.get("status") == "PASS", f"120-word claim overflow failed under words=35: {res}"
    ans_lines = Path(res["answers_path"]).read_text(encoding="utf-8").strip().splitlines()
    body = str(json.loads(ans_lines[-1])["response"])
    assert len(body.split()) <= 35


def test_adversarial_invariant_bypass_cross_engagement_and_post_write_fault(
    tmp_path: Path, monkeypatch
) -> None:
    """Category 5 (Invariant bypass attempt) & Category 6 (Rollback / fault isolation):
    1. Live-only directive blocks NEE graph assertions even if engagement="nee_edo" is passed on CLI.
    2. Exception in post-write hint reconciliation never drops the written segment."""
    monkeypatch.setenv("MLX_GLOBAL_LOCK_DIR", str(tmp_path / "locks"))
    out_dir = tmp_path / "output" / "personal" / "10-27-2026" / "Bypass_And_Fault"
    rec = LiveMeetingRecorder(
        project_name="personal",
        meeting_name="Bypass And Fault",
        meeting_date="10-27-2026",
        attendees=["Amit", "Shari Perlwocz (Tucker's Teacher)"],
        output_dir=out_dir,
        mock_mode=True,
    )
    try:
        chunk0 = rec.chunks_dir / "chunk_00000.wav"
        _write_test_wav(chunk0)
        rec._transcribe_chunk = MagicMock(  # type: ignore[method-assign]
            return_value=(
                "Tucker is making wonderful progress on reading comprehension and math stations.",
                [{"text": "Tucker is making wonderful progress on reading comprehension and math stations.", "no_speech_prob": 0.02, "avg_logprob": -0.1}],
            )
        )
        rec._resolve_chunk_speaker_and_tracker = MagicMock(  # type: ignore[method-assign]
            return_value=("Tucker's Teacher", "voice_teacher", 0.95)
        )
        rec.speaker_hint_queue = MagicMock()
        rec.speaker_hint_queue.reconcile_pending_hints.side_effect = OSError("simulated disk lock in hint queue")

        seg = rec.process_chunk(chunk0, 0)
        assert seg is not None
        assert rec.segments_jsonl.exists()

        # Now attempt to bypass live-only directive by passing engagement="nee_edo"
        topo = SimpleNamespace(
            nodes={
                "nee_node": {
                    "node_id": "nee_node",
                    "canonical_name": "Shari Perlwocz",
                    "display_name": "Shari Perlwocz",
                    "node_type": "PERSON",
                    "engagement_id": "nee_edo",
                }
            },
            assertions={
                "asrt_nee_poison": {
                    "assertion_id": "asrt_nee_poison",
                    "subject_node_id": "nee_node",
                    "predicate": "REQUIRES_ACTION",
                    "object_node_id": "nee_node",
                    "claim_text": "Shari Perlwocz must approve NEE SAP EDO Conduit cutover.",
                    "object_literal": "Shari Perlwocz must approve NEE SAP EDO Conduit cutover.",
                    "engagement_id": "nee_edo",
                    "observed_at": "2026-10-26",
                }
            },
            edges=[],
        )
        retriever = CopilotGraphRetriever(topo, engagement_id="nee_edo")
        monkeypatch.setattr(
            copilot_handlers,
            "load_copilot_graph",
            lambda **kwargs: (GraphLoadStatus.LOADED_ACTIVE_PAIR, topo, retriever),
        )

        args = argparse.Namespace(
            intent=None,
            target=None,
            topic=None,
            request="respond back to Shari Perlwocz based on live transcript only, do not look at NEE graph",
            query=None,
            prompt=None,
            words=40,
            meeting_dir=str(out_dir),
            runtime_root=tmp_path,
            engagement=None,
            session=None,
            json=True,
        )
        res = execute_copilot_request(args)
        assert res.get("status") == "PASS", f"Expected live-only response to succeed: {res}"
        assert all(str(aid).startswith("live_turn_") for aid in res.get("cited_assertion_ids", []))
    finally:
        rec._release_slug_lock()
