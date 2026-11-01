"""
Adversarial and regression test suite for Stage 03 graph healer improvements.
Covers:
- Exact production regression: NEA Agentic Governance Evals Output 03
- Boundary payload: empty and minimal mutation payloads
- Malformed input: corrupted proposal and signal data types
- Invariant bypass attempt: rogue attendee evidence clobbering prevention
- Overflow & exhaustion: chained conversational speech fragment stripping
"""
from __future__ import annotations

import copy
from typing import Any, Dict

import pytest

from dual_pipeline.transcript_qa_core.graph_healer import heal_graph_projection


@pytest.fixture
def base_candidate_payload() -> Dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "meeting_id": "meeting-nea-evals-20260902",
        "evidence_index": {
            "ev-01": {
                "evidence_id": "ev-01",
                "speaker": "person:andrew-hanson",
                "text": "The agent assessment workflow evaluates agent maturity across governance dimensions.",
                "exact_text": "The agent assessment workflow evaluates agent maturity across governance dimensions.",
            },
            "ev-02": {
                "evidence_id": "ev-02",
                "speaker": "person:andrew-hanson",
                "text": "We map identified agent gaps into a gap register and prioritize remediation in an optimization backlog.",
                "exact_text": "We map identified agent gaps into a gap register and prioritize remediation in an optimization backlog.",
            },
            "ev-03": {
                "evidence_id": "ev-03",
                "speaker": "person:andrew-hanson",
                "text": "We'd like to create some solution to where it's like a constant heartbeat right so that we can monitor how well these agents are not only doing at this initial checkpoint But just how they're evolving over time.",
                "exact_text": "We'd like to create some solution to where it's like a constant heartbeat right so that we can monitor how well these agents are not only doing at this initial checkpoint But just how they're evolving over time.",
            },
            "ev-16": {
                "evidence_id": "ev-16",
                "speaker": "person:amit-ayer",
                "text": "Amit Ayer (Lead Enterprise Architect)",
                "exact_text": "Amit Ayer (Lead Enterprise Architect)",
            },
            "ev-17": {
                "evidence_id": "ev-17",
                "speaker": "person:andrew-hanson",
                "text": "Andrew Hanson (AI/ML Director)",
                "exact_text": "Andrew Hanson (AI/ML Director)",
            },
            "ev-18": {
                "evidence_id": "ev-18",
                "speaker": "person:tanner-stevens",
                "text": "Tanner Stevens (Product Manager)",
                "exact_text": "Tanner Stevens (Product Manager)",
            },
            "ev_hitl_role_andrew-hanson": {
                "evidence_id": "ev_hitl_role_andrew-hanson",
                "speaker": "Operator",
                "text": "Human operator verified professional title 'AI/ML Director' and organization 'PartnerAdvisory' for Andrew Hanson.",
                "exact_text": "Human operator verified professional title 'AI/ML Director' and organization 'PartnerAdvisory' for Andrew Hanson.",
            },
            "ev_hitl_role_tanner-stevens": {
                "evidence_id": "ev_hitl_role_tanner-stevens",
                "speaker": "Operator",
                "text": "Human operator verified professional title 'Product Manager' and organization 'Apex Global Analytics (NEA)' for Tanner Stevens.",
                "exact_text": "Human operator verified professional title 'Product Manager' and organization 'Apex Global Analytics (NEA)' for Tanner Stevens.",
            },
        },
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "person:andrew-hanson",
                    "type": "person",
                    "name": "Andrew Hanson",
                    "evidence_ids": ["ev-17", "ev_hitl_role_andrew-hanson"],
                },
                {
                    "id": "person:amit-ayer",
                    "type": "person",
                    "name": "Amit Ayer",
                    "evidence_ids": ["ev-16"],
                },
                {
                    "id": "person:tanner-stevens",
                    "type": "person",
                    "name": "Tanner Stevens",
                    "evidence_ids": ["ev-18", "ev_hitl_role_tanner-stevens"],
                },
                {
                    "id": "org:partner_advisory",
                    "type": "org",
                    "name": "PartnerAdvisory",
                    "evidence_ids": ["ev_hitl_role_andrew-hanson"],
                },
                {
                    "id": "org:apex_global-analytics-nea",
                    "type": "org",
                    "name": "Apex Global Analytics (NEA)",
                    "evidence_ids": ["ev-18", "ev_hitl_role_tanner-stevens"],
                },
                {
                    "id": "org:nea",
                    "type": "org",
                    "name": "NEA",
                    "evidence_ids": ["ev-03"],
                },
                {
                    "id": "system:agent-assessment-workflow",
                    "type": "system",
                    "name": "Agent Assessment Workflow",
                    "evidence_ids": ["ev-01"],
                },
                {
                    "id": "system:agent-maturity-assessment",
                    "type": "system",
                    "name": "Agent Maturity Assessment",
                    "evidence_ids": ["ev-01"],
                },
                {
                    "id": "system:agent-gap-register",
                    "type": "system",
                    "name": "Agent Gap Register",
                    "evidence_ids": ["ev-02"],
                },
                {
                    "id": "system:agent-optimization-backlog",
                    "type": "system",
                    "name": "Agent Optimization Backlog",
                    "evidence_ids": ["ev-02"],
                },
            ],
            "relationship_signals": [
                {
                    "from_id": "person:andrew-hanson",
                    "to_id": "org:partner_advisory",
                    "relationship": "MEMBER_OF",
                    "evidence_ids": ["ev_hitl_role_andrew-hanson"],
                },
                {
                    "from_id": "person:amit-ayer",
                    "to_id": "org:partner_advisory",
                    "relationship": "MEMBER_OF",
                    "evidence_ids": ["ev-16"],
                },
                {
                    "from_id": "person:tanner-stevens",
                    "to_id": "org:apex_global-analytics-nea",
                    "relationship": "MEMBER_OF",
                    "evidence_ids": ["ev_hitl_role_tanner-stevens"],
                },
                {
                    "from_id": "org:nea",
                    "to_id": "org:partner_advisory",
                    "relationship": "PARTNERS_WITH",
                    "evidence_ids": ["ev-03"],
                },
                {
                    "from_id": "system:agent-assessment-workflow",
                    "to_id": "system:agent-maturity-assessment",
                    "relationship": "ENABLES",
                    "evidence_ids": ["ev-01"],
                },
                {
                    "from_id": "system:agent-assessment-workflow",
                    "to_id": "system:agent-gap-register",
                    "relationship": "ENABLES",
                    "evidence_ids": ["ev-02"],
                },
                {
                    "from_id": "system:agent-assessment-workflow",
                    "to_id": "system:agent-optimization-backlog",
                    "relationship": "ENABLES",
                    "evidence_ids": ["ev-02"],
                },
            ],
            "proposal_updates": [
                {
                    "proposal_id": "proposal:longitudinal-agent-monitoring",
                    "description": "Develop a recurring monitoring approach to assess agent performance at an initial checkpoint and track changes over time.",
                    "rationale": "We'd like to create some solution to where it's like a constant heartbeat right so that we can monitor how well these agents are not only doing at this initial checkpoint But just how they're evolving over time.",
                    "evidence_ids": ["ev-03"],
                },
                {
                    "proposal_id": "proposal:nea-agent-evaluation-pilot",
                    "description": "Evaluate the agent assessment approach on three to five additional NEA applications and use the results to identify improvements.",
                    "rationale": "And then I think, Tanner, my thought would be, let's, can we talk, can we work with you on, okay, what would be, and we'll talk to Dave Sunderman and some others, right? Say, okay, let's deploy this on like maybe three to five other applications on the next service, for you guys.",
                    "evidence_ids": ["ev-03"],
                },
                {
                    "proposal_id": "proposal:agent-enterprise-signoff-enforcement",
                    "description": "Condition agent registry participation on enterprise sign-off and required governance controls.",
                    "rationale": "There's a participation type aspect of it of like if it really gets to be like enterprise sign off and symphony is the agent registry for xdera then it's like the apple store like you can't get on you can't play until you put this stuff in place right so we can do that type of enforcement or we can just if we have the.",
                    "evidence_ids": ["ev-03"],
                },
                {
                    "proposal_id": "proposal:14",
                    "description": "Symphony that way we just whatever they do releases or something that. Maybe it also promotes whatever those updates are to Symphony I don't know. I'm just trying to think of a way that it's a it's it's a programmatic back and forth Right and so because ultimately what Symphony is also intending to do now mind you there's.",
                    "rationale": "Symphony that way we just whatever they do releases or something like that. Maybe it also promotes whatever those updates are to Symphony I don't know. I'm just trying to think of a way that like it's a it's it's a programmatic back and forth Right and so because ultimately what Symphony is also intending to do now mind you there's.",
                    "evidence_ids": ["ev-03"],
                },
            ],
        },
    }


