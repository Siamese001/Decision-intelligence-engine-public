"""
tests/end_to_end/test_live_copilot_asr_failure_and_speak_gate_e2e.py

End-to-end verification of Wave 2 Live Copilot readiness gates,
segment discovery in output/transcript_cache/, and speak-gate integrity.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import pytest

from dual_pipeline.live_meeting_recorder_core.session_registry import (
    SessionManifest, SessionRegistry,
)
from dual_pipeline.live_meeting_recorder_core.live_buffer_reader import LiveBufferReader
from dual_pipeline.live_meeting_recorder_core.copilot_engine import (
    _resolve_copilot_session, _check_focus_turns,
)
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import ConversationContextModel
from dual_pipeline.live_meeting_recorder_core.copilot_speak_gate import (
    evaluate_speak_gate, AbstainReasonCode,
)
from dual_pipeline.cli.copilot_speech import _distill_claim_text, _sanitize_speakable_claim, _frame_inquiry


def test_active_session_asr_failed_blocks_silent_graph_fallback(tmp_path: Path):
    reg = SessionRegistry(tmp_path)
    manifest = reg.register(
        meeting_slug="Debrief_Skyler_PartnerAdvisory",
        engagement_name="project_lens",
        output_dir=str(tmp_path / "output/project_lens/10-21-2026/Debrief_Skyler_PartnerAdvisory"),
        capture_state="CAPTURING",
        asr_state="FAILED",
        copilot_state="NOT_READY",
    )
    args = argparse.Namespace(
        runtime_root=str(tmp_path),
        active=True,
        offline=False,
        graph_only=False,
        intent="respond",
        target="Miles Erickson",
        request="respond to Miles about PGD being engaged with rules",
    )

    resolved, err = _resolve_copilot_session(args, machine=True)
    assert resolved is None
    assert err is not None
    assert err["error"] == "ASR_FAILED"
    assert "Live ASR has failed" in err["details"]


def test_active_session_asr_warming_blocks_silent_graph_fallback(tmp_path: Path):
    reg = SessionRegistry(tmp_path)
    manifest = reg.register(
        meeting_slug="Debrief_Skyler_PartnerAdvisory",
        engagement_name="project_lens",
        output_dir=str(tmp_path / "output/project_lens/10-21-2026/Debrief_Skyler_PartnerAdvisory"),
        capture_state="CAPTURING",
        asr_state="WARMING",
        copilot_state="NOT_READY",
    )
    args = argparse.Namespace(
        runtime_root=str(tmp_path),
        active=True,
        offline=False,
        graph_only=False,
        intent="respond",
        target="Miles Erickson",
        request="respond to Miles about PGD being engaged with rules",
    )

    resolved, err = _resolve_copilot_session(args, machine=True)
    assert resolved is None
    assert err is not None
    assert err["error"] in ("ASR_NOT_READY", "SESSION_INITIALIZING")


def test_active_session_with_explicit_offline_flag_permits_graph_lookup(tmp_path: Path):
    reg = SessionRegistry(tmp_path)
    out_dir = tmp_path / "output/project_lens/10-21-2026/Debrief_Skyler_PartnerAdvisory"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = reg.register(
        meeting_slug="Debrief_Skyler_PartnerAdvisory",
        engagement_name="project_lens",
        output_dir=str(out_dir),
        capture_state="CAPTURING",
        asr_state="FAILED",
        copilot_state="NOT_READY",
    )
    args = argparse.Namespace(
        runtime_root=str(tmp_path),
        active=True,
        offline=True,
        graph_only=False,
        intent="respond",
        target="Miles Erickson",
        request="what was previously agreed",
    )

    resolved, err = _resolve_copilot_session(args, machine=True)
    assert err is None
    assert resolved is not None
    res_dir, ctx_model, res_manifest, slug = resolved
    assert res_manifest.session_id == manifest.session_id


def test_check_focus_turns_active_session_zero_turns_fails_closed(tmp_path: Path):
    cm = ConversationContextModel()
    manifest = SessionManifest(
        session_id="test_sess_01",
        engine_signature="test",
        parent_pid=1234,
        parent_start_time=0.0,
        repo_root=str(tmp_path),
        capture_state="CAPTURING",
        asr_state="FAILED",
    )
    args = argparse.Namespace(request="answer Miles about rules", offline=False, graph_only=False)
    res, err = _check_focus_turns(cm, args, manifest, True, tmp_path, tmp_path, "test_sess_01", None, None)
    assert res is None
    assert err is not None
    assert err["error"] == "ASR_FAILED"


def test_live_buffer_reader_resolves_segments_from_transcript_cache(tmp_path: Path):
    project = "project_lens"
    date_str = "10-21-2026"
    slug = "Debrief_Skyler_PartnerAdvisory"
    out_dir = tmp_path / "output" / project / date_str / slug
    cache_dir = tmp_path / "output" / "transcript_cache" / project / date_str
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    seg_file = cache_dir / f"01_{date_str}_{slug}_segments.jsonl"
    turn1 = {"speaker": "Miles Erickson", "text": "PGD is actively engaged with rules.", "timestamp": "00:01:15", "start_sec": 75.0}
    turn2 = {"speaker": "Skyler Carlson", "text": "Agreed, we verified the pipeline yesterday.", "timestamp": "00:01:25", "start_sec": 85.0}
    seg_file.write_text(json.dumps(turn1) + "\n" + json.dumps(turn2) + "\n", encoding="utf-8")

    manifest = SessionManifest(
        session_id="sess_cache_test",
        engine_signature="test",
        parent_pid=1234,
        parent_start_time=0.0,
        repo_root=str(tmp_path),
        output_dir=str(out_dir),
        cache_dir=str(cache_dir),
        segments_jsonl=str(seg_file),
        meeting_slug=slug,
    )

    reader = LiveBufferReader(tmp_path)
    turns = reader.read_turns(manifest)
    assert len(turns) == 2
    assert turns[0]["speaker"] == "Miles Erickson"
    assert turns[0]["text"] == "PGD is actively engaged with rules."
    assert turns[1]["speaker"] == "Skyler Carlson"


def test_truncated_assertion_sanitization_and_speak_gate_rejection():
    # 1. Trailing conjunction in raw text is cleaned by _distill_claim_text
    raw_ast = "the customer was trying to build some rules, and they would build it, and it, like, wouldn't accept, and..."
    cleaned = _distill_claim_text(raw_ast)
    assert not cleaned.endswith("and")
    assert not cleaned.endswith(",")
    assert not cleaned.endswith("...")
    assert "wouldn't accept" in cleaned

    # 2. Inquiry framing never ends with trailing question mark after conjunction
    framed = _frame_inquiry("could we clarify", "Miles Erickson", "", cleaned)
    assert not framed.endswith("and?")
    assert not framed.endswith("or?")

    # 3. Direct dangling conjunction is rejected by copilot speak gate even with max_words set
    dangling_text = "Miles Erickson, could we clarify that the rule engine wouldn't accept, and?"
    ast_fixture = {"assertion_id": "ast_dangling_01", "claim_text": dangling_text, "canonical_status": "ACTIVE"}
    decision = evaluate_speak_gate(dangling_text, assertion=ast_fixture, max_words=35)
    assert not decision.should_speak
    assert decision.diagnostics.get("reason") in ("DANGLING_CONJUNCTION_REJECTED", "WRITTEN_ESSAY_FORMAT_REJECTED")

    # 4. Long run-on sentence exceeding _MAX_SENTENCE_WORDS is rejected even with max_words=35
    runon_sentence = (
        "Miles, we know that the initial implementation encountered continuous latency spikes across every single "
        "worker node during the heavy load tests because the background garbage collector was never tuned appropriately."
    )
    ast_runon = {"assertion_id": "ast_runon_01", "claim_text": runon_sentence, "canonical_status": "ACTIVE"}
    decision_runon = evaluate_speak_gate(runon_sentence, assertion=ast_runon, max_words=35)
    assert not decision_runon.should_speak
    assert decision_runon.abstain_reason == AbstainReasonCode.WORD_BUDGET_EXCEEDED
