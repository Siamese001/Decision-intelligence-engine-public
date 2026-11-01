"""
tests/test_copilot_lens_debrief_adversarial.py

Wave 5 Adversarial & Edge-Case Suite covering:
1. Boundary payloads (0-turn cold start, 441-chunk long session windowing).
2. Malformed inputs (unmatched speaker hint queues, markdown-heavy roster claims, Contract No. abbreviations).
3. Invariant bypass attempts (C-19 career leverage quarantine and prompt-echo poisoning).
"""
from __future__ import annotations

import json
from pathlib import Path

from dual_pipeline.audio_postprocess import is_hallucinated_or_prompt_echo_turn
from dual_pipeline.cli.copilot_speech import _distill_claim_text, _sanitize_speakable_claim
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import (
    ConversationContextModel,
)
from dual_pipeline.live_meeting_recorder_core.copilot_speak_gate import (
    AbstainReasonCode,
    evaluate_speak_gate,
)
from dual_pipeline.live_meeting_recorder_core.speaker_hint_matcher import (
    reconcile_turns_with_hint_queue,
)


def test_boundary_0_turn_cold_start_and_441_chunk_session() -> None:
    """Verify 0-turn cold start returns empty focus_turns cleanly and 441-chunk session retains >=25 turns."""
    empty_model = ConversationContextModel(project_scope="project_lens", attendees=["Rob"])
    assert empty_model.focus_turns() == []

    long_model = ConversationContextModel(project_scope="project_lens", attendees=["Rob", "Amit Ayer"])
    for idx in range(1, 442):
        long_model.add_turn(
            speaker="Rob" if idx % 2 else "Amit Ayer",
            text=f"Substantive LENS pole inspection discussion point number {idx} for SOW 1.",
            timestamp=f"00:{idx // 60:02d}:{idx % 60:02d}",
            turn_sec=float(idx * 5),
        )
    focused = long_model.focus_turns()
    assert len(focused) >= 25
    assert focused[-1]["seq"] == 441


def test_malformed_unmatched_hints_markdown_headers_and_contract_no(tmp_path: Path) -> None:
    """Verify unmatched speaker hints do not corrupt timestamps, markdown headers are stripped, and No. passes."""
    turns = [
        {
            "seq": i,
            "speaker": "Amit Ayer",
            "text": f"Turn {i} discussing LENS golden dataset validation.",
            "timestamp": f"00:0{i}:00",
            "turn_sec": float(i * 60),
        }
        for i in range(1, 12)
    ]
    hints_file = tmp_path / "speaker_hints_queue.json"
    hints_file.write_text(
        json.dumps(
            [
                {
                    "speaker_name": "Unmatched Guest",
                    "quote": "Completely unrelated quote not in transcript",
                    "session_elapsed_seconds": 99999.0,
                }
            ]
        ),
        encoding="utf-8",
    )
    reconciled = reconcile_turns_with_hint_queue(turns, [tmp_path])
    assert len(reconciled) == 11
    assert all(t["timestamp"] != "00:00:00" for t in reconciled)

    # Markdown roster header rejected by _distill_claim_text
    assert _distill_claim_text("- **Apex Global Enterprises (NEA):** harper Hoffman, Eduardo More") == ""
    # Bold markdown stripped cleanly on valid claims
    assert "**" not in _sanitize_speakable_claim("**harper Hoffman** confirmed the **Golden Dataset** scope.")

    # Contract No. abbreviation passes speak gate without NEGATION_CONFLICT
    ast = {
        "assertion_id": "asrt_contract_no",
        "claim_text": "Under Contract No. 4600027256, PartnerAdvisory delivers SOW 1.",
        "object_literal": "Under Contract No. 4600027256, PartnerAdvisory delivers SOW 1.",
        "canonical_status": "ACTIVE",
    }
    dec = evaluate_speak_gate("Under Contract No. 4600027256, PartnerAdvisory delivers SOW 1.", assertion=ast)
    assert dec.should_speak is True


def test_invariant_bypass_c19_and_prompt_echo_quarantine() -> None:
    """Verify C-19 career leverage assertions fail closed and prompt echoes are quarantined."""
    c19_ast = {
        "assertion_id": "asrt_c19_leak",
        "claim_text": "Internal career leverage strategy for executive compensation band.",
        "namespace": "INTERNAL_ADVISORY",
        "is_quarantined": 1,
    }
    dec = evaluate_speak_gate("We are tracking the executive compensation band.", assertion=c19_ast)
    assert dec.should_speak is False
    assert dec.abstain_reason == AbstainReasonCode.C19_CAREER_LEVERAGE

    echo_text = "Meeting participants: Rob, Amit Ayer. Domain terms, LENS, Apex Global Enterprises, PartnerAdvisory."
    assert is_hallucinated_or_prompt_echo_turn(echo_text, no_speech_prob=0.1) is True

    model = ConversationContextModel(project_scope="project_lens", attendees=["Rob"])
    model.add_turn(speaker="Rob", text=echo_text, timestamp="00:01:00", turn_sec=60.0)
    assert model.focus_turns() == []