def test_exact_production_regression_nea_evals_output3_healing(base_candidate_payload: Dict[str, Any]) -> None:
    """Exact production regression: verify all 4 RCA defects heal cleanly."""
    changes = []
    healed = heal_graph_projection(
        base_candidate_payload,
        changes,
        metadata={"meeting_name": "NEA Agentic Governance Evals"},
    )

    gm = healed.get("graph_mutations", {})

    # 1. Proposal:14 converted to semantic ID with clean non-conversational text
    props = {p.get("proposal_id") or p.get("id"): p for p in gm.get("proposal_updates", [])}
    assert "proposal:14" not in props
    assert "proposal:symphony-programmatic-updates" in props
    p14 = props["proposal:symphony-programmatic-updates"]
    assert "I don't know" not in p14.get("description", "")
    assert "I don't know" not in p14.get("rationale", "")
    assert "programmatic" in p14.get("description", "").lower()
    assert "programmatic" in p14.get("rationale", "").lower()

    # 2. Oral monologues and trailing fragments paraphrased
    p_signoff = props["proposal:agent-enterprise-signoff-enforcement"]
    assert "or we can just if we have the" not in p_signoff.get("rationale", "")
    assert "mandatory governance controls" in p_signoff.get("rationale", "")

    p_pilot = props["proposal:nea-agent-evaluation-pilot"]
    assert "can we talk" not in p_pilot.get("rationale", "")
    assert "three to five additional NEA applications" in p_pilot.get("rationale", "")

    p_monitor = props["proposal:longitudinal-agent-monitoring"]
    assert "constant heartbeat right so" not in p_monitor.get("rationale", "")
    assert "operational heartbeat" in p_monitor.get("rationale", "")

    # 3. Deliverables typed as workstream
    ents = {e.get("id"): e for e in gm.get("entity_upserts", [])}
    assert ents["system:agent-maturity-assessment"]["type"] == "workstream"
    assert ents["system:agent-gap-register"]["type"] == "workstream"
    assert ents["system:agent-optimization-backlog"]["type"] == "workstream"

    # 4. Split org:nea consolidated into org:apex_global-analytics-nea
    assert "org:nea" not in ents
    assert "org:apex_global-analytics-nea" in ents

    # 5. Amit Ayer retains ev-16 and is NOT clobbered with Andrew Hanson's verification record
    sigs = gm.get("relationship_signals", [])
    amit_sigs = [s for s in sigs if s.get("from_id") == "person:amit-ayer" and s.get("to_id") == "org:partner_advisory"]
    assert len(amit_sigs) == 1
    assert amit_sigs[0]["evidence_ids"] == ["ev-16"]

    # 6. Ungrounded partnership edge pruned
    partner_sigs = [s for s in sigs if s.get("relationship") == "PARTNERS_WITH" or s.get("predicate") == "PARTNERS_WITH"]
    assert len(partner_sigs) == 0


