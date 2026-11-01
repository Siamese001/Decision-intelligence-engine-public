"""Adversarial and edge-case tests for Wave 4 anti-conflation, role-optics guardrails, and query routing."""
from __future__ import annotations

from dual_pipeline.decision_engine_core.frame import build_decision_frame
from dual_pipeline.prompt_assembly import assemble_prompt
from dual_pipeline.query_core.anti_conflation import validate_answer_anti_conflation


def test_exact_production_regression_vendor_tech_lead_optics_collision() -> None:
    """Category 7 (Exact production regression): Pitching Michael Romagnolo as Project Beacon Tech Lead triggers ORG_ROLE_OPTICS_COLLISION."""
    bad_answer = {
        "current_state": [
            {
                "statement": "We are bringing on Michael Romagnolo to serve as the new Project Beacon Tech Lead following Lillian's departure.",
                "citations": ["rec-1"],
            }
        ],
        "pain_points": [],
        "opportunities": [],
        "guidance": [],
        "hypotheses": [],
    }
    failures = validate_answer_anti_conflation(bad_answer)
    assert any("ORG_ROLE_OPTICS_COLLISION" in f for f in failures)


def test_invariant_bypass_attempt_cross_workstream_lens_personnel_on_sherpa() -> None:
    """Category 5 (Invariant bypass attempt): Attributing Project LENS personnel (Alex Sanchez, Jennifer Pan) to Project Beacon fails validation."""
    bad_answer = {
        "current_state": [
            {
                "statement": "Alex Sanchez is leading backend engineering on Project Beacon alongside Jennifer Pan.",
                "citations": ["rec-1"],
            }
        ],
        "pain_points": [],
        "opportunities": [],
        "guidance": [],
        "hypotheses": [],
    }
    failures = validate_answer_anti_conflation(bad_answer)
    assert any("cross_workstream_personnel_leakage_lens_to_sherpa" in f for f in failures)


def test_boundary_payload_generic_decision_question_still_requires_clarification() -> None:
    """Category 1 (Boundary payload): Generic questions without criteria or staffing/gap signals still return CLARIFICATION_REQUIRED."""
    frame = build_decision_frame(question="Which option should NEE choose?")
    assert frame["frame_state"] == "CLARIFICATION_REQUIRED"
    assert frame["missing_fields"] == ["criteria"]


def test_prompt_assembly_preserves_slot_g0_roster_and_roles_when_present() -> None:
    """Category 2 (Malformed/optional input): assemble_prompt includes workstream_roster_and_roles in G0 only when present in packet."""
    plan = {
        "query_type": "cross_workstream_synthesis",
        "materiality": "high",
        "retrieval_mode": "strategic",
        "subquestions": ["Who leads Sherpa?"],
        "required_output_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "prompt_blueprint": "V5.5-standard",
        "challenger_required": False,
    }
    route = {"query_route": "cross_workstream_synthesis"}
    packet = {
        "approved": [],
        "candidate": [],
        "external_public": [],
        "graph_paths": [],
        "counterevidence": [],
        "gaps": [],
        "exclusions": [],
        "exact_anchor_ids": [],
        "graph_snapshot": "snap-1",
        "packet_digest": "a" * 64,
        "workstream_roster_and_roles": {
            "client_tech_lead": "Egle Tamasevice (Apex Global Enterprises)",
            "partner_advisory_delivery_lead": "Andy Kilinskis (PartnerAdvisory)",
            "partner_advisory_opensearch_lead": "Speaker_Beta Tharakan (PartnerAdvisory)",
        },
    }
    prompt = assemble_prompt("Review Sherpa leadership", plan, route, packet)
    assert prompt["slots"]["G0"]["workstream_roster_and_roles"]["client_tech_lead"] == "Egle Tamasevice (Apex Global Enterprises)"
