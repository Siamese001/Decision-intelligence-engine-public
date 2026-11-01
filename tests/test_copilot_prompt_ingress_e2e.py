"""E2E lifecycle tests for copilot prompt ingress, natural language parsing, and topic keyword extraction."""
from __future__ import annotations

import pytest

from dual_pipeline.live_meeting_recorder_core.copilot_context import (
    filter_assertions_for_utterance_topic,
    parse_copilot_request,
)


def test_copilot_prompt_ingress_e2e_sap_tech_owner_jason():
    """Verifies complete E2E ingress extraction for the incident prompt."""
    raw_prompt = "-copilot SAP tech owner jason question"
    parsed = parse_copilot_request(raw_request=raw_prompt)

    assert parsed.target_speaker == "Jason"
    assert parsed.topic_query is not None
    assert "SAP" in parsed.topic_query or "sap" in parsed.topic_query.lower()
    assert "sap" in parsed.topic_keywords
    assert "tech" in parsed.topic_keywords
    assert "owner" in parsed.topic_keywords


def test_copilot_prompt_ingress_e2e_various_freeform_queries():
    """Verifies freeform queries without prepositions extract topic query and keywords."""
    p1 = parse_copilot_request(raw_request="SCADA latency thresholds")
    assert p1.target_speaker is None
    assert p1.topic_query == "SCADA latency thresholds"
    assert "scada" in p1.topic_keywords
    assert "latency" in p1.topic_keywords
    assert "thresholds" in p1.topic_keywords

    p2 = parse_copilot_request(raw_request="question for Jason regarding SAP architecture")
    assert p2.target_speaker == "Jason"
    assert p2.topic_query is not None
    assert "sap" in p2.topic_keywords
    assert "architecture" in p2.topic_keywords

    p3 = parse_copilot_request(raw_request="copilot what is the data product roadmap")
    assert p3.topic_query is not None
    assert "data" in p3.topic_keywords
    assert "product" in p3.topic_keywords
    assert "roadmap" in p3.topic_keywords


def test_copilot_topic_filtering_stem_and_boundary():
    """Verifies filter_assertions_for_utterance_topic matches word stems like tech->technical."""
    assertions = [
        {
            "assertion_id": "ast-1",
            "subject_node_id": "entity-sap",
            "predicate": "HAS_CLAIM_TEXT",
            "claim_text": "SAP is an identified technical_anchor in 20260928 follow up.",
        },
        {
            "assertion_id": "ast-2",
            "subject_node_id": "entity-scada",
            "predicate": "HAS_CLAIM_TEXT",
            "claim_text": "SCADA latency target is 250ms for telemetry.",
        },
    ]

    filtered = filter_assertions_for_utterance_topic(assertions, ["sap", "tech", "owner"])
    assert len(filtered) == 1
    assert filtered[0]["assertion_id"] == "ast-1"


def test_copilot_cli_leading_hyphen_argv_normalization():
    """Verifies `-copilot` CLI variations normalize cleanly for argparse without masking unknown flags."""
    from dual_pipeline.cli.copilot_handlers import normalize_copilot_argv
    from dual_pipeline.cli.parser import build_parser

    parser = build_parser()

    a1 = parser.parse_args(normalize_copilot_argv(["copilot", "-copilot next best point to live transcript", "--active"]))
    assert a1.command == "copilot"
    assert a1.request == "-copilot next best point to live transcript"
    assert a1.active is True

    a2 = parser.parse_args(normalize_copilot_argv(["-copilot", "respond to Dan", "--active"]))
    assert a2.command == "copilot"
    assert a2.prompt == "respond to Dan"
    assert a2.active is True

    a3 = parser.parse_args(normalize_copilot_argv(["-copilot respond", "--active"]))
    assert a3.command == "copilot"
    assert a3.request == "-copilot respond"
    assert a3.active is True

    a4 = parser.parse_args(normalize_copilot_argv(["copilot", "--request", "- copilot respond", "--active"]))
    assert a4.request == "- copilot respond"

    # Unknown single-token flags remain untouched so GovernedArgumentParser fails closed
    assert normalize_copilot_argv(["copilot", "--bogus-flag"]) == ["copilot", "--bogus-flag"]


