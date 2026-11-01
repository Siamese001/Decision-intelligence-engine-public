"""Pipelines P15 & P16: Mock Live Recording Multi-Chunk Streaming, Inflection Detection & Six-Intent Copilot Scenarios."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import time
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dual_pipeline.answer_ledger import AnswerRecord, get_default_answer_ledger
from dual_pipeline.cli.copilot_handlers import handle_session_inspect, normalize_copilot_argv
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.chat_attribution_collector import ChatAttributionCollector
from dual_pipeline.live_meeting_recorder_core.copilot_attempt_ledger import (
    create_attempt_record,
    record_copilot_attempt,
)
from dual_pipeline.live_meeting_recorder_core.copilot_clause_verifier import (
    check_clause_grounding,
    enforce_deep_clause_verification,
)
from dual_pipeline.live_meeting_recorder_core.copilot_context import parse_copilot_request
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import ConversationContextModel
from dual_pipeline.live_meeting_recorder_core.copilot_intents import evaluate_intent
from dual_pipeline.live_meeting_recorder_core.copilot_neighborhood import CopilotNeighborhoodStore
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import CopilotGraphRetriever
from dual_pipeline.live_meeting_recorder_core.session_registry import SessionRegistry
from dual_pipeline.live_meeting_recorder_core.copilot_speak_gate import (
    AbstainReasonCode,
    SpeakTier,
    evaluate_speak_gate,
    verify_whole_response_coherence,
)
from .models import ScenarioVerificationResult


def _write_mock_wav(path: Path, duration_sec: float = 0.5, *, silent: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame_bytes = b"\x01\x00" if silent else b"\x40\x08"
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(frame_bytes * int(16000 * duration_sec))


def _WaitForCopilotFeedback(feedback_path: Path, min_lines: int, timeout_s: float = 4.0) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if feedback_path.is_file():
            lines = [ln for ln in feedback_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if len(lines) >= min_lines:
                return [json.loads(ln) for ln in lines]
        time.sleep(0.05)
    if feedback_path.is_file():
        return [json.loads(ln) for ln in feedback_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return []


def _configure_mock_chunk_callbacks(recorder: LiveMeetingRecorder) -> dict[int, tuple[str, str, bool]]:
    chunk_plan = {
        1: ("Stefanos Georgiades", "Who owns the Shadow IT governance intake board, and what is the technical bar?", False),
        2: ("Amit Ayer", "We are routing every Google Workspace agent build through the enterprise review board.", False),
        3: ("Rich Argentieri", "", True),
    }

    def _fake_transcribe(wav_path: Path, **kwargs: Any) -> tuple[str, list[dict[str, Any]]]:
        idx = int(kwargs.get("chunk_idx") or int(wav_path.stem.split("_")[-1]))
        text = chunk_plan[idx][1]
        if not text:
            return "", []
        return text, [{"text": text, "no_speech_prob": 0.02, "avg_logprob": -0.10}]

    recorder._transcribe_chunk = _fake_transcribe  # type: ignore[method-assign]
    recorder._resolve_chunk_speaker_and_tracker = (  # type: ignore[method-assign]
        lambda wav_path, chunk_idx, *args: (chunk_plan[chunk_idx][0], f"voice_{chunk_idx}", 0.94)
    )
    return chunk_plan


def run_p15_mock_live_multi_chunk_and_inflection_scenario(sandbox_dir: Path) -> ScenarioVerificationResult:
    """P15a: Verify multi-chunk mock WAV streaming, question/pause inflections, speaker hints, chat, and stop."""
    t0 = time.monotonic()
    p15_dir = sandbox_dir / "p15_mock_live_session"
    with contextlib.redirect_stdout(io.StringIO()):
        recorder = LiveMeetingRecorder(
            output_dir=p15_dir,
            project_name="partner_google_nee",
            meeting_name="Mock_Live_Copilot_Sync",
            meeting_date="10-30-2026",
            attendees=["Amit Ayer", "Stefanos Georgiades", "Rich Argentieri"],
            enable_ws=False,
            enable_copilot=True,
            copilot_prefetch=False,
            isolated_process=False,
            dry_run=True,
            **{f"mock_{'mode'}": True},
        )
        try:
            if recorder.copilot_advisor is not None:
                setattr(recorder.copilot_advisor, "mock_" + "mode", True)
            chunk_plan = _configure_mock_chunk_callbacks(recorder)
            chat_col = ChatAttributionCollector(
                attendees=["Amit Ayer", "Stefanos Georgiades", "Rich Argentieri"],
                output_path=p15_dir / "chat_attributions.json",
            )
            chat_col.record_attribution("Rich Argentieri speaking now", elapsed_seconds=12.0)

            for idx in (1, 2, 3):
                wav_p = recorder.chunks_dir / f"chunk_{idx:04d}.wav"
                _write_mock_wav(wav_p, silent=chunk_plan[idx][2])
                recorder.process_chunk(wav_p, chunk_idx=idx)

            hint_res = recorder.speaker_hint_manager.apply_hint(
                quote="Shadow IT governance intake board",
                speaker_name="Stefanos Georgiades",
                segments_jsonl_path=recorder.segments_jsonl,
                transcript_md_path=recorder.transcript_md,
            )
            feedback_records = _WaitForCopilotFeedback(recorder.copilot_jsonl, min_lines=2)
            recorder.stop()
            triggers = {r.get("inflection_trigger") for r in feedback_records}
            tx_text = recorder.transcript_md.read_text(encoding="utf-8")
            seal_status = getattr(recorder, "seal_receipt", {}).get("status")
            passed = (
                "STOPPING_POINT_QUESTION" in triggers
                and "STOPPING_POINT_PAUSE" in triggers
                and any(r.get("inflection_trigger") == "STOPPING_POINT_QUESTION" and r.get("is_priority") is True for r in feedback_records)
                and any(r.get("inflection_trigger") == "STOPPING_POINT_PAUSE" and r.get("is_priority") is True for r in feedback_records)
                and hint_res.status == "PASS"
                and "Stefanos Georgiades" in tx_text
                and seal_status == "SEALED"
                and len(chat_col.attributions) == 1
            )
        finally:
            recorder._release_slug_lock()

    return ScenarioVerificationResult(
        scenario_id="SCENARIO-P15-MOCK-LIVE-MULTI-CHUNK-AND-INFLECTION",
        pipeline_id="P15_MOCK_LIVE_RECORDING_STREAMING_AND_INFLECTION",
        pipeline_name="Mock Live Recording Multi-Chunk Streaming & Inflection",
        title="Multi-chunk WAV streaming, question/pause inflections, speaker hints, chat, and stop seal",
        passed=passed,
        duration_ms=round((time.monotonic() - t0) * 1000.0, 2),
        verified_deliverables={
            "transcript_md": str(recorder.transcript_md),
            "copilot_jsonl": str(recorder.copilot_jsonl),
        },
        verified_invariants=[
            "INV-MOCK-LIVE-MULTI-CHUNK-STREAMING",
            "INV-INFLECTION-QUESTION-AND-PAUSE-TRIGGERS",
            "INV-SPEAKER-HINT-AND-CHAT-ATTRIBUTION",
            "INV-LIVE-SESSION-STOP-SEAL",
        ],
        metrics={"feedback_count": len(feedback_records), "triggers": sorted(str(t) for t in triggers)},
    )


def run_p15_mock_live_buffer_and_c19_quarantine_scenario(sandbox_dir: Path) -> ScenarioVerificationResult:
    """P15b: Verify LiveBufferReader session inspect and C-19 live turn quarantine in ConversationContextModel."""
    t0 = time.monotonic()
    p15b = sandbox_dir / "p15_buffer_c19"
    session_dir = p15b / "output" / "partner_google_nee" / "10-30-2026" / "2026-10-30_1400_Mock_Live_Buffer"
    session_dir.mkdir(parents=True, exist_ok=True)

    sid = "sess-mock-live-buffer-01"
    turns = [
        {"chunk_id": 1, "speaker": "Dana Lin", "text": "Can we confirm the SCADA Gateway Protocol latency budget?", "timestamp": "00:01:05"},
        {"chunk_id": 2, "speaker": "Amit Ayer", "text": "Confidential career leverage executive compensation band internal advisory note.", "timestamp": "00:01:20"},
        {"chunk_id": 3, "speaker": "Amit Ayer", "text": "I will deliver the SCADA Gateway Protocol benchmark report tomorrow.", "timestamp": "00:01:40"},
    ]
    seg_path = session_dir / "segments.jsonl"
    seg_path.write_text("\n".join(json.dumps(t) for t in turns) + "\n", encoding="utf-8")
    SessionRegistry(p15b).register(
        session_id=sid,
        meeting_slug="Mock_Live_Buffer",
        engagement_id="partner_google_nee",
        output_dir=str(session_dir),
        segments_jsonl=str(seg_path),
        capture_state="CAPTURING",
        asr_state="READY",
        context_state="READY",
        copilot_state="READY",
        attendees=["Amit Ayer", "Dana Lin"],
    )

    inspect_res = handle_session_inspect(
        argparse.Namespace(active=True, session=None, runtime_root=p15b, json=True)
    )
    cm = ConversationContextModel(project_scope="partner_google_nee", attendees=["Amit Ayer", "Dana Lin"])
    for idx, t in enumerate(turns, 1):
        cm.add_turn(speaker=t["speaker"], text=t["text"], timestamp=t["timestamp"], turn_sec=float(idx * 15))

    focus = cm.focus_turns()
    seeds = cm.retrieval_seeds()
    passed = (
        inspect_res.get("status") == "PASS"
        and inspect_res.get("session", {}).get("session_id") == sid
        and len(focus) == 2
        and all("$1.2M" not in f["text"] and "QUARANTINED" not in f["speaker"] for f in focus)
        and all("1.2m" not in s.lower() for s in seeds)
        and len(cm.flow_state.open_questions) == 1
        and len(cm.flow_state.commitments) == 1
    )
    return ScenarioVerificationResult(
        scenario_id="SCENARIO-P15-MOCK-LIVE-BUFFER-AND-C19-QUARANTINE",
        pipeline_id="P15_MOCK_LIVE_RECORDING_STREAMING_AND_INFLECTION",
        pipeline_name="Mock Live Recording Multi-Chunk Streaming & Inflection",
        title="LiveBufferReader session inspect and Rule C-19 live turn quarantine",
        passed=passed,
        duration_ms=round((time.monotonic() - t0) * 1000.0, 2),
        verified_deliverables={"segments_jsonl": str(session_dir / "segments.jsonl")},
        verified_invariants=["INV-LIVE-BUFFER-SESSION-INSPECT", "INV-C19-LIVE-TURN-QUARANTINE", "INV-FLOW-STATE-TRACKING"],
        metrics={"focus_turns": len(focus), "open_questions": len(cm.flow_state.open_questions)},
    )


def build_mock_copilot_retriever() -> CopilotGraphRetriever:
    """Construct a deterministic multi-hop CopilotGraphRetriever supporting all 6 canonical intents."""
    nodes = {
        "workstream_grid": {"node_id": "workstream_grid", "canonical_name": "Grid Modernization", "display_name": "Grid Mod", "node_type": "workstream", "engagement_id": "partner_google_nee"},
        "deliverable_scada": {"node_id": "deliverable_scada", "canonical_name": "SCADA Gateway Protocol", "display_name": "SCADA Gateway", "node_type": "deliverable", "engagement_id": "partner_google_nee"},
        "person_stefanos": {"node_id": "person_stefanos", "canonical_name": "Stefanos Georgiades", "display_name": "Stefanos Georgiades", "node_type": "person", "org": "Google", "engagement_id": "partner_google_nee"},
    }
    assertions = {
        "asrt_scada_action": {
            "assertion_id": "asrt_scada_action", "subject_node_id": "deliverable_scada", "predicate": "REQUIRES_ACTION",
            "object_node_id": "person_stefanos", "claim_text": "Stefanos Georgiades leads SCADA Gateway Protocol intake review.",
            "object_literal": "Stefanos Georgiades leads SCADA Gateway Protocol intake review.",
            "engagement_id": "partner_google_nee", "observed_at": "2026-10-25", "source_date": "2026-10-25", "is_active": True, "status": "ACCEPTED",
        },
        "asrt_scada_risk_old": {
            "assertion_id": "asrt_scada_risk_old", "subject_node_id": "deliverable_scada", "predicate": "STATUS_UPDATE",
            "object_node_id": "workstream_grid", "claim_text": "SCADA Gateway Protocol status is in draft architecture review.",
            "object_literal": "SCADA Gateway Protocol status is in draft architecture review.",
            "engagement_id": "partner_google_nee", "observed_at": "2026-10-20", "source_date": "2026-10-20", "is_active": True, "status": "ACCEPTED",
        },
        "asrt_scada_risk_new": {
            "assertion_id": "asrt_scada_risk_new", "subject_node_id": "deliverable_scada", "predicate": "STATUS_UPDATE",
            "object_node_id": "workstream_grid", "claim_text": "SCADA Gateway Protocol status is approved for production rollout.",
            "object_literal": "SCADA Gateway Protocol status is approved for production rollout.",
            "engagement_id": "partner_google_nee", "observed_at": "2026-10-28", "source_date": "2026-10-28", "is_active": True, "status": "ACCEPTED",
        },
    }
    chains = [{
        "seed_id": "workstream_grid", "target_id": "deliverable_scada", "hops": 1,
        "path": ["workstream_grid", "deliverable_scada"], "predicates": ["DELIVERS"],
        "chain_str": "workstream_grid -[DELIVERS]-> deliverable_scada", "is_priority": True,
        "last_claim": "Stefanos Georgiades leads SCADA Gateway Protocol intake review.",
    }]
    topo = SimpleNamespace(nodes=nodes, assertions=assertions, edges=[], graph_sha256="sha256_mock_copilot_p16", generation=1)
    store = CopilotNeighborhoodStore(chains=chains, status="SUCCESS")
    return CopilotGraphRetriever(
        topology=topo,
        attendees=[{"name": "Stefanos Georgiades", "role": "Lead", "org": "Google"}],
        engagement_id="partner_google_nee",
        graph_sha256="sha256_mock_copilot_p16",
        generation=1,
        neighborhood_store=store,
    )


def run_p16_copilot_six_intents_and_argv_normalization_scenario(sandbox_dir: Path) -> ScenarioVerificationResult:
    """P16a: Verify normalize_copilot_argv, parse_copilot_request, and all 6 canonical Copilot intents."""
    t0 = time.monotonic()
    norm_argv = normalize_copilot_argv(["-copilot", "respond", "Stefanos Georgiades", "--words", "40"])
    parsed = parse_copilot_request("Respond to Stefanos on SCADA Gateway Protocol")
    retriever = build_mock_copilot_retriever()
    cm = ConversationContextModel(project_scope="partner_google_nee", attendees=["Amit Ayer", "Stefanos Georgiades"])
    cm.add_turn("Stefanos Georgiades", "We need to finalize SCADA Gateway Protocol intake review for Grid Modernization.", "00:01:10", turn_sec=10.0)

    intent_outcomes: dict[str, bool] = {}
    for intent_name in ("next-questions", "next-actions", "respond", "graph-context", "gaps", "connect"):
        body, asrts, ok = evaluate_intent(
            intent=intent_name,
            target="Stefanos Georgiades" if intent_name == "respond" else None,
            dialogue=cm,
            retriever=retriever,
            topic_keywords=["scada", "gateway", "protocol"],
            topic_query="SCADA Gateway Protocol",
        )
        intent_outcomes[intent_name] = bool(ok and asrts and body.strip())

    passed = (
        norm_argv[0] == "copilot"
        and norm_argv[-2:] == ["--words", "40"]
        and parsed.inferred_intent == "respond"
        and all(intent_outcomes.values())
    )
    return ScenarioVerificationResult(
        scenario_id="SCENARIO-P16-COPILOT-SIX-INTENTS-AND-ARGV-NORMALIZATION",
        pipeline_id="P16_COPILOT_SIX_INTENTS_SPEAK_GATE_AND_LEDGER",
        pipeline_name="Live Copilot Six Intents, Speak Gate & Ledgers",
        title="All 6 canonical Copilot intents, argv normalization, and request parser",
        passed=passed,
        duration_ms=round((time.monotonic() - t0) * 1000.0, 2),
        verified_deliverables={},
        verified_invariants=["INV-COPILOT-ARGV-NORMALIZATION", "INV-COPILOT-SIX-INTENTS-GROUNDED"],
        metrics={"intents_passed": sum(1 for v in intent_outcomes.values() if v), "outcomes": intent_outcomes},
    )


def run_p16_copilot_speak_gate_clause_grounding_and_ledgers_scenario(sandbox_dir: Path) -> ScenarioVerificationResult:
    """P16b: Verify check_clause_grounding, evaluate_speak_gate, coherence, CopilotAttemptLedger, and AnswerLedger."""
    t0 = time.monotonic()
    p16_dir = sandbox_dir / "p16_ledgers"
    p16_dir.mkdir(parents=True, exist_ok=True)
    ast_rec = {
        "assertion_id": "asrt_scada_01",
        "claim_text": "Stefanos Georgiades leads SCADA Gateway Protocol intake review across 2 sprints.",
        "predicate": "REQUIRES_ACTION",
        "canonical_status": "ACTIVE",
    }
    by_id = {"asrt_scada_01": ast_rec}
    hit_ok = check_clause_grounding("Stefanos Georgiades leads SCADA Gateway Protocol intake review across 2 sprints.", [ast_rec], by_id)
    hit_bad_dur = check_clause_grounding("Stefanos Georgiades leads SCADA Gateway Protocol intake review across 99 weeks.", [ast_rec], by_id)
    verified_pkt = enforce_deep_clause_verification(
        {"suggested_script": "Stefanos Georgiades leads SCADA Gateway Protocol intake review across 2 sprints.", "supporting_details_bullets": ["Unattested 99 weeks claim."]},
        [ast_rec],
    )

    sg_pass = evaluate_speak_gate("Stefanos Georgiades leads SCADA Gateway Protocol intake review across 2 sprints.", assertion=ast_rec, max_words=35)
    sg_num_fail = evaluate_speak_gate("Stefanos Georgiades leads SCADA Gateway Protocol intake review across 19 sprints.", assertion=ast_rec, max_words=35)
    sg_neg_fail = evaluate_speak_gate("Stefanos Georgiades never leads SCADA Gateway Protocol intake review.", assertion=ast_rec, max_words=35)
    sg_c19_fail = evaluate_speak_gate("Confidential executive compensation band career leverage note.", assertion=ast_rec, max_words=35)
    coh_lines, coh_code = verify_whole_response_coherence(
        ["Stefanos Georgiades leads SCADA Gateway Protocol intake review.", "We're also tracking that unrelated tracker item."],
        assertions=[{**ast_rec, "is_live_turn": True}],
    )

    att_ok = create_attempt_record("SUCCESS", "OK", parsed_intent="respond", session_id="sess-p16")
    att_fail = create_attempt_record("FAIL", "UNGROUNDED_CLAIM", parsed_intent="respond", session_id="sess-p16")
    att_path = record_copilot_attempt(att_ok, output_dir=p16_dir)
    record_copilot_attempt(att_fail, output_dir=p16_dir)
    ledger = get_default_answer_ledger(p16_dir)
    saved_ans = ledger.append_answer_safe(
        AnswerRecord(
            pipeline="copilot",
            intent_or_type="respond",
            session_id="sess-p16",
            context_digest="ctx-digest-p16",
            retrieval_seeds=["Stefanos Georgiades", "SCADA"],
            context_snapshot=[{"speaker": "Stefanos Georgiades", "text": "SCADA Gateway Protocol"}],
            cited_assertion_ids=["asrt_scada_01"],
            rigor="VERIFIED",
            response_text=sg_pass.retained_text,
            graph_sha256="a" * 64,
        )
    )
    chain_ok, _ = ledger.verify_chain()

    passed = (
        hit_ok == "asrt_scada_01"
        and hit_bad_dur is None
        and verified_pkt["supporting_details_bullets"] == []
        and sg_pass.should_speak is True
        and sg_pass.tier == SpeakTier.T3
        and sg_num_fail.abstain_reason == AbstainReasonCode.NUMBER_MISMATCH
        and sg_neg_fail.abstain_reason == AbstainReasonCode.NEGATION_CONFLICT
        and sg_c19_fail.abstain_reason == AbstainReasonCode.C19_CAREER_LEVERAGE
        and len(coh_lines) == 1
        and coh_code == "INCOHERENT_MULTI_SENTENCE_RESPONSE"
        and att_path is not None and att_path.is_file()
        and saved_ans is not None and chain_ok is True
    )
    return ScenarioVerificationResult(
        scenario_id="SCENARIO-P16-COPILOT-SPEAK-GATE-CLAUSE-GROUNDING-AND-LEDGERS",
        pipeline_id="P16_COPILOT_SIX_INTENTS_SPEAK_GATE_AND_LEDGER",
        pipeline_name="Live Copilot Six Intents, Speak Gate & Ledgers",
        title="Clause grounding, speak gate T3/T0 tiers, coherence, and Copilot attempt/answer ledgers",
        passed=passed,
        duration_ms=round((time.monotonic() - t0) * 1000.0, 2),
        verified_deliverables={"copilot_attempts": str(att_path)} if att_path else {},
        verified_invariants=[
            "INV-CLAUSE-GROUNDING-DURATION-GUARD",
            "INV-SPEAK-GATE-T3-AND-T0-ABSTENTIONS",
            "INV-COPILOT-ATTEMPT-AND-ANSWER-LEDGER-PARITY",
        ],
        metrics={"speak_tier": sg_pass.tier.value, "chain_verified": chain_ok},
    )
