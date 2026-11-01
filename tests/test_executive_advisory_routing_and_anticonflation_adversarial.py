"""Adversarial and regression tests for Wave 3: Executive Advisory Routing, L2 Synthesis Activation & Anti-Conflation."""

from __future__ import annotations

from unittest.mock import patch

from dual_pipeline.l2_output_contract import FIELD_ELIGIBILITY
from dual_pipeline.query_core.reasoning import validate_answer
from dual_pipeline.query_router import QueryDestination, route_query
from dual_pipeline.query_router_core.intent import _query_type
from dual_pipeline.query_router_core.policy import load_query_routing_policy
from dual_pipeline.query_core.anti_conflation import (
    validate_answer_anti_conflation,
)
from dual_pipeline.transcript_stages_core.anti_conflation import (
    CompoundWorkstreamConflationError,
    is_compound_workstream_reference,
    validate_anti_conflation,
)


def test_executive_question_routes_to_nee_decision_intelligence_with_l2_required() -> None:
    """Exact production regression: executive question with named entities routes to NEE_DECISION_INTELLIGENCE."""
    with patch("dual_pipeline.query_router._check_graphdb_available", return_value=True):
        decision = route_query(
            "Draft an executive question for our PartnerAdvisory all-hands anchored in our 106-engineer Apex Global Enterprises footprint and co-hydrated operating model."
        )
        assert decision.destination == QueryDestination.NEE_DECISION_INTELLIGENCE
        assert decision.query_type == "cross_workstream_synthesis"
        assert decision.materiality == "high"
        assert decision.handoff_permitted is False
        assert decision.eligibility_checks["l2_reasoning_required"] == "PASS"
        assert "EXECUTIVE_SYNTHESIS_FORCED" in decision.reason_codes


def test_imperative_synthesis_verb_with_entity_does_not_handoff_to_general_assistant() -> None:
    """Invariant bypass attempt: 'Write/Draft/Compose ... Apex Global/PartnerAdvisory' must not bypass GraphDB via GENERAL_ASSISTANT_HANDOFF."""
    with patch("dual_pipeline.query_router._check_graphdb_available", return_value=True):
        decision = route_query(
            "Write a stakeholder prep summary for Apex Global Enterprises and Engineering Exchange."
        )
        assert decision.destination == QueryDestination.NEE_DECISION_INTELLIGENCE
        assert decision.handoff_permitted is False


def test_candidate_exclusion_complaint_does_not_route_to_candidate_promotion_review() -> None:
    """Boundary & regression: complaining about candidate inclusion must not trigger candidate_promotion_review."""
    policy = load_query_routing_policy()
    q = (
        "Strip out all candidate assertions from the Apex Global Enterprises architecture synthesis; "
        "candidates should never be cited in an executive question."
    )
    qtype = _query_type(policy, q, None)
    assert qtype != "candidate_promotion_review"
    assert qtype == "cross_workstream_synthesis"


def test_l2_output_contract_restricts_current_state_to_approved_lane() -> None:
    """Verify FIELD_ELIGIBILITY for current_state and counterevidence permits only approved lane."""
    assert FIELD_ELIGIBILITY["current_state"] == (("approved",), "factual_answer")
    assert FIELD_ELIGIBILITY["counterevidence"] == (("approved",), "factual_answer")


def test_anti_conflation_blocks_swarm_flexai_splicing_and_106_vs_66_conflation() -> None:
    """Exact production regression: Engineering Exchange citing source_chakra must not splice LENS, Swarm, FlexAI, and 66 NEA record must not be cited for 106 engineers."""
    assert is_compound_workstream_reference("LENS / Swarm") is True
    assert is_compound_workstream_reference("Swarm & FlexAI") is True

    packet = {
        "approved": [
            {
                "record": {
                    "record_id": "rec-66-nea",
                    "record_type": "edge",
                    "lane": "approved",
                    "title": "PartnerAdvisory allocates 66 engineers to NEA",
                    "retrieval_text": "PartnerAdvisory allocates 66 engineering capacity to Apex Global Analytics (NEA).",
                    "evidence_ids": ["ev-66"],
                },
                "retrieval_metadata": {"answer_use": "factual_answer"},
            },
            {
                "record": {
                    "record_id": "rec-eng-ex",
                    "record_type": "edge",
                    "lane": "approved",
                    "title": "Engineering Exchange builds pattern library",
                    "retrieval_text": "Engineering Exchange builds a cross-workstream reusable pattern library.",
                    "evidence_ids": ["ev-chakra"],
                },
                "retrieval_metadata": {"answer_use": "factual_answer"},
            },
        ],
        "candidate": [],
        "external_public": [],
    }

    bad_answer = {
        "query_type": "cross_workstream_synthesis",
        "current_state": [
            {
                "statement": "At Apex Global Enterprises, where we have 106 software engineers allocated to NEA.",
                "citations": ["rec-66-nea"],
                "evidence_ids": ["ev-66"],
                "lane": "approved",
            },
            {
                "statement": "The Engineering Exchange builds a cross-workstream reusable pattern library across LENS, Swarm, and FlexAI.",
                "citations": ["rec-eng-ex"],
                "evidence_ids": ["ev-chakra"],
                "lane": "approved",
            },
        ],
        "patterns": [],
        "pain_points": [],
        "opportunities": [],
        "guidance": [],
        "hypotheses": [],
        "source_evidence": [],
        "next_best_actions": [],
        "rationale": "Test rationale.",
        "counterevidence": [],
        "missing_information": [],
        "assumptions": ["Snapshot is current."],
        "what_would_change_the_conclusion": ["New evidence."],
        "proposed_assertions": [],
        "automatic_graph_mutation_count": 0,
    }

    failures = validate_answer_anti_conflation(bad_answer, packet)
    assert any("org_scope_conflation_106_vs_66_nea" in f for f in failures)
    assert any("numeric_scope_conflation_106_on_66_record" in f for f in failures)
    assert any("uncorroborated_workstream_splicing(lens)" in f for f in failures)
    assert any("uncorroborated_workstream_splicing(swarm)" in f for f in failures)
    assert any("uncorroborated_workstream_splicing(flexai)" in f for f in failures)

    res = validate_answer(bad_answer, packet)
    assert res["status"] == "FAIL"
    assert len(res["anti_conflation_failures"]) >= 4