def test_boundary_payload_empty_and_minimal_mutations() -> None:
    """Boundary payload: empty dictionaries, null fields, and minimal payloads."""
    changes = []
    res = heal_graph_projection({}, changes)
    assert isinstance(res, dict)
    assert res["evidence_index"] == {}

    changes.clear()
    res2 = heal_graph_projection({"graph_mutations": {"entity_upserts": []}}, changes)
    assert isinstance(res2, dict)
    assert res2["graph_mutations"]["entity_upserts"] == []


def test_malformed_input_corrupted_proposals_and_signals() -> None:
    """Malformed input: non-dict list items, missing keys, and unexpected types."""
    changes = []
    payload = {
        "evidence_index": {"ev-01": {"text": "Valid reference"}},
        "graph_mutations": {
            "entity_upserts": ["not-a-dict", None, {"id": "system:test", "type": "system"}],
            "relationship_signals": [123, None, {"from_id": "system:test", "to_id": "org:partner_advisory"}],
            "proposal_updates": [None, "invalid-proposal", {"proposal_id": "proposal:05", "description": "what"}],
        },
    }
    healed = heal_graph_projection(payload, changes)
    gm = healed.get("graph_mutations", {})
    # proposal:05 with description 'what' must be evicted
    assert len(gm.get("proposal_updates", [])) == 0


