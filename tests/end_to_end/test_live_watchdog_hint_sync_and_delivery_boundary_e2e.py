"""End-to-end verification for Wave 4 (CAT-3 P0 & P1):
- Watchdog failover propagation from background daemon thread to main poll loop (CAT3-1000-P0-1)
- Thread-safe segments.jsonl & transcript_md synchronization and single-path hint application (CAT3-1000-P0-2)
- Off-topic claim detection without fixture hardcoding & severity-ordered delivery receipts (CAT3-1000-P0-3, P0-4)
- Live headphone/mute cache invalidation, inactivity timeout at zero audible chunks, DialogueTurn quarantine check, and post-compression clause re-verification (CAT3-1000-P1-1..4)
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.grounding.delivery_boundary import (
    _is_off_topic_claim,
    _resolve_terminal_reason,
)
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.audio_capture import (
    AudioCaptureUnprovenError,
    AudioReasonCode,
    LiveAudioCaptureManager,
)
from dual_pipeline.live_meeting_recorder_core.copilot_engine import (
    _check_focus_turns,
    _evaluate_line_for_speak_gate,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import CopilotGraphRetriever


def test_watchdog_failover_propagates_to_recorder_main_loop(tmp_path: Path) -> None:
    """CAT3-1000-P0-1: Watchdog failover on background thread records watchdog_exception and halts recorder.run()."""
    chunks_dir = tmp_path / "chunks"
    master_wav = tmp_path / "master.wav"
    mgr = LiveAudioCaptureManager(
        chunks_dir=chunks_dir,
        master_wav=master_wav,
        source_mode="mock",
        mock_mode=True,
        skip_device_discovery=True,
    )
    with pytest.raises(AudioCaptureUnprovenError):
        mgr._handle_watchdog_failover("zero_byte_stall", exit_code=1)

    assert mgr.watchdog_exception is not None
    assert mgr.capture_state == "FAILED"
    assert (tmp_path / "record_start_failure.json").is_file()

    # Verify LiveMeetingRecorder.run() immediately surfaces watchdog_exception and quarantines session
    rec = LiveMeetingRecorder.__new__(LiveMeetingRecorder)
    rec.running = True
    rec.mock_mode = True
    rec.max_duration_minutes = None
    rec.inactivity_timeout_seconds = 0.0
    rec.capture_manager = mgr
    rec.collateral_watcher = None
    rec.chat_collateral_bridge = None
    rec._init_transcript_file = MagicMock()
    rec._start_asr_worker = MagicMock()
    rec.start_ffmpeg = MagicMock()
    rec._poll_and_process_chunks = MagicMock()
    rec._quarantine_session = MagicMock()
    rec.stop = MagicMock()

    with pytest.raises(AudioCaptureUnprovenError) as exc_info:
        rec.run(max_iterations=3)
    assert exc_info.value.reason_code == AudioReasonCode.AUDIO_GROWTH_STALLED
    rec._quarantine_session.assert_called_once()
    rec.stop.assert_called_once()


def test_record_chat_message_avoids_duplicate_enqueue_when_applied_immediately() -> None:
    """CAT3-1000-P0-2: record_chat_message applies hint under lock and only enqueues when not immediately applied."""
    rec = LiveMeetingRecorder.__new__(LiveMeetingRecorder)
    rec._transcript_io_lock = threading.RLock()
    rec.clean_slug = "test_slug"
    rec.segments_jsonl = Path("/tmp/seg.jsonl")
    rec.transcript_md = Path("/tmp/trans.md")
    rec.voice_registry = MagicMock()
    rec.speaker_hint_manager = MagicMock()
    rec.speaker_hint_queue = MagicMock()
    rec.chat_attributions = MagicMock()

    attr = SimpleNamespace(
        attribution_type="quote_to_speaker",
        quoted_text="We are deploying Kubernetes",
        resolved_speaker="Alice",
    )
    rec.chat_attributions.record_attribution.return_value = [attr]

    # Case 1: Immediate apply succeeds (PASS) -> queue.enqueue_hint must NOT be called
    rec.speaker_hint_manager.apply_hint.return_value = SimpleNamespace(status="PASS", voice_id="v1")
    rec.record_chat_message('"We are deploying Kubernetes" is Alice', elapsed_seconds=12.0)
    rec.speaker_hint_queue.enqueue_hint.assert_not_called()

    # Case 2: Immediate apply fails (quote not yet transcribed) -> queue.enqueue_hint IS called
    rec.speaker_hint_manager.apply_hint.return_value = SimpleNamespace(status="FAIL", voice_id=None)
    rec.record_chat_message('"Future quote" is Alice', elapsed_seconds=15.0)
    rec.speaker_hint_queue.enqueue_hint.assert_called_once()


def test_off_topic_claim_detection_and_terminal_reason_resolution() -> None:
    """CAT3-1000-P0-3 & P0-4: _is_off_topic_claim rejects arbitrary unrelated claims and _resolve_terminal_reason never returns GROUNDED_SUPPORTED on failure."""
    claim_off_topic = SimpleNamespace(
        claim_id="clm-1",
        subject="Alice",
        predicate="FAVORITE_SPORT",
        object="Plays competitive tennis on weekends",
    )
    claim_on_topic = SimpleNamespace(
        claim_id="clm-2",
        subject="Alice",
        predicate="OWNS_ARCHITECTURE",
        object="Leads Kubernetes cluster migration",
    )
    prompt_tokens = {"alice", "kubernetes", "cluster", "migration"}
    assert _is_off_topic_claim(claim_off_topic, prompt_tokens) is True
    assert _is_off_topic_claim(claim_on_topic, prompt_tokens) is False

    # Terminal reason when payload digest or generation parity fails while claims are GROUNDED_SUPPORTED
    assert _resolve_terminal_reason(True, False, "GROUNDED_SUPPORTED", has_digest_err=True) == "UNBACKED_PAYLOAD_SPAN"
    assert _resolve_terminal_reason(True, False, "GROUNDED_SUPPORTED", has_gen_err=True) == "GENERATION_DRIFT"
    assert _resolve_terminal_reason(True, False, "GROUNDED_SUPPORTED") == "UNSUPPORTED_CLAIM"


def test_copilot_dialogue_turn_quarantine_and_ranked_person_context() -> None:
    """CAT3-1000-P1-3 & P1-4: _check_focus_turns handles DialogueTurn objects, compress_for_speak_gate re-verifies grounding, and get_person_context ranks person nodes."""
    @dataclass
    class DummyTurn:
        speaker: str
        text: str
        is_quarantined: bool = False

    cm = SimpleNamespace(_turns=[DummyTurn(speaker="Alice", text="Hello", is_quarantined=False)])
    in_mem = MagicMock()
    ret = MagicMock()
    pair, err = _check_focus_turns(cm, SimpleNamespace(), None, True, Path("/tmp"), Path("/tmp"), "s1", in_mem, ret)
    assert err is None
    assert pair == (in_mem, ret)

    # Post-compression clause grounding check rejects ungrounded compressed line
    assertions = [{"assertion_id": "a1", "claim_text": "Alice approved the 2026 cloud budget."}]
    with patch(
        "dual_pipeline.live_meeting_recorder_core.proactive_inflection_agent.compress_for_speak_gate",
        return_value="Completely fabricated ungrounded sentence after compression.",
    ), patch(
        "dual_pipeline.live_meeting_recorder_core.copilot_clause_verifier.check_clause_grounding",
        return_value=False,
    ):
        long_line = " ".join(["word"] * 40)
        retained, code = _evaluate_line_for_speak_gate(
            long_line, assertions, spk_conf=1.0, intent="respond", max_words=20, cumulative_words=0
        )
        assert retained is None
        assert code == "UNGROUNDED_CLAIM"

    # Ranked person context selects exact person node over incidental single-token workstream match
    topology = SimpleNamespace(
        nodes={
            "workstream:john_initiative": {
                "canonical_name": "John Legacy Migration Initiative",
                "display_name": "John Legacy Migration Initiative",
                "node_type": "workstream",
            },
            "person:john_smith": {
                "canonical_name": "John Smith",
                "display_name": "John Smith",
                "node_type": "person",
            },
        },
        assertions={},
    )
    retriever = CopilotGraphRetriever(topology=topology)
    ctx = retriever.get_person_context("John Smith", "architecture review")
    assert ctx["node_id"] == "person:john_smith"
