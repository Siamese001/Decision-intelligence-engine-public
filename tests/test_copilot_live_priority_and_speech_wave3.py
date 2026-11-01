"""Wave 3 tests: Live-Transcript Priority Lock & Speakable Executive Response Synthesis."""
from __future__ import annotations

from dual_pipeline.cli.copilot_speech import render_speaker_comments, spoken_claim
from dual_pipeline.live_meeting_recorder_core.copilot_clause_verifier import check_clause_grounding
from dual_pipeline.live_meeting_recorder_core.copilot_context import SelectedUtterance
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import ConversationContextModel
from dual_pipeline.live_meeting_recorder_core.copilot_grounding_verifier import rerank_assertions_vectorized
from dual_pipeline.live_meeting_recorder_core.copilot_intents import _maybe_augment_live_assertions


class _DummyRetriever:
    engagement_id = "project:lens"
    _topology = None


def test_live_directive_lock_outranks_historical_priority_milestone() -> None:
    """When has_live_transcript_directive(prompt) is True and req_cnt=1, live_turn_* stays ranked #1."""
    cm = ConversationContextModel(project_scope="project:lens", focus_seconds=86400.0)
    cm._raw_request = "live transcript next best point to Ed to respond"
    cm.add_turn(
        speaker="Eduardo Juan More",
        text="In the old project rule we had GE hardcoded at 100% and a single nameplate has five different numbers for IGBT extraction.",
        timestamp="00:03:55",
        turn_sec=235.0,
    )

    historical_milestone = {
        "assertion_id": "assertion-f8bc3becf4bee5f4b369281afec5",
        "claim_text": "Client Production Milestone: Ed More oversees LENS production readiness and GE fleet integration.",
        "object_literal": "Client Production Milestone: Ed More oversees LENS production readiness and GE fleet integration.",
        "predicate": "DECIDED_TO",
        "subject_node_id": "person:ed_more",
        "is_priority": True,
        "is_primary": True,
        "provenance_scope": "PRIMARY",
        "confidence_score": 0.95,
    }

    sel_utt = SelectedUtterance(
        seq=1,
        speaker="Eduardo Juan More",
        text="In the old project rule we had GE hardcoded at 100% and a single nameplate has five different numbers for IGBT extraction.",
        timestamp="00:03:55",
        turn_sec=235.0,
        relevance_score=1.0,
        matched_keywords=[],
    )

    body, merged, ok = _maybe_augment_live_assertions(
        intent="respond",
        target="Ed More",
        dialogue=cm,
        selected_utterance=sel_utt,
        topic_keywords=[],
        body="- Client Production Milestone: Ed More oversees LENS production readiness.",
        assertions=[historical_milestone],
        ok=True,
        retriever=_DummyRetriever(),
    )
    assert ok is True
    assert merged[0]["assertion_id"].startswith("live_turn_")
    assert merged[0].get("live_directive_lock") is True

    reranked, _ = rerank_assertions_vectorized(merged, query_tokens={"ed", "more", "lens", "production", "ge"})
    assert reranked[0]["assertion_id"].startswith("live_turn_")


def test_speakable_executive_synthesis_and_clause_gate_blocks_defects() -> None:
    """Output bullets use natural phrasing and fail closed on split-identity vocatives or raw milestone headers."""
    retriever = _DummyRetriever()
    live_asrt = {
        "assertion_id": "live_turn_40",
        "claim_text": "In the old project rule we had GE hardcoded at 100% and a single nameplate has five different numbers",
        "object_literal": "In the old project rule we had GE hardcoded at 100% and a single nameplate has five different numbers",
        "predicate": "DISCUSSES",
        "subject_node_id": "attendee:eduardo_juan_more",
        "is_live_turn": True,
        "is_primary": True,
        "is_priority": True,
        "live_directive_lock": True,
        "confidence_score": 0.92,
    }
    sel_utt = SelectedUtterance(
        seq=40,
        speaker="Eduardo Juan More",
        text=live_asrt["claim_text"],
        timestamp="00:03:55",
        turn_sec=235.0,
        relevance_score=1.0,
        matched_keywords=["how"],
    )

    res = render_speaker_comments(
        intent="respond",
        target="Ed More",
        assertions=[live_asrt],
        max_words=60,
        retriever=retriever,
        selected_utterance=sel_utt,
        max_items=1,
    )
    assert res.rendered_count == 1
    assert "Ed More, regarding Eduardo Juan More's point" not in res.body
    assert "on your how point" not in res.body
    assert "Client Production Milestone:" not in res.body
    assert "could we clarify" not in res.body.lower()
    assert "let's align on" in res.body.lower()

    by_id = {live_asrt["assertion_id"]: live_asrt}
    assert check_clause_grounding(res.body, [live_asrt], by_id, target="Ed More", selected_utterance=sel_utt) == "live_turn_40"

    # Defect 1: Split-identity self-referential vocative must fail closed
    bad_split = "Ed More, regarding Eduardo Juan More's point, could we clarify GE hardcoded at 100%?"
    assert check_clause_grounding(bad_split, [live_asrt], by_id, target="Ed More", selected_utterance=sel_utt) is None

    # Defect 2: Raw markdown header label must fail closed in clause verifier and be stripped in spoken_claim
    bad_header = "Ed More, Client Production Milestone: GE hardcoded at 100%."
    assert check_clause_grounding(bad_header, [live_asrt], by_id, target="Ed More", selected_utterance=sel_utt) is None
    assert "Client Production Milestone:" not in spoken_claim(
        {"claim_text": "Client Production Milestone: GE turbine rule update"}, retriever
    )

    # Defect 3: Interrogative topic anchor must fail closed
    bad_anchor = "Ed, on your how point, let's align on GE hardcoded at 100%."
    assert check_clause_grounding(bad_anchor, [live_asrt], by_id, target="Ed", selected_utterance=sel_utt) is None

    # Defect 4: Live-turn next-questions uses context-aware executive question framing instead of mechanical 'could we clarify'
    jeff_asrt = {
        "assertion_id": "live_turn_99",
        "claim_text": "We need to align with Brett Manning on Tech Lead expectations and Mandy ownership boundaries",
        "object_literal": "We need to align with Brett Manning on Tech Lead expectations and Mandy ownership boundaries",
        "predicate": "DISCUSSES",
        "subject_node_id": "attendee:jeffrey_chyan",
        "is_live_turn": True,
        "is_primary": True,
        "is_priority": True,
        "live_directive_lock": True,
        "confidence_score": 0.92,
    }
    res_q = render_speaker_comments(
        intent="next-questions",
        target=" Avery Chen",
        assertions=[jeff_asrt],
        max_words=60,
        retriever=retriever,
        selected_utterance=None,
        max_items=1,
    )
    assert res_q.rendered_count == 1
    assert "could we clarify" not in res_q.body.lower()
    assert "concrete ownership boundaries and deliverables should we lock in regarding" in res_q.body.lower()
    assert "Brett Manning" in res_q.body
    assert check_clause_grounding(res_q.body, [jeff_asrt], {"live_turn_99": jeff_asrt}, target=" Avery Chen") == "live_turn_99"


