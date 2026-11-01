"""Wave 2 tests: Live-Transcript-Only Directives, Personal Scope Isolation & Single-Speaker Alias Resolution."""
from __future__ import annotations

from types import SimpleNamespace

from dual_pipeline.live_meeting_recorder_core.copilot_connection_policy import (
    is_assertion_in_scope,
    is_node_in_scope,
)
from dual_pipeline.live_meeting_recorder_core.copilot_context import parse_copilot_request
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import ConversationContextModel
from dual_pipeline.live_meeting_recorder_core.copilot_intents import evaluate_intent
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import CopilotGraphRetriever
from dual_pipeline.live_meeting_recorder_core.copilot_utterance_resolver import _resolve_speaker_and_utterance


def _build_nee_enterprise_topology() -> SimpleNamespace:
    nodes = {
        "person_dan_okray": {
            "node_id": "person_dan_okray",
            "canonical_name": "Drew Oakley",
            "display_name": "Drew Oakley",
            "node_type": "person",
            "namespace": "ENTERPRISE_POLICY",
        },
        "org_edo": {
            "node_id": "org_edo",
            "canonical_name": "EDO",
            "display_name": "EDO",
            "node_type": "organization",
            "namespace": "ENTERPRISE_POLICY",
        },
    }
    assertions = {
        f"ast_edo_{i}": {
            "assertion_id": f"ast_edo_{i}",
            "subject_node_id": "person_dan_okray" if i % 2 == 0 else "org_edo",
            "object_node_id": "org_edo",
            "predicate": "CHAMPIONS",
            "claim_text": f"As AI adoption grows across Apex Global, teams want to write back to SAP and SuccessFactors ({i}).",
            "namespace": "ENTERPRISE_POLICY",
            "is_enterprise_policy": True,
            "canonical_status": "canonical",
            "confidence_score": 0.95,
            "evidence_ids": [f"ev_edo_{i}", f"src_edo_{i}"],
        }
        for i in range(10)
    }
    return SimpleNamespace(nodes=nodes, assertions=assertions, supporting_evidence={})


def test_parse_live_transcript_only_directive_strips_meta_pollution() -> None:
    """'respond back to Shari Perlwocz based on live transcript only, do not look at NEE graph' must not pollute keywords."""
    raw = "respond back to Shari Perlwocz based on live transcript only, do not look at NEE graph"
    parsed = parse_copilot_request(raw)
    assert parsed.inferred_intent == "respond"
    assert parsed.target_speaker == "Shari"
    assert parsed.live_transcript_only is True
    for forbidden in ("back", "only", "not", "look", "nee", "graph", "perlwocz"):
        assert forbidden not in [k.lower() for k in parsed.topic_keywords]


def test_tuckers_teacher_single_speaker_alias_and_live_only_isolation() -> None:
    """Parent-teacher call ('Tucker's Teacher') must resolve 'Shari Perlwocz' and return 0 NEE graph assertions."""
    ctx = ConversationContextModel(project_scope="project:personal", attendees=["Amit", "Tucker's Teacher"], focus_seconds=300.0)
    ctx.add_turn(
        speaker="Tucker's Teacher",
        text="Tucker is doing a wonderful job in reading group and his letter recognition has grown so much this quarter.",
        timestamp="00:05:10",
        turn_sec=310.0,
    )
    ctx.add_turn(
        speaker="Tucker's Teacher",
        text="He works really well with Miles and loves helping his classmates during morning circle.",
        timestamp="00:05:25",
        turn_sec=325.0,
    )
    ctx._raw_request = "respond back to Shari Perlwocz based on live transcript only, do not look at NEE graph"

    topo = _build_nee_enterprise_topology()
    resolved_target, utt, window, err = _resolve_speaker_and_utterance("Shari", [], ctx, topo)
    assert err is None
    assert resolved_target == "Shari"
    assert utt is not None
    assert "Tucker" in utt.text or "Miles" in utt.text

    retriever = CopilotGraphRetriever(
        topology=topo,
        attendees=[{"name": "Amit"}, {"name": "Tucker's Teacher"}],
        engagement_id="project:personal",
    )
    body, assertions, ok = evaluate_intent(
        "respond",
        target="Shari",
        dialogue=ctx,
        retriever=retriever,
        topic_keywords=[],
        selected_utterance=utt,
        topic_query=None,
    )
    assert ok is True
    assert len(assertions) >= 1
    assert all(a.get("source_type") == "ACOUSTIC_TRANSCRIPT" for a in assertions)
    assert all("Apex Global" not in str(a.get("claim_text", "")) and "SAP" not in str(a.get("claim_text", "")) for a in assertions)


def test_personal_and_unmapped_engagement_suppresses_nee_enterprise_policy() -> None:
    """Personal scope and unmapped school engagements must not leak NEE enterprise_policy assertions."""
    edo_assertion = {
        "assertion_id": "ast_edo_1",
        "claim_text": "EDO operates enterprise-wide across Apex Global Enterprises.",
        "namespace": "ENTERPRISE_POLICY",
        "is_enterprise_policy": True,
    }
    edo_node = {
        "node_id": "org_edo",
        "canonical_name": "EDO",
        "namespace": "ENTERPRISE_POLICY",
        "is_enterprise_policy": True,
    }
    assert is_assertion_in_scope(edo_assertion, "project:personal") is False
    assert is_node_in_scope("org_edo", edo_node, "project:personal", is_attendee=False) is False

    topo = _build_nee_enterprise_topology()
    ctx = ConversationContextModel(
        project_scope="project:miles_evenlyn_rivera_spector",
        attendees=["Amit", "Evelyn Rivera", "Arlene Spector"],
        focus_seconds=300.0,
    )
    ctx.add_turn(
        speaker="Evelyn Rivera",
        text="Miles has made strong progress in reading comprehension and phonics decoding this semester.",
        timestamp="00:02:00",
        turn_sec=120.0,
    )
    resolved_target, utt, _, err = _resolve_speaker_and_utterance("Evelyn Rivera", [], ctx, topo)
    assert err is None
    assert utt is not None

    retriever = CopilotGraphRetriever(
        topology=topo,
        attendees=[{"name": "Amit"}, {"name": "Evelyn Rivera"}, {"name": "Arlene Spector"}],
        engagement_id="project:miles_evenlyn_rivera_spector",
    )
    _, assertions, ok = evaluate_intent(
        "respond",
        target=resolved_target,
        dialogue=ctx,
        retriever=retriever,
        topic_keywords=[],
        selected_utterance=utt,
        topic_query=None,
    )
    assert ok is True
    assert all(a.get("source_type") == "ACOUSTIC_TRANSCRIPT" for a in assertions)
    assert all("Apex Global" not in str(a.get("claim_text", "")) for a in assertions)
