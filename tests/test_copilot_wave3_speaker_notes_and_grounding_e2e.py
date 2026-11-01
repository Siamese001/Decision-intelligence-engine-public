"""
tests/test_copilot_wave3_speaker_notes_and_grounding_e2e.py

End-to-End verification for Wave 3:
- Rich per-speaker notes (SpeakerNoteProfile) tracking recent commitments, risks, stances, and questions.
- Multi-sentence live turn claim distillation (up to 30 words / 2 sentences) with conversational act predicate mapping.
- Expanded speakable sentence and word budgets (130w summary, 220w supporting details) with sentence-boundary truncation.
- Two-assertion pairwise union grounding in copilot_clause_verifier without sentence-initial capitalization false rejections.
- O(N) bucketed conflict detection and non-polluted speculative encoder fallback in copilot_grounding_verifier.
"""
from __future__ import annotations

import time
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import ConversationContextModel
from dual_pipeline.live_meeting_recorder_core.copilot_utterance_resolver import (
    _resolve_speaker_and_utterance,
    synthesize_live_transcript_assertions,
)
from dual_pipeline.live_meeting_recorder_core.copilot_clause_verifier import (
    check_clause_grounding,
    enforce_deep_clause_verification,
)
from dual_pipeline.live_meeting_recorder_core.copilot_validator import (
    MAX_SPEAKER_SUMMARY_WORDS,
    MAX_SUPPORTING_DETAILS_WORDS,
    _truncate_at_sentence_boundary,
)
from dual_pipeline.live_meeting_recorder_core.copilot_grounding_verifier import (
    AsyncSpeculativeEncoder,
    detect_assertion_conflicts,
)
from dual_pipeline.cli.copilot_speech import _distill_claim_text, _extract_topic_anchor


def test_speaker_note_profile_accumulates_stances_commitments_and_risks() -> None:
    now = time.time()
    ctx = ConversationContextModel(focus_seconds=300.0)
    ctx.add_turn(
        speaker="Speaker_Alpha",
        text="I will deliver the BigQuery migration architecture blueprint by Friday afternoon.",
        timestamp="14:01:00",
        turn_sec=now - 120.0,
    )
    ctx.add_turn(
        speaker="Speaker_Alpha",
        text="The legacy egress firewall is the blocker right now for our Phase 2 cutover.",
        timestamp="14:02:00",
        turn_sec=now - 60.0,
    )
    ctx.add_turn(
        speaker="Speaker_Alpha",
        text="How should we sequence the Vertex AI quota expansion across regions?",
        timestamp="14:03:00",
        turn_sec=now - 20.0,
    )
    notes = ctx.get_speaker_notes("Speaker_Alpha")

    assert notes is not None
    assert notes.speaker == "Speaker_Alpha"
    assert notes.turn_count == 3
    assert len(notes.commitments) >= 1
    assert "BigQuery migration" in notes.commitments[0]
    assert len(notes.objections_and_risks) >= 1
    assert "blocker" in notes.objections_and_risks[0].lower()
    assert len(notes.open_asks) >= 1
    # Verify cached entities on turn dicts are reused cleanly
    assert isinstance(ctx.focus_turns()[0].get("entities"), list)


def test_utterance_resolver_preserves_30_word_claims_and_semantic_predicates() -> None:
    ctx = ConversationContextModel(focus_seconds=300.0)
    ctx.add_turn(
        speaker="Chad",
        text=(
            "We face a severe risk from a latency spike in the AlloyDB read replica during peak load. "
            "This bottleneck threatens our Friday release SLA unless we provision additional compute nodes."
        ),
        timestamp="14:05:00",
        turn_sec=100.0,
    )
    ctx.add_turn(
        speaker="Speaker_Beta",
        text="I will deliver the regional vCPU quota expansion request to the Google Cloud support team today.",
        timestamp="14:06:00",
        turn_sec=110.0,
    )
    _, selected, _, err = _resolve_speaker_and_utterance("Chad", ["latency", "AlloyDB"], ctx, None)
    assert err is None
    synth = synthesize_live_transcript_assertions(ctx, "Chad", selected, topic_keywords=["latency"], max_items=3)

    by_id = {a["assertion_id"]: a for a in synth}
    assert by_id["live_turn_1"]["predicate"] == "RAISES_RISK"
    # Up to 30 words preserved across two sentences
    risk_words = by_id["live_turn_1"]["claim_text"].split()
    assert 15 <= len(risk_words) <= 30
    assert len(by_id["live_turn_1"]["verbatim_span"]) > 40
    assert by_id["live_turn_2"]["predicate"] == "COMMITS_TO"


