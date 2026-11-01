"""End-to-end verification of Output 03 LLM-as-a-Judge RCA remediations."""
from __future__ import annotations

import copy
from typing import Any, Dict

from dual_pipeline.llm_judge_core.graph_checks import (
    apply_deterministic_graph_scoring,
    check_quote_and_evidence_resolution,
)
from dual_pipeline.llm_judge_core.judges.graph import build_graph_judge_user_content
from dual_pipeline.prompts.contracts import MeetingArchetype
from dual_pipeline.transcript_qa_core.graph_healer import _heal_signal_evidence_and_qualifiers
from dual_pipeline.transcript_qa_core.graph_reference_validator import check_graph_reference_integrity
from dual_pipeline.transcript_stages_core.graph_executor import _append_roster_block, _enrich_gs10_traits
from dual_pipeline.transcript_stages_core.quote_pointer_resolver import resolve_evidence_pointers
from dual_pipeline.transcript_stages_core.speaker_role_reconciler import reconcile_speaker_role_resolutions


def test_output3_judge_rca_pipeline_e2e() -> None:
    """Verify end-to-end Stage 03 post-processing, healing, Gate 3 validation, and Judge scoring."""
    projection: Dict[str, Any] = {
        "meeting_id": "mtg-sherpa-10-13-2026",
        "graph_mutations": {
            "entity_upserts": [
                {
                    "id": "person:amit-ayer",
                    "name": "Amit Ayer",
                    "type": "person",
                    "role": "Director",
                    "description": "Amit Ayer is Director at PartnerAdvisory.",
                    "evidence_ids": ["ev-01"],
                },
                {
                    "id": "org:partner_advisory",
                    "name": "PartnerAdvisory",
                    "type": "org",
                    "description": "Organization: PartnerAdvisory",
                    "evidence_ids": ["ev-01"],
                },
                {
                    "id": "role:director",
                    "name": "Director",
                    "type": "role",
                    "description": "Professional role: Director",
                    "evidence_ids": ["ev-01"],
                },
                {
                    "id": "project:project_beacon",
                    "name": "Project Beacon",
                    "type": "project",
                    "description": "Project Beacon adoption tracking initiative.",
                    "evidence_ids": ["ev-16"],
                },
            ],
            "relationship_signals": [
                {
                    "signal_id": "sig-1",
                    "from_id": "person:amit-ayer",
                    "to_id": "org:partner_advisory",
                    "relationship": "MEMBER_OF",
                    "description": "Amit Ayer is a member of PartnerAdvisory.",
                    "evidence_ids": ["ev-01"],
                },
                {
                    "signal_id": "sig-2",
                    "from_id": "person:amit-ayer",
                    "to_id": "role:director",
                    "relationship": "HAS_ROLE",
                    "description": "Amit Ayer holds the role of Director.",
                    "evidence_ids": ["ev-01"],
                },
            ],
            "decision_updates": [],
            "proposal_updates": [],
            "risk_updates": [],
        },
        "evidence_index": {
            "ev-01": {
                "statement_id": "stmt-01",
                "text": "Wait, let us look at the adoption numbers for Project Beacon.",
                "exact_text": "Wait, let us look at the adoption numbers for Project Beacon.",
                "speaker": "Speaker_Beta",
                "timestamp": "00:01:12",
                "locator": "00:01:12",
            },
            "ev-16": {
                "statement_id": "stmt-16",
                "text": "Project Beacon Weekly Active Users: 2,410 across business units.",
                "exact_text": "Project Beacon Weekly Active Users: 2,410 across business units.",
                "speaker": "attachment:sherpa_weekly_trend.png",
                "source_type": "ATTACHMENT",
                "locator": "attachment:sherpa_weekly_trend.png",
            },
        },
    }

    metadata: Dict[str, Any] = {
        "meeting_id": "mtg-sherpa-10-13-2026",
        "attachment_prompt_block": "Slide 1 (sherpa_weekly_trend.png): Project Beacon Weekly Active Users: 2,410 across business units.",
        "hitl_role_resolutions": {
            "Amit Ayer": {
                "person_name": "Amit Ayer",
                "resolved_role": "Director",
                "organization": "PartnerAdvisory",
                "provenance": "HUMAN_OPERATOR",
                "is_human_confirmed": True,
                "resolution_id": "res-amit-01",
                "predicate": "HAS_ROLE",
            }
        },
    }

    # 1. Initial GS10 enrichment populates nested sig["evidence"] with ev-01
    _enrich_gs10_traits(projection, metadata)
    assert projection["graph_mutations"]["relationship_signals"][0]["evidence"]["statement_id"] == "stmt-01"

    # 2. Reconcile speaker roles: must reuse person:amit-ayer without creating duplicate person:amit_ayer signals
    reconciled = reconcile_speaker_role_resolutions(projection, metadata)
    rels = reconciled["graph_mutations"]["relationship_signals"]
    assert len(rels) == 2
    assert all(r["from_id"] == "person:amit-ayer" for r in rels)
    ev_hitl = reconciled["evidence_index"]["ev_hitl_role_amit_ayer"]
    assert "PartnerAdvisory" in ev_hitl["text"]

    # 3. Graph healer aligns MEMBER_OF evidence to ev_hitl_role_amit_ayer AND syncs nested sig["evidence"]
    changes: list[str] = []
    _heal_signal_evidence_and_qualifiers(
        reconciled,
        rels,
        changes,
    )
    member_sig = next(r for r in rels if r["relationship"] == "MEMBER_OF")
    assert member_sig["evidence_ids"] == ["ev_hitl_role_amit_ayer"]
    assert member_sig["evidence"]["statement_id"] == "stmt_hitl_role_amit_ayer"
    assert "PartnerAdvisory" in member_sig["evidence"]["direct_quote"]

    # 4. Quote pointer resolver preserves collateral/attachment grounding
    transcript_text = "[00:01:12] Speaker_Beta: Wait, let us look at the adoption numbers for Project Beacon."
    resolved = resolve_evidence_pointers(
        reconciled,
        source_text=transcript_text,
        collateral_text=metadata["attachment_prompt_block"],
    )
    assert resolved["evidence_index"]["ev-16"]["grounding_status"] == "GROUNDED"
    assert resolved["evidence_index"]["ev_hitl_role_amit_ayer"]["grounding_status"] == "GROUNDED"

    # 5. Gate 3 reference integrity passes with zero dangling references or statement_id mismatches
    ok, ref_errors = check_graph_reference_integrity(resolved)
    assert ok, f"Unexpected Gate 3 reference errors: {ref_errors}"

    # 6. Judge user content includes <uploaded_meeting_collateral>
    user_content = build_graph_judge_user_content(
        reference_text=transcript_text,
        candidate_text="{}",
        metadata=metadata,
        gov_ctx="<governance_context/>",
    )
    assert "<uploaded_meeting_collateral>" in user_content
    assert "sherpa_weekly_trend.png" in user_content

    # 7. Deterministic Judge quote check & scoring preserves 89/100 HITL_REQUIRED and D1=15/15
    q_ok, q_defects, _ = check_quote_and_evidence_resolution(
        resolved,
        transcript_text,
        collateral_text=metadata["attachment_prompt_block"],
    )
    assert q_ok, f"Unexpected quote defects: {q_defects}"

    raw_eval: Dict[str, Any] = {
        "dimension_1_strategic_edge": 15,
        "dimension_2_knowledge_layer_density": 13,
        "dimension_3_semantic_atomicity": 17,
        "dimension_4_predicate_specificity": 15,
        "dimension_5_exclusion_of_action_items": 15,
        "dimension_6_evidence_grounding_and_schema": 14,
        "composite_score": 89,
        "verdict": "HITL_REQUIRED",
        "defect_codes": [],
        "forensic_rationale": "All dimension floors met.",
    }
    scored = apply_deterministic_graph_scoring(
        copy.deepcopy(raw_eval),
        resolved,
        transcript_text,
        archetype=MeetingArchetype.TACTICAL_ALIGNMENT,
        collateral_text=metadata["attachment_prompt_block"],
    )
    assert scored["composite_score"] == 89
    assert scored["verdict"] == "HITL_REQUIRED"


def test_roster_prompt_enforces_atomic_role_description() -> None:
    """Verify Stage 03 roster prompt instructs atomic role description without bundling meeting actions."""
    static_blocks: list[str] = []
    dynamic_blocks: list[str] = []
    _append_roster_block(
        static_blocks,
        dynamic_blocks,
        {"attendees": [{"name": "Amit Ayer", "role": "Director", "org": "PartnerAdvisory"}]},
    )
    assert static_blocks
    assert "atomic 'description' stating ONLY their verified organizational title" in static_blocks[0]
    assert "contributions in the meeting" not in static_blocks[0]