def test_copilot_meta_words_aliases_and_whisper_hallucination_hygiene():
    """Verifies meta-words do not leak into keywords/anchors, Stepfan/Stephan match, and Whisper hallucinations strip."""
    from dual_pipeline.cli.copilot_speech import render_speaker_comments
    from dual_pipeline.live_meeting_recorder_core.copilot_context import SelectedUtterance
    from dual_pipeline.live_meeting_recorder_core.copilot_offline import resolve_implicit_respond_target
    from dual_pipeline.live_meeting_recorder_core.copilot_context_model import ConversationContextModel
    from dual_pipeline.live_meeting_recorder_core.copilot_roster_bridge import are_same_speaker_identity
    from dual_pipeline.live_meeting_recorder_core.copilot_utterance_resolver import (
        _clean_live_turn_claim,
        merge_live_and_graph_assertions,
        score_utterance_domain_substance,
    )

    assert are_same_speaker_identity("Stepfan", "Cameron Mercer") is True
    assert are_same_speaker_identity("Stefan", "Stephen") is True

    p_meta = parse_copilot_request(raw_request="- copilot respond next best point - most intelligent latest response")
    assert "copilot" not in p_meta.topic_keywords
    assert "most" not in p_meta.topic_keywords
    assert "latest" not in p_meta.topic_keywords
    assert "intelligent" not in p_meta.topic_keywords

    p_resp = parse_copilot_request(raw_request="-copilot respond")
    assert p_resp.inferred_intent == "respond"
    assert p_resp.target_speaker is None

    # Whisper broadcast silence hallucination, prompt glossary echo, and headless/verbless fragment stripping
    from dual_pipeline.live_meeting_recorder_core.copilot_utterance_resolver import _is_glossary_echo_or_headless

    assert _clean_live_turn_claim("Thank you for this excellent opportunity. I'll see you next time.") == ""
    assert _clean_live_turn_claim("This is a presentation of the NERC-CIP.") == ""
    assert _clean_live_turn_claim("was the only person there. Tune in on the program.") == ""
    assert _is_glossary_echo_or_headless("Apex Global Analytics, Nexus, ROCC, CIP-005, Drew Oakley, PartnerAdvisory") is True
    assert _is_glossary_echo_or_headless("For Brad, the apartment") is True
    assert score_utterance_domain_substance("Thank you for watching. See you next time.") < 0.0

    # Implicit target resolution for live `respond`
    cm = ConversationContextModel(project_scope="project:nexus", attendees=["Cameron Mercer", "Dan", "Amit Ayer"])
    cm.add_turn("Amit Ayer", "Let us review the architecture.", "00:01:00", turn_sec=60.0)
    cm.add_turn("Dan", "We need reusable model evaluation pipelines across workstreams.", "00:01:15", turn_sec=75.0)
    resolved_spk, missing = resolve_implicit_respond_target("respond", None, cm)
    assert missing is False
    assert resolved_spk == "Dan"

    # Interleaving live_turn_1 + verified GraphDB assertions (and novel non-echo override)
    live = [
        {"assertion_id": "live_turn_1", "claim_text": "Reusable model evaluation pipelines", "is_live_turn": True},
        {"assertion_id": "live_turn_2", "claim_text": "Secondary live turn", "is_live_turn": True},
    ]
    graph = [{"assertion_id": "asrt_graph_1", "claim_text": "Shared telemetry baseline reduces deployment cost."}]
    interleaved = merge_live_and_graph_assertions(live, graph, "live transcript respond", explicit_live=True)
    assert [a["assertion_id"] for a in interleaved[:2]] == ["live_turn_1", "asrt_graph_1"]

    novel_first = merge_live_and_graph_assertions(live, graph, "novel point no repeating back live transcript", explicit_live=True)
    assert novel_first[0]["assertion_id"] == "asrt_graph_1"

    class _DummyRet:
        engagement_id = "project:nexus"
        _topology = None

    sel = SelectedUtterance(
        seq=1, speaker="Dan", text="Reusable model evaluation pipelines",
        timestamp="00:01:15", turn_sec=75.0, relevance_score=1.0, matched_keywords=["most"],
    )
    rendered = render_speaker_comments("next-questions", "Dan", [live[0]], max_words=60, retriever=_DummyRet(), selected_utterance=sel, max_items=1)
    assert "on your most point" not in rendered.body
    assert "what specific guardrails or next steps should we lock in on" in rendered.body.lower()
