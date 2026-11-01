"""
tests/test_copilot_live_transcript_and_sibling_continuity.py

Wave 2 unit and integration tests for:
1. .cache/*segments.jsonl discovery and same-day cross-worktree sibling session continuity
   (Prd_Eduardo_PartnerAdvisory -> Debrief_Rob_PartnerAdvisory) with >= 25 focus turns.
2. Unmatched speaker hint timestamp preservation (never resetting last_turn_timestamp to 00:00:00).
3. Whisper initial_prompt echo and no_speech_prob >= 0.60 quarantine before ConversationContextModel.
4. Gate 3.14 and reconciliation_coverage_healer alignment on 1-turn substantive transcripts.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from dual_pipeline.asr_decode_config import DEFAULT_ASR_DECODE_CONFIG
from dual_pipeline.audio_postprocess import (
    filter_segments_by_telemetry,
    is_hallucinated_or_prompt_echo_turn,
    sanitize_transcript_text,
)
from dual_pipeline.cli.copilot_session import _resolve_session_and_context_model
from dual_pipeline.live_meeting_recorder_core.chunk_transcriber import extract_chunk_telemetry
from dual_pipeline.live_meeting_recorder_core.copilot_attempt_ledger import extract_context_freshness
from dual_pipeline.live_meeting_recorder_core.live_voice_registry import LiveVoiceRegistry
from dual_pipeline.transcript_qa_core.gate3_evidence import check_substantive_transcript_evidence
from dual_pipeline.transcript_qa_core.reconciliation_coverage_healer import heal_substantive_transcript_evidence


def test_dot_cache_discovery_sibling_worktree_continuity_and_hint_preservation(tmp_path: Path) -> None:
    """Verify .cache segment loading, sibling audio worktree continuity, >=25 focus turns, and non-zero timestamp."""
    main_root = tmp_path / "repo"
    wt_prd = main_root / ".worktrees" / "audio_project_lens_10_27_2026_prd_eduardo_partner_advisory"
    wt_debrief = main_root / ".worktrees" / "audio_project_lens_10_27_2026_debrief_rob_partner_advisory"

    prd_cache = wt_prd / "output" / "project_lens" / "10-27-2026" / "Prd_Eduardo_PartnerAdvisory" / ".cache"
    prd_cache.mkdir(parents=True, exist_ok=True)
    prd_seg_file = prd_cache / "01_10-27-2026_Prd_Eduardo_PartnerAdvisory_segments.jsonl"
    prd_seg_file.write_text(
        json.dumps({
            "chunk_idx": 0,
            "speaker": "Eduardo More",
            "text": "Mandy and I walked through the PRD and golden dataset alignment before the sprint review.",
            "timestamp": "00:01:15",
            "start_sec": 75.0,
            "asr_conf": 0.94,
            "no_speech_prob": 0.03,
        }) + "\n",
        encoding="utf-8",
    )

    debrief_dir = wt_debrief / "output" / "project_lens" / "10-27-2026" / "Debrief_Rob_PartnerAdvisory"
    debrief_cache = debrief_dir / ".cache"
    debrief_cache.mkdir(parents=True, exist_ok=True)
    debrief_seg_file = debrief_cache / "01_10-27-2026_Debrief_Rob_PartnerAdvisory_segments.jsonl"

    lines = []
    # Include 28 real turns across 420 seconds + 2 hallucinated/echo turns that must be quarantined
    for i in range(28):
        sec = float((i + 1) * 15)
        mm = int(sec) // 60
        ss = int(sec) % 60
        ts = f"00:{mm:02d}:{ss:02d}"
        spk = "Rob" if i % 2 == 0 else "Amit Ayer"
        lines.append(json.dumps({
            "chunk_idx": i,
            "speaker": spk,
            "text": f"Turn {i + 1}: Mandy and Eduardo noted the 200-card sprint board and serial number linkage gap.",
            "timestamp": ts,
            "start_sec": sec,
            "asr_conf": 0.92,
            "no_speech_prob": 0.04,
        }))
    # Add a Whisper initial_prompt echo and a high no_speech_prob segment
    lines.append(json.dumps({
        "chunk_idx": 28,
        "speaker": "Amit Ayer",
        "text": "Domain terms, LENS, Single Turbine, Turbine Fleet, Mandy, Jen, Amit, PartnerAdvisory.",
        "timestamp": "00:07:10",
        "start_sec": 430.0,
        "no_speech_prob": 0.15,
    }))
    lines.append(json.dumps({
        "chunk_idx": 29,
        "speaker": "Amit Ayer",
        "text": "Leave a comment below and glad to have you on the show.",
        "timestamp": "00:07:12",
        "start_sec": 432.0,
        "no_speech_prob": 0.88,
    }))
    debrief_seg_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Add an unmatched speaker hint with late wall-clock session_elapsed_seconds (1979.2s)
    (debrief_dir / "speaker_hints_queue.json").write_text(
        json.dumps([
            {
                "hint_id": "unmatched-late-hint",
                "speaker_name": "harper Hoffman",
                "quote": "completely unrelated quote not in transcript",
                "status": "QUEUED",
                "session_elapsed_seconds": 1979.2,
            }
        ]),
        encoding="utf-8",
    )

    args = argparse.Namespace(
        meeting_dir=str(debrief_dir),
        runtime_root=str(wt_debrief),
        _parsed_window_seconds=None,
    )
    out_dir, model, _, _ = _resolve_session_and_context_model(args)
    assert out_dir == debrief_dir.resolve()

    focus = model.focus_turns()
    assert len(focus) >= 25
    all_texts = " ".join(t["text"] for t in focus)
    assert "Domain terms, LENS" not in all_texts
    assert "Leave a comment below" not in all_texts
    # Sibling session turn from Prd_Eduardo_PartnerAdvisory is bridged into focus turns
    assert any("PRD and golden dataset alignment" in t["text"] for t in focus)

    freshness = extract_context_freshness(model)
    assert freshness["turn_count"] >= 25
    assert freshness["last_turn_timestamp"] == "00:07:00"
    assert freshness["last_turn_timestamp"] != "00:00:00"


def test_whisper_prompt_echo_and_high_no_speech_quarantined() -> None:
    """Verify prompt echoes, podcast sign-offs, and high no_speech_prob segments are dropped."""
    assert is_hallucinated_or_prompt_echo_turn(
        "Domain terms, LENS, Single Turbine, Turbine Fleet, Mandy, Jen."
    ) is True
    assert is_hallucinated_or_prompt_echo_turn(
        "Leave a comment below if you enjoyed the episode."
    ) is True
    assert is_hallucinated_or_prompt_echo_turn(
        "Valid discussion of LENS serial number validation.", no_speech_prob=0.72
    ) is True
    assert is_hallucinated_or_prompt_echo_turn(
        "Valid discussion of LENS serial number validation.", no_speech_prob=0.12
    ) is False

    assert sanitize_transcript_text("Domain terms, LENS, Single Turbine, Turbine Fleet.") == ""
    assert sanitize_transcript_text("This is a hero of my life.") == ""

    segs = [
        {"text": "Real LENS discussion with Mandy.", "no_speech_prob": 0.08, "avg_logprob": -0.2, "compression_ratio": 1.1},
        {"text": "Phantom speech during silence.", "no_speech_prob": 0.78, "avg_logprob": -0.3, "compression_ratio": 1.1},
    ]
    filtered = filter_segments_by_telemetry(segs, decode_config=DEFAULT_ASR_DECODE_CONFIG)
    assert filtered == "Real LENS discussion with Mandy."


def test_gate3_healer_aligns_single_turn_substantive_transcript() -> None:
    """Verify 1-turn >500-char transcript (Prd_Eduardo_PartnerAdvisory pattern) is healed by heal_substantive_transcript_evidence."""
    long_turn = (
        "[00:01:15] Eduardo More: "
        + "We reviewed the LENS product requirements document with harper Hoffman and confirmed that "
        * 7
    )
    assert len(long_turn) > 500
    proj = {
        "meeting_id": "Prd_Eduardo_PartnerAdvisory",
        "evidence_index": {
            "ev-att-01": {
                "statement_id": "ev-att-01",
                "attribution_mode": "COLLATERAL_REVIEWED",
                "text": "Visual collateral artifact from slide deck.",
                "locator": {"kind": "attachment", "value": "attachment:deck.pdf"},
            }
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "person:eduardo-more", "type": "person", "name": "Eduardo More", "mutation": "confirm"}
            ],
            "relationship_signals": [],
        },
    }
    heal_substantive_transcript_evidence(proj, f1_text=long_turn)
    ok, failures = check_substantive_transcript_evidence(proj, f1_text=long_turn)
    assert ok is True, f"Expected Gate 3.14 pass after healing, got: {failures}"


def test_voice_registry_host_resolution_and_chunk_telemetry() -> None:
    """Verify LiveVoiceRegistry resolves host attendee name and extract_chunk_telemetry extracts conf/no_speech."""
    reg = LiveVoiceRegistry(attendees=["Eduardo More", "Amit Ayer"])
    assert reg.resolve_host_attendee_name() == "Amit Ayer"

    asr_conf, no_speech = extract_chunk_telemetry((
        "Sample text",
        [{"text": "Sample text", "avg_logprob": -0.15, "no_speech_prob": 0.04}],
    ))
    assert 0.80 <= asr_conf <= 1.0
    assert no_speech == 0.04