def test_pairwise_union_clause_grounding_and_sentence_initial_capitalization() -> None:
    assertions = [
        {
            "assertion_id": "asrt_1",
            "subject_node_id": "person:chad",
            "predicate": "OWNS_OR_LEADS",
            "object_node_id": "workstream:mobilization",
            "claim_text": "Chad leads the mobilization engineering workstream for the Google Cloud transition.",
        },
        {
            "assertion_id": "asrt_2",
            "subject_node_id": "workstream:mobilization",
            "predicate": "TARGETS_DATE",
            "object_literal": "November 15",
            "claim_text": "The mobilization engineering workstream targets a November 15 readiness milestone.",
        },
    ]
    by_id = {a["assertion_id"]: a for a in assertions}
    # Sentence starts with capitalized common word "Currently" and synthesizes facts across asrt_1 + asrt_2 without explicit tags
    sentence = "Currently Chad leads the mobilization engineering workstream targeting the November 15 readiness milestone."
    hit = check_clause_grounding(sentence, assertions, by_id)
    assert hit in ("asrt_1", "asrt_2"), f"Expected pairwise union grounding match, got: {hit}"

    payload = {
        "speaker_summary": sentence,
        "supporting_details_bullets": [
            "Specifically Chad leads the mobilization engineering workstream for the Google Cloud transition."
        ],
    }
    filtered = enforce_deep_clause_verification(payload, assertions)
    assert "November 15" in filtered["speaker_summary"]
    assert len(filtered["supporting_details_bullets"]) == 1


def test_expanded_word_budgets_and_speech_distillation() -> None:
    assert MAX_SPEAKER_SUMMARY_WORDS >= 130
    assert MAX_SUPPORTING_DETAILS_WORDS >= 220

    long_claim = (
        "PartnerAdvisory and Google Cloud agreed to prioritize the Phase 2 data warehouse migration across three business units, "
        "ensuring that security governance controls and VPC service perimeters are validated prior to production cutover."
    )
    distilled = _distill_claim_text(long_claim)
    assert len(distilled.split()) >= 28
    assert "production cutover" in distilled

    anchor = _extract_topic_anchor({"matched_keywords": ["perimeter", "latency"]})
    assert "perimeter" in anchor.lower()

    text = "First complete sentence stays intact. Second complete sentence also stays intact. Third sentence overflows the limit."
    truncated = _truncate_at_sentence_boundary(text, 12)
    assert truncated.endswith(".")
    assert "Third" not in truncated


def test_bucketed_conflict_detection_and_speculative_encoder_no_pseudo_vector() -> None:
    assertions = [
        {
            "assertion_id": "a1",
            "subject_node_id": "project:alpha",
            "predicate": "HAS_STATUS",
            "object_literal": "approved",
            "claim_text": "Project Alpha is approved for Q4 launch.",
        },
        {
            "assertion_id": "a2",
            "subject_node_id": "project:alpha",
            "predicate": "HAS_STATUS",
            "object_literal": "rejected",
            "claim_text": "Project Alpha is rejected due to budget constraints.",
        },
        {
            "assertion_id": "a3",
            "subject_node_id": "project:beta",
            "predicate": "HAS_STATUS",
            "object_literal": "approved",
            "claim_text": "Project Beta is approved.",
        },
    ]
    conflicts = detect_assertion_conflicts(assertions)
    assert len(conflicts) == 1
    assert {conflicts[0]["assertion_a"], conflicts[0]["assertion_b"]} == {"a1", "a2"}

    enc = AsyncSpeculativeEncoder()
    triggered = enc.trigger_speculative_encode("This is a sample live dialogue turn without backend", backend=None)
    assert triggered is False
    assert enc.get_cached_vector() is None
