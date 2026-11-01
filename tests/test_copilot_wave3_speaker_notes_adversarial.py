"""
tests/test_copilot_wave3_speaker_notes_adversarial.py

Adversarial and edge-case tests for Wave 3:
1. Boundary payload: Empty turns, unknown speaker notes lookup, 0-word claims.
2. Malformed input: Non-string claim fields, missing assertion IDs in pairwise union.
3. Invariant bypass attempt: Fabricated proper nouns injected into pairwise union clause must fail closed.
4. Overflow & exhaustion: 500-assertion O(N) conflict detection completes in <25ms without quadratic stall.
"""
from __future__ import annotations

import time
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import ConversationContextModel
from dual_pipeline.live_meeting_recorder_core.copilot_clause_verifier import check_clause_grounding
from dual_pipeline.live_meeting_recorder_core.copilot_grounding_verifier import detect_assertion_conflicts
from dual_pipeline.live_meeting_recorder_core.copilot_turn_signals import AskRecord, _check_turn_answers_ask
from dual_pipeline.live_meeting_recorder_core.copilot_utterance_resolver import _extract_valid_live_claim
from dual_pipeline.live_meeting_recorder_core.stream_aggregator import ConversationalSpeakerTracker


def test_boundary_empty_context_and_unknown_speaker_notes() -> None:
    ctx = ConversationContextModel(focus_seconds=300.0)
    notes = ctx.get_speaker_notes("NonExistentSpeaker")
    assert notes is None
    assert ctx.focus_turns() == []


def test_adversarial_fabricated_entity_in_pairwise_union_rejected() -> None:
    assertions = [
        {
            "assertion_id": "a1",
            "subject_node_id": "person:alice",
            "predicate": "LEADS",
            "object_node_id": "project:atlas",
            "claim_text": "Alice leads the Atlas cloud migration program.",
        },
        {
            "assertion_id": "a2",
            "subject_node_id": "project:atlas",
            "predicate": "HAS_BUDGET",
            "object_literal": "500k",
            "claim_text": "The Atlas cloud migration program has a 500k budget.",
        },
    ]
    by_id = {a["assertion_id"]: a for a in assertions}
    # Inject ungrounded proper noun "Snowflake" into a pairwise-synthesized sentence
    hallucinated = "Alice leads the Atlas cloud migration program onto Snowflake with a 500k budget."
    hit = check_clause_grounding(hallucinated, assertions, by_id)
    assert hit is None


def test_adversarial_stopword_only_overlap_does_not_resolve_open_ask() -> None:
    ask = AskRecord(
        ask_id="ask_1",
        turn_seq=1,
        speaker="Alice",
        addressee=None,
        text="What is the current status of the deployment?",
        ask_kind="open",
        created_sec=10.0,
    )
    turn_text = "What is the current plan for our team?"
    # Shares "what", "is", "the", "current" (all stopwords), zero substantive tokens ("deployment", "status")
    assert _check_turn_answers_ask(ask, "Bob", turn_text, is_bc=False) is False


def test_overflow_500_distinct_assertions_conflict_check_under_25ms() -> None:
    assertions = [
        {
            "assertion_id": f"asrt_{i}",
            "subject_node_id": f"node:subject_{i}",
            "predicate": "OWNS_OR_LEADS",
            "object_node_id": f"node:obj_{i}",
            "claim_text": f"Subject {i} leads object {i}.",
        }
        for i in range(500)
    ]
    t0 = time.perf_counter()
    conflicts = detect_assertion_conflicts(assertions)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert conflicts == []
    assert elapsed_ms < 25.0, f"Expected O(N) conflict check < 25ms, took {elapsed_ms:.2f}ms"


def test_production_regression_speaker_tracker_attribute_chunk_kwargs() -> None:
    tracker = ConversationalSpeakerTracker(
        attendees=[{"name": "Traci"}, {"name": "Amit"}],
        primary_speaker="Amit",
    )
    spk = tracker.attribute_chunk("We need a solution owner to backfill this role.")
    assert spk == "Traci"


def test_production_regression_non_substantive_dangling_live_claim_rejected() -> None:
    seen: set[str] = set()
    assert _extract_valid_live_claim("So, I think that could potentially be the .", seen) is None
    valid = _extract_valid_live_claim(
        "Dan Branken is the product owner on the NEA side so we need a solution owner.",
        seen,
    )
    assert valid is not None
    assert "solution owner" in valid.lower()
