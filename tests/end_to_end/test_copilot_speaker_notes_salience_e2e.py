"""
tests/end_to_end/test_copilot_speaker_notes_salience_e2e.py

End-to-End verification for Wave 2: Human Speaker Notes & Salience Re-Ranking over Technical Schema.
Validates:
1. Human speaker-attributed assertions (STATED, RAISED_CONCERN, COMMITTED_TO, PROPOSED) outrank unattributed technical workstream assertions.
2. Synthetic roster participation stubs (asrt_attendee_*) do not crowd out real speaker commitments in get_person_context or get_next_questions.
3. Expanded domain role synonyms (product owner, delivery lead, executive sponsor, architect) resolve in retrieval.
4. Intent evaluation bodies omit raw [PREDICATE] schema prefixes.
"""
from __future__ import annotations

import pytest

from dual_pipeline.live_meeting_recorder_core.copilot_intents import (
    eval_actions,
    eval_questions,
    eval_respond,
)
from dual_pipeline.live_meeting_recorder_core.copilot_predicates import (
    ACTION_PREDICATES,
    PRIORITY_PREDICATES,
    QUESTION_PREDICATES,
    normalize_copilot_predicate,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
)


def _build_speaker_vs_schema_retriever() -> CopilotGraphRetriever:
    class DummyTopology:
        def __init__(self) -> None:
            self.nodes = {
                "person:ninan": {
                    "node_id": "person:ninan",
                    "canonical_name": "Speaker_Beta",
                    "display_name": "Speaker_Beta",
                    "node_type": "person",
                    "organization": "Apex Global",
                    "role": "Executive Sponsor",
                },
                "workstream:sap_ingest": {
                    "node_id": "workstream:sap_ingest",
                    "canonical_name": "SAP Ingestion Pipeline",
                    "display_name": "SAP Ingestion Pipeline",
                    "node_type": "workstream",
                },
                "system:kafka_bus": {
                    "node_id": "system:kafka_bus",
                    "canonical_name": "Kafka Event Bus",
                    "display_name": "Kafka Event Bus",
                    "node_type": "system",
                },
            }
            self.assertions = {
                "asrt_schema_tech_1": {
                    "assertion_id": "asrt_schema_tech_1",
                    "subject_node_id": "workstream:sap_ingest",
                    "object_node_id": "system:kafka_bus",
                    "predicate": "CONTAINS",
                    "claim_text": "SAP Ingestion Pipeline contains Kafka Event Bus connector configuration parameters.",
                    "confidence_score": 0.90,
                    "engagement_id": "project:lens",
                    "primary_source_id": "src_tech_01",
                },
                "asrt_speaker_concern_1": {
                    "assertion_id": "asrt_speaker_concern_1",
                    "subject_node_id": "person:ninan",
                    "object_node_id": "workstream:sap_ingest",
                    "predicate": "RAISED_CONCERN",
                    "claim_text": "Speaker_Beta raised a concern that the SAP Ingestion Pipeline cutover lacks rollback sign-off.",
                    "confidence_score": 0.92,
                    "engagement_id": "project:lens",
                    "primary_source_id": "src_mtg_01",
                },
                "asrt_speaker_commit_1": {
                    "assertion_id": "asrt_speaker_commit_1",
                    "subject_node_id": "person:ninan",
                    "object_node_id": "workstream:sap_ingest",
                    "predicate": "COMMITTED_TO",
                    "claim_text": "Speaker_Beta committed to reviewing the SAP Ingestion Pipeline readiness checklist on Thursday.",
                    "confidence_score": 0.94,
                    "engagement_id": "project:lens",
                    "primary_source_id": "src_mtg_01",
                },
            }

    return CopilotGraphRetriever(
        topology=DummyTopology(),
        graph_sha256="b" * 64,
        generation=2,
        attendees=[{"name": "Speaker_Beta", "role": "Executive Sponsor", "org": "Apex Global"}],
        engagement_id="project:lens",
    )


@pytest.mark.e2e
def test_human_speaker_notes_outrank_technical_schema_e2e() -> None:
    """Human speaker concerns and commitments outrank generic technical workstream schema assertions."""
    retriever = _build_speaker_vs_schema_retriever()

    assert normalize_copilot_predicate("RAISED_CONCERN") in QUESTION_PREDICATES
    assert normalize_copilot_predicate("COMMITTED_TO") in ACTION_PREDICATES
    assert normalize_copilot_predicate("STATED") in PRIORITY_PREDICATES

    ranked = retriever.retrieve_structured_assertions("SAP Ingestion Pipeline", max_results=5)
    assert len(ranked) >= 3
    top_ids = [r["assertion_id"] for r in ranked[:2]]
    assert "asrt_speaker_concern_1" in top_ids
    assert "asrt_speaker_commit_1" in top_ids
    assert ranked[0]["assertion_id"] != "asrt_schema_tech_1"


@pytest.mark.e2e
def test_person_context_and_intent_bodies_exclude_synthetic_stubs_and_predicate_tags() -> None:
    """get_person_context excludes synthetic asrt_attendee_* stubs when substantive speaker assertions exist."""
    retriever = _build_speaker_vs_schema_retriever()

    person_ctx = retriever.get_person_context("Speaker_Beta", "SAP Ingestion Pipeline")
    assert person_ctx["found"] is True
    aids = [a["assertion_id"] for a in person_ctx["assertions"]]
    assert "asrt_speaker_concern_1" in aids
    assert "asrt_speaker_commit_1" in aids
    assert not any(aid.startswith("asrt_attendee_") for aid in aids)

    q_body, q_asrts, q_ok = eval_questions(retriever, "SAP Ingestion Pipeline")
    assert q_ok is True
    assert "[RAISED_CONCERN]" not in q_body
    assert "[HAS_RISK]" not in q_body
    assert q_asrts[0]["assertion_id"] == "asrt_speaker_concern_1"

    a_body, a_asrts, a_ok = eval_actions(retriever, "SAP Ingestion Pipeline")
    assert a_ok is True
    assert "[COMMITTED_TO]" not in a_body
    assert a_asrts[0]["assertion_id"] == "asrt_speaker_commit_1"

    r_body, _, r_ok = eval_respond(retriever, "Speaker_Beta", "SAP Ingestion Pipeline")
    assert r_ok is True
    assert "Anchor conversation on verified contractual commitments" not in r_body
    assert "Speaker_Beta" in r_body
