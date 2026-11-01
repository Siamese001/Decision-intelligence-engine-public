"""
tests/adversarial/test_graph_extraction_adversarial.py

Adversarial test suite exercising Wave 7 Graph Extraction boundaries:
1. Boundary payloads: arbitrary unseen attendees (Sarah Chen, Carlos Rodriguez) succeed with substantive descriptions.
2. Invalid evidence kinds fail closed.
3. Conversational filler ('two big ones', 'yeah and then') is cleansed or evicted pre-flight.
4. Mutation budget triage respects semantic gravity without deleting decisions.
"""
import pytest
from pydantic import ValidationError

from dual_pipeline.transcript_models import (
    EvidenceStatement,
)
from dual_pipeline.transcript_qa_core.gate3_roles import (
    lint_person_descriptions,
)
from dual_pipeline.transcript_qa_core.graph_healer import heal_graph_projection


def test_unseen_attendees_succeed_without_hardcoding():
    entities = [
        {
            "id": "person:sarah-chen",
            "type": "person",
            "name": "Sarah Chen",
            "description": "Enterprise Solutions Architect at Client Corp, evaluating integration feasibility.",
        },
        {
            "id": "person:carlos-rodriguez",
            "type": "person",
            "name": "Carlos Rodriguez",
            "description": "Cloud Security Lead governing identity boundary compliance.",
        }
    ]
    passed, failures, stats = lint_person_descriptions(entities)
    assert passed, f"Unseen attendees should pass Gate 3.11 cleanly: {failures}"
    assert stats["person_entities_checked"] == 2


def test_adversarial_conversational_filler_payload_healed():
    payload = {
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "system:nexus",
                    "type": "system",
                    "name": "Nexus Core",
                    "description": "Central decision intelligence engine."
                }
            ],
            "relationship_signals": [],
            "proposal_updates": [
                {
                    "proposal_id": "proposal:05",
                    "description": "So those are the main, I think those are the two big ones that would stick out to me as additional areas that could be applicable to Jarvis.",
                    "rationale": "Yeah, and then we need to document the boundary constraints."
                },
                {
                    "proposal_id": "proposal:toxic-filler",
                    "description": "Those are the main, I think those are the two big ones",
                    "rationale": "Just chatting."
                }
            ],
            "decision_updates": [
                {
                    "decision_id": "decision:nexus-architecture",
                    "mutation": "CONFIRM",
                    "status": "RATIFIED",
                    "decision_question": "Adopt modular dual pipeline DAG?",
                    "observed_at": "09/28/2026",
                    "stale_after": "12/28/2026"
                }
            ]
        },
        "evidence_index": {
            "ev-01": {"text": "Architecture discussion", "kind": "technical"}
        }
    }
    changes = []
    healed = heal_graph_projection(payload, changes)
    props = healed["graph_mutations"]["proposal_updates"]

    # proposal:toxic-filler was pure sludge with no substance -> evicted
    assert not any(p.get("proposal_id") == "proposal:toxic-filler" for p in props)

    # proposal:05 had substance after the filler prefix -> cleansed
    prop05 = next(p for p in props if p.get("proposal_id") == "proposal:05")
    assert not prop05["description"].startswith("So those are the main")
    assert "Additional areas that could be applicable to Jarvis." in prop05["description"]
    assert not prop05["rationale"].startswith("Yeah, and then")

    # Critical invariant: decisions are NEVER erased by the healer
    assert len(healed["graph_mutations"]["decision_updates"]) == 1
    assert healed["graph_mutations"]["decision_updates"][0]["decision_id"] == "decision:nexus-architecture"


def test_invalid_evidence_kind_rejected():
    with pytest.raises(ValidationError):
        EvidenceStatement(
            speaker="Taylor Mercer",
            kind="unauthorized_kind",
            text="Invalid taxonomy test"
        )
