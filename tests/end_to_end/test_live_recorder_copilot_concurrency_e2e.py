"""End-to-end tests for Wave 5 Live Recorder Queue Safety & Copilot Singleton Isolation (CAT-3)."""
from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from dual_pipeline.cli.copilot_speech import (
    _sanitize_speakable_claim,
    render_speaker_comments,
)
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.copilot_engine import (
    _bootstrap_or_load_graph,
    _find_matching_assertion,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import CopilotGraphRetriever


def test_asr_worker_queue_marks_processed_only_after_chunk_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CAT3-P0-1: Enqueuing a chunk into _chunk_queue does not mark it in processed_chunks until process_chunk completes."""
    rec = object.__new__(LiveMeetingRecorder)
    rec.processed_chunks = set()
    rec.transcribed_chunks = set()
    rec._Queued_chunks = set()
    rec._chunks_lock = threading.Lock()
    rec._chunk_failures = {}
    rec._consecutive_asr_failures = 0
    rec._asr_failed = False
    rec._chunk_queue = queue.Queue(maxsize=8)
    rec._max_backlog = 0
    rec.mock_mode = True
    rec.chunks_dir = tmp_path
    rec.capture_manager = None

    chunk_f = tmp_path / "chunk_0001.wav"
    chunk_f.write_bytes(b"RIFF" + b"\x00" * 100)

    monkeypatch.setattr("dual_pipeline.live_meeting_recorder.ChunkHealth.evaluate", lambda *a, **kw: SimpleNamespace(is_valid=True))
    rec._is_chunk_audible = lambda cf: False  # type: ignore[method-assign]
    rec._maybe_materialize_for_chunk = lambda cf: cf  # type: ignore[method-assign]

    # Simulate active worker thread that hasn't dequeued yet
    rec._asr_worker_thread = SimpleNamespace(is_alive=lambda: True)  # type: ignore[assignment]
    rec._process_chunk_file(chunk_f)

    # While still in queue, chunk is tracked in _Queued_chunks, NOT prematurely in processed_chunks
    assert "chunk_0001.wav" not in rec.processed_chunks
    assert "chunk_0001.wav" in rec._Queued_chunks

    # Now run worker loop with sentinel None
    rec.process_chunk = lambda cf, idx: None  # type: ignore[method-assign]
    rec._chunk_queue.put(None)
    rec._asr_worker_loop()

    assert "chunk_0001.wav" in rec.processed_chunks
    assert "chunk_0001.wav" in rec.transcribed_chunks
    assert "chunk_0001.wav" not in rec._Queued_chunks


def test_copilot_retriever_does_not_mutate_permitted_engagements_singleton_in_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CAT3-P0-2: Concurrent calls to retrieve_structured_assertions do not mutate self.permitted_engagements in-place."""
    topology = SimpleNamespace(
        nodes={"org:partner_advisory": {"canonical_name": "PartnerAdvisory", "node_type": "org"}},
        assertions={
            "a1": {
                "assertion_id": "a1",
                "subject": "org:partner_advisory",
                "predicate": "OWNS",
                "object_literal": "Conduit architecture plan",
                "claim_text": "PartnerAdvisory owns the Conduit architecture plan",
                "engagement_id": "nea",
            }
        },
        edges=[],
        supporting_evidence={},
        evidence_meta={},
    )
    ret = CopilotGraphRetriever(
        topology=topology,
        engagement_id="nea",
        permitted_engagements={"nea"},
    )
    original_set_ref = ret.permitted_engagements
    initial_permitted_copy = set(ret.permitted_engagements)

    observed_during_call: list[set[str]] = []
    orig_collect = ret._collect_multi_query_rankings

    def _spy_collect(*args, **kwargs):
        observed_during_call.append(set(ret.permitted_engagements))
        return orig_collect(*args, **kwargs)

    monkeypatch.setattr(ret, "_collect_multi_query_rankings", _spy_collect)
    monkeypatch.setattr(
        "dual_pipeline.live_meeting_recorder_core.copilot_retriever.resolve_permitted_engagements",
        lambda q, e, t: {"conduit_extra"},
    )

    ret.retrieve_structured_assertions("Conduit architecture plan")
    # Instance attribute must never have been mutated to include conduit_extra
    assert observed_during_call == [initial_permitted_copy]
    assert "conduit_extra" not in ret.permitted_engagements
    assert ret.permitted_engagements == initial_permitted_copy
    assert ret.permitted_engagements is original_set_ref


def test_copilot_engine_bypass_cache_does_not_destroy_shared_neighborhood_store() -> None:
    """CAT3-P0-2: bypass_cache=True returns a shallow copy with neighborhood_store=None without mutating singleton context."""
    from dual_pipeline.live_meeting_recorder_core.copilot_lifecycle import CopilotLifecycleState

    shared_store = object()
    shared_ret = SimpleNamespace(neighborhood_store=shared_store)
    ctx = SimpleNamespace(
        graph_status="LOADED_ACTIVE_PAIR",
        diagnostic=None,
        in_memory_graph={"nodes": {}},
        retriever=shared_ret,
    )
    orch = SimpleNamespace(state=CopilotLifecycleState.READY, _context=ctx)

    _, caller_ret, err, _ = _bootstrap_or_load_graph(
        orch, "nea", [], Path("."), None, bypass_cache=True, sess_id="s1"
    )
    assert err is None
    assert caller_ret.neighborhood_store is None
    assert shared_ret.neighborhood_store is shared_store


def test_copilot_speech_preserves_proper_nouns_and_renders_multiple_gap_items() -> None:
    """CAT3-P1-1 & CAT3-P1-4: Proper nouns at claim start stay capitalized; gaps intent renders up to max_items under max_words."""
    assert _sanitize_speakable_claim("PartnerAdvisory proposed the phase two timeline").startswith("PartnerAdvisory ")
    assert _sanitize_speakable_claim("Maximo integration requires schema validation").startswith("Maximo ")
    assert _sanitize_speakable_claim("The budget was approved").startswith("the ")

    ret = SimpleNamespace(engagement_id="nea", _topology=None)
    assertions = [
        {
            "assertion_id": "a1",
            "subject": "workstream:gis",
            "predicate": "HAS_STATUS",
            "claim_text": "GIS workstream status is green",
            "object_literal": "green",
            "observed_at": "2026-10-01",
        },
        {
            "assertion_id": "a2",
            "subject": "workstream:gis",
            "predicate": "HAS_STATUS",
            "claim_text": "GIS workstream status is delayed",
            "object_literal": "delayed",
            "observed_at": "2026-10-10",
        },
    ]
    res = render_speaker_comments(
        intent="gaps",
        target="Alice",
        assertions=assertions,
        max_words=120,
        retriever=ret,  # type: ignore[arg-type]
        max_items=3,
    )
    assert res.rendered_count >= 2