def test_invariant_bypass_attempt_attendee_evidence_preservation() -> None:
    """Invariant bypass attempt: ensure attendee evidence is never overwritten by foreign speaker record."""
    changes = []
    payload = {
        "evidence_index": {
            "ev-attendee-amit": {
                "evidence_id": "ev-attendee-amit",
                "speaker": "person:amit-ayer",
                "text": "Amit Ayer Lead Enterprise Architect PartnerAdvisory",
            },
            "ev_hitl_role_andrew-hanson": {
                "evidence_id": "ev_hitl_role_andrew-hanson",
                "speaker": "Operator",
                "text": "Human operator verified professional title 'AI/ML Director' and organization 'PartnerAdvisory' for Andrew Hanson.",
            },
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "person:amit-ayer", "type": "person", "name": "Amit Ayer", "evidence_ids": ["ev-attendee-amit"]},
                {"id": "org:partner_advisory", "type": "org", "name": "PartnerAdvisory", "evidence_ids": ["ev_hitl_role_andrew-hanson"]},
            ],
            "relationship_signals": [
                {
                    "from_id": "person:amit-ayer",
                    "to_id": "org:partner_advisory",
                    "relationship": "MEMBER_OF",
                    "evidence_ids": ["ev-attendee-amit"],
                }
            ],
        },
    }
    healed = heal_graph_projection(payload, changes)
    sig = healed["graph_mutations"]["relationship_signals"][0]
    assert sig["evidence_ids"] == ["ev-attendee-amit"]
    assert sig["evidence_ids"] != ["ev_hitl_role_andrew-hanson"]


def test_overflow_and_trailing_fragment_exhaustion() -> None:
    """Overflow & exhaustion: chained trailing fragments stripped cleanly to atomic sentences."""
    changes = []
    payload = {
        "evidence_index": {"ev-01": {"text": "Valid reference"}},
        "graph_mutations": {
            "proposal_updates": [
                {
                    "proposal_id": "proposal:test-overflow",
                    "description": "Establish automated test gates, or we can just if we have the, now mind you there's.",
                    "rationale": "Automated verification ensures regression prevention, and then... I don't know...",
                    "evidence_ids": ["ev-01"],
                }
            ]
        },
    }
    healed = heal_graph_projection(payload, changes)
    p = healed["graph_mutations"]["proposal_updates"][0]
    assert not p["description"].endswith("there's.")
    assert not p["rationale"].endswith("don't know...")
