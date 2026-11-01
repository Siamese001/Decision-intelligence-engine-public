"""Wave 2 tests: Contiguous ASR Turn Coalescing & Substantive Domain Utterance Ranking."""
from __future__ import annotations

from dual_pipeline.live_meeting_recorder_core.copilot_context import UtteranceSelector
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import ConversationContextModel
from dual_pipeline.live_meeting_recorder_core.copilot_utterance_resolver import (
    score_utterance_domain_substance,
    synthesize_live_transcript_assertions,
)
from dual_pipeline.live_meeting_recorder_core.live_buffer_reader import coalesce_contiguous_turns


def test_coalesce_contiguous_5s_turns_preserves_timestamps() -> None:
    """Contiguous 5-second turns by 'Eduardo Juan More' at [03:55]–[04:30] coalesce into a single coherent passage."""
    raw_chunks = [
        {"seq": 40, "speaker": "Eduardo Juan More", "text": "In the old project rule we had GE...", "timestamp": "00:03:55", "turn_sec": 235.0, "end_sec": 240.0},
        {"seq": 41, "speaker": "Eduardo Juan More", "text": "hardcoded at 100% accuracy in the extraction metric.", "timestamp": "00:04:05", "turn_sec": 245.0, "end_sec": 250.0},
        {"seq": 42, "speaker": "Eduardo Juan More", "text": "If you do not put a field rule with Daniel Schaap, the number is misleading.", "timestamp": "00:04:30", "turn_sec": 255.0, "end_sec": 270.0},
        {"seq": 43, "speaker": "Skyler", "text": "Yeah, we should fix that rule.", "timestamp": "00:04:35", "turn_sec": 275.0, "end_sec": 280.0},
    ]
    coalesced = coalesce_contiguous_turns(raw_chunks)
    assert len(coalesced) == 2
    assert coalesced[0]["speaker"] == "Eduardo Juan More"
    assert "GE" in coalesced[0]["text"]
    assert "100% accuracy" in coalesced[0]["text"]
    assert "Daniel Schaap" in coalesced[0]["text"]
    assert coalesced[0]["timestamp"] == "00:03:55–00:04:30"


def test_substantive_domain_turns_outrank_signoffs_and_fillers() -> None:
    """Sign-off/filler turns ('I do have to run', 'To make it right. Yeah, for sure.') are outranked by substantive turns."""
    ge_score = score_utterance_domain_substance(
        "In the old project rule we had GE hardcoded at 100% and a single nameplate has five different numbers for IGBT modules."
    )
    notif_score = score_utterance_domain_substance(
        "Notifications are flooding the wrong engineers; we need notification routing separated by wind vs solar and GE turbines vs transformers."
    )
    signoff_1 = score_utterance_domain_substance("I do have to run, I have another meeting in a minute.")
    signoff_2 = score_utterance_domain_substance("To make it right. Yeah, for sure.")
    signoff_3 = score_utterance_domain_substance("cool. Yeah, I mean, that seems pretty straightforward.")

    assert ge_score > 10.0
    assert notif_score > 10.0
    assert signoff_1 < 0.0
    assert signoff_2 < 0.0
    assert signoff_3 < 0.0

    cm = ConversationContextModel(project_scope="project:lens", focus_seconds=86400.0)
    raw_turns = [
        {"seq": 40, "speaker": "Eduardo Juan More", "text": "The old project rule hardcoded GE at 100%...", "timestamp": "00:03:55", "turn_sec": 235.0},
        {"seq": 41, "speaker": "Eduardo Juan More", "text": "and a single nameplate has five different numbers for IGBT extraction with Daniel Schaap.", "timestamp": "00:04:00", "turn_sec": 240.0},
        {"seq": 85, "speaker": "Eduardo Juan More", "text": "Notifications are flooding engineers across wind and solar.", "timestamp": "00:06:20", "turn_sec": 380.0},
        {"seq": 240, "speaker": "Eduardo Juan More", "text": "To make it right. Yeah, for sure.", "timestamp": "00:29:40", "turn_sec": 1780.0},
        {"seq": 242, "speaker": "Eduardo Juan More", "text": "cool. Yeah, I mean, that seems pretty straightforward.", "timestamp": "00:29:50", "turn_sec": 1790.0},
        {"seq": 244, "speaker": "Eduardo Juan More", "text": "I do have to run, I have another meeting in a minute.", "timestamp": "00:29:58", "turn_sec": 1798.0},
    ]
    for rt in raw_turns:
        cm.add_turn(speaker=rt["speaker"], text=rt["text"], timestamp=rt["timestamp"], turn_sec=rt["turn_sec"])

    coalesced_turns = coalesce_contiguous_turns(raw_turns)
    sel_res = UtteranceSelector.select(coalesced_turns, target_speaker="Ed More", topic_keywords=[])
    assert sel_res.status == "SELECTED"
    assert sel_res.selected_utterance is not None
    assert "GE" in sel_res.selected_utterance.text or "Notifications" in sel_res.selected_utterance.text
    assert "have to run" not in sel_res.selected_utterance.text

    live_asrts = synthesize_live_transcript_assertions(cm, target="Ed More", selected_utterance=sel_res.selected_utterance, topic_keywords=[], max_items=3)
    assert len(live_asrts) >= 2
    top_claims = " ".join(a["claim_text"] for a in live_asrts)
    assert "GE" in top_claims or "nameplate" in top_claims
    assert "have to run" not in top_claims
    assert "To make it right" not in top_claims