def test_active_session_isolation_and_nea_graph_corroboration() -> None:
    """Active session turns must outrank older sibling session turns, filter charter/resource stubs, and bridge NEA to Apex Global agentic claims."""
    from dual_pipeline.live_meeting_recorder_core.copilot_context import filter_assertions_for_utterance_topic, parse_copilot_request
    from dual_pipeline.live_meeting_recorder_core.copilot_utterance_resolver import merge_live_and_graph_assertions, synthesize_live_transcript_assertions

    parsed = parse_copilot_request("explain how NEA applies this")
    assert parsed.topic_keywords == ["nea"]

    cm = ConversationContextModel(project_scope="project:project_nexus", focus_seconds=86400.0)
    cm._raw_request = "explain how NEA applies this"
    cm.add_turn(
        speaker="Speaker",
        text="Memory systems are becoming more persistent, and multimodal models are learning to interpret the physical, moving from AI that generates information to AI that performs workflows.",
        timestamp="00:01:00",
        turn_sec=60.0,
        is_sibling_session=True,
    )
    cm.add_turn(
        speaker="Speaker",
        text="The new way people use AI is through agents, where your agent has tools and has integrations, and it's going to loop through your process.",
        timestamp="00:02:00",
        turn_sec=120.0,
        is_sibling_session=False,
    )

    live_asrts = synthesize_live_transcript_assertions(cm, None, None, ["nea"])
    assert len(live_asrts) == 1
    assert "agents" in live_asrts[0]["claim_text"].lower()
    assert "memory systems" not in live_asrts[0]["claim_text"].lower()

    graph_candidates = [
        {
            "assertion_id": "asrt_charter_stub",
            "claim_text": "Workstream GRID_OS has Q4 2026 Charter Mission 'NEA GRID_OS', PM harper Hoffman: Resource Note: I think….",
        },
        {
            "assertion_id": "asrt_role_bio_dup",
            "claim_text": "Eduardo (Ed) Juan More: Role: Atlas Lead / Rule Specialist. Organization: Apex Global Enterprises (NEA / PGD).",
        },
        {
            "assertion_id": "asrt_claim_claim_v27_gemini_nea_ge301_track",
            "claim_text": "Track 3 Apex Global Analytics / NEA is proposed as GE 301 orchestration with Agent Designer, Google Cloud agentic development and consumption through Gemini Enterprise, governance of GE/connectors/agents, reusable patterns, and best practices.",
        },
        {
            "assertion_id": "asrt_claim_claim_v40_gemini_no_code_first",
            "subject_node_id": "workstream_nee_ai_to_gemini_transition",
            "claim_text": "The current delivery sequence prioritizes connectors and no-code agents, with full-code agent integrations retained as a later or exception path, as no-code lowers maintenance and operating burden.",
        },
    ]
    filtered = filter_assertions_for_utterance_topic(
        graph_candidates, ["nea", "workstream_nee_ai_to_gemini_transition"], utterance_text=live_asrts[0]["claim_text"]
    )
    assert [a["assertion_id"] for a in filtered] == [
        "asrt_claim_claim_v27_gemini_nea_ge301_track",
        "asrt_claim_claim_v40_gemini_no_code_first",
    ]
    merged = merge_live_and_graph_assertions(live_asrts, filtered, "explain how NEA applies this", False)
    assert [a["assertion_id"] for a in merged[:3]] == [
        live_asrts[0]["assertion_id"],
        "asrt_claim_claim_v27_gemini_nea_ge301_track",
        "asrt_claim_claim_v40_gemini_no_code_first",
    ]


