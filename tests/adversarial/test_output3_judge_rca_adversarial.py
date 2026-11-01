"""Adversarial and edge-case tests for Output 03 LLM-as-a-Judge RCA remediations."""
from __future__ import annotations

from typing import Any, Dict

from dual_pipeline.llm_judge_core.graph_checks import (
    apply_deterministic_graph_scoring,
    check_quote_and_evidence_resolution,
)
from dual_pipeline.prompts.contracts import MeetingArchetype
from dual_pipeline.transcript_qa_core.graph_reference_validator import check_graph_reference_integrity
from dual_pipeline.transcript_stages_core.quote_pointer_resolver import resolve_evidence_pointers
from dual_pipeline.transcript_stages_core.speaker_role_reconciler import reconcile_speaker_role_resolutions


def test_boundary_empty_collateral_and_empty_projection() -> None:
    """Category 1 (Boundary payload): Empty projection and empty collateral do not crash."""
    empty_proj: Dict[str, Any] = {"graph_mutations": {}, "evidence_index": {}}
    resolved = resolve_evidence_pointers(empty_proj, source_text="", collateral_text="")
    assert resolved["evidence_index"] == {}
    ok, errs = check_graph_reference_integrity(empty_proj)
    assert ok and errs == []


def test_malformed_nested_evidence_and_non_dict_entries() -> None:
    """Category 2 (Malformed input): Non-dict evidence fields and malformed locators handled safely."""
    malformed: Dict[str, Any] = {
        "graph_mutations": {
            "entity_upserts": [{"id": "person:amit-ayer", "name": "Amit Ayer"}],
            "relationship_signals": [
                {
                    "signal_id": "sig-bad",
                    "from_id": "person:amit-ayer",
                    "to_id": "person:amit-ayer",
                    "evidence_ids": ["ev-1"],
                    "evidence": "malformed-string-instead-of-dict",
                }
            ],
        },
        "evidence_index": {
            "ev-1": {
                "statement_id": "stmt-1",
                "text": "Unverified quote not in transcript",
                "locator": ["invalid-list-locator"],
            }
        },
    }
    resolved = resolve_evidence_pointers(malformed, source_text="Short transcript.", collateral_text=None)
    assert resolved["evidence_index"]["ev-1"]["grounding_status"] == "UNGROUNDED_HALLUCINATION"
    ok, errs = check_graph_reference_integrity(malformed)
    assert not ok
    assert any("SELF_REFERENTIAL_RELATIONSHIP" in e for e in errs)


def test_invariant_bypass_dangling_endpoint_and_nested_evidence_mismatch() -> None:
    """Category 5 (Invariant bypass attempt): Gate 3 catches dangling underscore from_id and stale nested statement_id."""
    corrupted: Dict[str, Any] = {
        "graph_mutations": {
            "entity_upserts": [
                {"id": "person:amit-ayer", "name": "Amit Ayer"},
                {"id": "org:partner_advisory", "name": "PartnerAdvisory"},
            ],
            "relationship_signals": [
                {
                    "signal_id": "sig_member_amit_ayer",
                    "from_id": "person:amit_ayer",
                    "to_id": "org:partner_advisory",
                    "relationship": "MEMBER_OF",
                    "evidence_ids": ["ev_hitl_role_amit_ayer"],
                    "evidence": {
                        "statement_id": "ev-01",
                        "direct_quote": "Stale quote from another speaker",
                    },
                }
            ],
        },
        "evidence_index": {
            "ev-01": {"statement_id": "ev-01", "text": "Stale quote from another speaker"},
            "ev_hitl_role_amit_ayer": {
                "statement_id": "stmt_hitl_role_amit_ayer",
                "text": "Human operator verified professional title 'Director' and organization 'PartnerAdvisory' for Amit Ayer.",
            },
        },
    }
    ok, errs = check_graph_reference_integrity(corrupted)
    assert not ok
    assert any("DANGLING_ENTITY_REFERENCE" in e and "person:amit_ayer" in e for e in errs)
    assert any("EVIDENCE_REFERENCE_MISMATCH" in e and "ev-01" in e for e in errs)


def test_exact_production_regression_project_beacon_89_hitl_and_idempotent_reingest() -> None:
    """Categories 6 & 7 (Rollback/idempotence & Exact production regression):
    1. Repeated reconciliation does not duplicate signals or overwrite hyphenated IDs.
    2. Floor failure below floor still fails closed even if composite score is 88.
    """
    proj: Dict[str, Any] = {
        "graph_mutations": {
            "entity_upserts": [
                {"id": "person:amit-ayer", "name": "Amit Ayer", "type": "person", "role": "Director"},
                {"id": "org:partner_advisory", "name": "PartnerAdvisory", "type": "org"},
            ],
            "relationship_signals": [
                {
                    "signal_id": "sig-m1",
                    "from_id": "person:amit-ayer",
                    "to_id": "org:partner_advisory",
                    "relationship": "MEMBER_OF",
                    "evidence_ids": ["ev-01"],
                }
            ],
        },
        "evidence_index": {},
    }
    meta = {
        "hitl_role_resolutions": {
            "Amit Ayer": {
                "person_name": "Amit Ayer",
                "resolved_role": "Director",
                "organization": "PartnerAdvisory",
                "provenance": "HUMAN_OPERATOR",
                "is_human_confirmed": True,
                "resolution_id": "res-1",
            }
        }
    }
    reconcile_speaker_role_resolutions(proj, meta)
    reconcile_speaker_role_resolutions(proj, meta)
    member_sigs = [
        r for r in proj["graph_mutations"]["relationship_signals"] if r.get("relationship") == "MEMBER_OF"
    ]
    assert len(member_sigs) == 1
    assert member_sigs[0]["from_id"] == "person:amit-ayer"

    # Floor failure must still FAIL even if score is in 85..89
    floor_fail_eval: Dict[str, Any] = {
        "dimension_1_strategic_edge": 15,
        "dimension_2_knowledge_layer_density": 15,
        "dimension_3_semantic_atomicity": 13,  # Below floor of 14
        "dimension_4_predicate_specificity": 15,
        "dimension_5_exclusion_of_action_items": 15,
        "dimension_6_evidence_grounding_and_schema": 15,
        "composite_score": 88,
        "verdict": "HITL_REQUIRED",
    }
    scored = apply_deterministic_graph_scoring(
        floor_fail_eval,
        {"graph_mutations": {}, "evidence_index": {}},
        "Transcript text",
        archetype=MeetingArchetype.TACTICAL_ALIGNMENT,
    )
    assert scored["verdict"] == "FAIL"

    # Genuine ungrounded quote (not in transcript and not in collateral) still fails check_quote_and_evidence_resolution
    bad_f3 = {
        "graph_mutations": {
            "entity_upserts": [{"id": "project:x", "evidence_ids": ["ev-hallucinated"]}],
        },
        "evidence_index": {
            "ev-hallucinated": {
                "statement_id": "stmt-h",
                "text": "Completely fabricated quote that appears nowhere in transcript or slides.",
                "speaker": "Speaker_Beta",
                "locator": "00:05:00",
            }
        },
    }
    q_ok, q_defects, _ = check_quote_and_evidence_resolution(
        bad_f3, "Actual spoken transcript words.", collateral_text="Actual slide text."
    )
    assert not q_ok
    assert q_defects