def test_jeffrey_chyan_q4_kickoff_smalltalk_rejected_for_brett_manning_and_maxcore() -> None:
    """Exact production regression: early Q4 kickoff small-talk and over-merged tail fragments are outranked by recent stakeholder/project turns."""
    from dual_pipeline.live_meeting_recorder_core.copilot_utterance_resolver import _clean_live_turn_claim

    # 1. Early Q4 kickoff small-talk scores negative and cleans to empty
    broken_turn = "working on stuff here in the I'm excited to get Q4 kick off. Which was nice."
    assert score_utterance_domain_substance(broken_turn) < 0.0
    assert _clean_live_turn_claim(broken_turn) == ""

    # 2. If a coalesced block starts with small-talk and ends with substantive stakeholder discussion, tail sentence is extracted
    mixed_block = (
        "working on stuff here in the I'm excited to get Q4 kick off. Which was nice. "
        "We need to align with Brett Manning on Tech Lead expectations and Mandy ownership boundaries."
    )
    cleaned_mixed = _clean_live_turn_claim(mixed_block)
    assert "working on stuff here" not in cleaned_mixed
    assert "Which was nice" not in cleaned_mixed
    assert "Brett Manning" in cleaned_mixed
    assert "Tech Lead expectations" in cleaned_mixed

    # 3. Full session context model prioritizes recent substantive turns at 10:45 over early 01:55 turns
    cm = ConversationContextModel(project_scope="project:nea", focus_seconds=86400.0)
    cm.add_turn(" Avery Chen", broken_turn, "00:01:55", turn_sec=115.0)
    cm.add_turn(
        " Avery Chen",
        "We need to align with Brett Manning on Tech Lead expectations and Mandy ownership boundaries.",
        "00:10:30",
        turn_sec=630.0,
    )
    cm.add_turn(
        " Avery Chen",
        "We are seeing MaxCore integration churn and need Conduit alignment with John Creech plus Tyler McCord NEA onboarding.",
        "00:10:45",
        turn_sec=645.0,
    )
    live_asrts = synthesize_live_transcript_assertions(cm, target=" Avery Chen", selected_utterance=None, topic_keywords=[], max_items=2)
    assert len(live_asrts) == 2
    claims_joined = " ".join(a["claim_text"] for a in live_asrts)
    assert "Q4 kick off" not in claims_joined
    assert "Which was nice" not in claims_joined
    assert "MaxCore integration churn" in claims_joined
    assert "Brett Manning" in claims_joined


def test_single_channel_turn_handoff_and_vocative_speaker_attribution() -> None:
    """Single-channel back-to-back turns split on question-answer handoffs and attribute vocative turns across two attendees."""
    from dual_pipeline.live_meeting_recorder_core.speaker_hint_matcher import reconcile_turns_with_hint_queue

    raw_chunks = [
        {"seq": 1, "speaker": " Avery Chen", "text": "Jeff, how are we handling the MaxCore integration churn?", "timestamp": "00:05:00", "turn_sec": 300.0, "end_sec": 305.0},
        {"seq": 2, "speaker": " Avery Chen", "text": "Yeah, we are locking in the Conduit guardrails with Brett Manning today.", "timestamp": "00:05:06", "turn_sec": 306.0, "end_sec": 311.0},
    ]
    reconciled = reconcile_turns_with_hint_queue(
        raw_chunks,
        [],
        attendees=["Amit Ayer", " Avery Chen"],
    )
    assert reconciled[0]["speaker"] == "Amit Ayer"
    assert reconciled[1]["speaker"] == " Avery Chen"

    # Even if two chunks have the same speaker label, a question followed by a handoff response does not coalesce
    same_spk_chunks = [
        {"seq": 10, "speaker": " Avery Chen", "text": "Are we aligned on the Conduit architecture?", "timestamp": "00:06:00", "turn_sec": 360.0, "end_sec": 364.0},
        {"seq": 11, "speaker": " Avery Chen", "text": "Yeah, John Creech confirmed the API contract.", "timestamp": "00:06:05", "turn_sec": 365.0, "end_sec": 369.0},
    ]
    coalesced = coalesce_contiguous_turns(same_spk_chunks)
    assert len(coalesced) == 2


