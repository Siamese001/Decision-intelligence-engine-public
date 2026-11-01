"""
tests/test_output3_contract.py

Wave 1 verification suite for Output 03 Knowledge Density Floor Retirement in Document Mode
and High-Signal Graph Selection alignment.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from dual_pipeline.document_pipeline_core.quality import qualify_document_judge
from dual_pipeline.llm_judge_core.contracts.judge_criterion_contract import (
    validate_output3_scorecard,
)
from dual_pipeline.llm_judge_core.graph_checks import apply_deterministic_graph_scoring
from dual_pipeline.llm_judge_core.judge_clarification import (
    assign_clarified_graph_verdict,
)
from dual_pipeline.prompts.contracts import (
    OUTPUT2_CONTRACT,
    OUTPUT3_CONTRACT,
    MeetingArchetype,
    get_dimension_floors_for_output,
    is_document_pipeline_mode,
)
from dual_pipeline.prompts.contracts.versioned_judge_contracts import (
    validate_versioned_judge_scorecard,
)
from dual_pipeline.release_policy import validate_micro_judge_scorecard
from dual_pipeline.transcript_qa_core.signal_density import (
    compute_technical_signal_density,
)


def _make_doc_scorecard(dim2: int = 8, dim6: int = 19, verdict: str = "PASS") -> Dict[str, Any]:
    dims = {
        "dimension_1_strategic_edge": 15,
        "dimension_2_knowledge_layer_density": dim2,
        "dimension_3_semantic_atomicity": 20,
        "dimension_4_predicate_specificity": 15,
        "dimension_5_exclusion_of_action_items": 15,
        "dimension_6_evidence_grounding_and_schema": dim6,
    }
    comp = sum(dims.values())
    return {
        "rubric_version": "2.0",
        "judge_model": "test-judge-model",
        "effective_model": "test-judge-model",
        "effective_provider": "local",
        "prompt_digest": "doc-judge-digest-w1",
        "sample_count": 1,
        "is_fallback": False,
        "evaluated_span_coverage": 1.0,
        "meeting_archetype": "DOCUMENT_MODE",
        **dims,
        "composite_score": comp,
        "verdict": verdict,
        "status": verdict,
        "forensic_rationale": "High-signal document claims grounded verbatim in source.",
    }


def test_output3_document_mode_retires_dim2_volume_floor_while_preserving_meeting_floor() -> None:
    """Verify Output 03 retires Dim 2 volume floor in DOCUMENT_MODE while keeping meeting floors intact."""
    meeting_floors = OUTPUT3_CONTRACT.get_dimension_floors()
    assert meeting_floors["dimension_2_knowledge_layer_density"] == 11.0
    assert len(meeting_floors) == 6

    doc_floors = OUTPUT3_CONTRACT.get_dimension_floors(pipeline_mode="DOCUMENT_MODE")
    assert "dimension_2_knowledge_layer_density" not in doc_floors
    assert doc_floors["dimension_1_strategic_edge"] == 11.0
    assert doc_floors["dimension_3_semantic_atomicity"] == 14.0
    assert doc_floors["dimension_4_predicate_specificity"] == 11.0
    assert doc_floors["dimension_5_exclusion_of_action_items"] == 11.0
    assert doc_floors["dimension_6_evidence_grounding_and_schema"] == 14.0

    assert is_document_pipeline_mode("DOCUMENT_MODE") is True
    assert is_document_pipeline_mode("DOCUMENT") is True
    assert is_document_pipeline_mode("ARCHITECTURE_DEEP_DIVE") is False
    assert "dimension_2_knowledge_layer_density" not in get_dimension_floors_for_output(3, pipeline_mode="DOCUMENT_MODE")


def test_document_extractor_prompt_replaces_zero_tolerance_density_quota() -> None:
    """Verify 03_document_extractor.md uses High-Signal Graph Selection instead of Zero-Tolerance Density Standard."""
    repo_root = Path(__file__).resolve().parent.parent
    prompt_path = repo_root / "config" / "prompts" / "synthesis" / "stages" / "03_document_extractor.md"
    content = prompt_path.read_text(encoding="utf-8")
    assert "High-Signal Graph Selection Standard (No Entity-Volume Quotas)" in content
    assert "Mandatory Component Enumeration (Zero-Tolerance Density Standard)" not in content


def test_document_mode_scorecard_validation_passes_without_dim2_floor_rejection() -> None:
    """Grounded document projection with Dim 2 = 8 (composite 92) passes all Output 03 validators."""
    card = _make_doc_scorecard(dim2=8, dim6=19, verdict="PASS")

    # 1. release_policy.validate_micro_judge_scorecard
    ok_micro, errs_micro = validate_micro_judge_scorecard(card, output_num=3, pipeline_mode="DOCUMENT_MODE")
    assert ok_micro, f"Expected DOCUMENT_MODE micro-judge validation to pass, got: {errs_micro}"

    # 2. document_pipeline_core.quality.qualify_document_judge
    qualified, passed, score = qualify_document_judge(card)
    assert passed is True, f"Expected qualify_document_judge to pass, got failures: {qualified.get('quality_failures')}"
    assert score == 92
    assert qualified["verdict"] == "PASS"

    # 3. versioned_judge_contracts.validate_versioned_judge_scorecard
    versioned_card = {k: v for k, v in card.items() if k != "status"}
    ok_ver, errs_ver = validate_versioned_judge_scorecard(versioned_card, output_num=3, modality="DOCUMENT")
    assert ok_ver, f"Expected versioned DOCUMENT scorecard validation to pass, got: {errs_ver}"

    # 4. judge_criterion_contract.validate_output3_scorecard
    crit_res = validate_output3_scorecard(card)
    assert crit_res.is_valid, f"Expected criterion contract validation to pass, got: {crit_res.errors}"


def test_document_mode_still_enforces_grounding_and_schema_floors() -> None:
    """Document mode continues to fail closed when Dim 6 (grounding/schema) breaches its 14.0 floor."""
    bad_grounding_card = _make_doc_scorecard(dim2=15, dim6=12, verdict="PASS")
    ok_micro, errs_micro = validate_micro_judge_scorecard(
        bad_grounding_card, output_num=3, pipeline_mode="DOCUMENT_MODE"
    )
    assert not ok_micro
    assert any("dimension_6_evidence_grounding_and_schema" in e for e in errs_micro)

    _, passed, _ = qualify_document_judge(bad_grounding_card)
    assert not passed


def test_apply_deterministic_graph_scoring_and_clarification_in_document_mode() -> None:
    """Verify deterministic graph scoring and clarification verdict assignment in DOCUMENT_MODE."""
    source_text = "HR360 manages the business request and approval before SuccessFactors updates official records."
    f3_parsed = {
        "evidence_index": {
            "ev-01": {
                "exact_text": "HR360 manages the business request and approval before SuccessFactors updates official records.",
                "locator": "char:0-95",
            }
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "system:hr360", "name": "HR360", "evidence_ids": ["ev-01"]},
                {"id": "system:successfactors", "name": "SuccessFactors", "evidence_ids": ["ev-01"]},
            ],
            "relationship_signals": [
                {
                    "from_id": "system:hr360",
                    "relationship": "INTEGRATES_WITH",
                    "to_id": "system:successfactors",
                    "evidence_ids": ["ev-01"],
                }
            ],
            "action_updates": [],
        },
    }
    card = _make_doc_scorecard(dim2=9, dim6=18, verdict="PASS")
    scored = apply_deterministic_graph_scoring(
        dict(card),
        f3_parsed=f3_parsed,
        f1_text=source_text,
        archetype="DOCUMENT_MODE",
        pipeline_mode="DOCUMENT_MODE",
    )
    assert scored["verdict"] == "PASS"
    assert scored["composite_score"] == 92

    dim_keys = [d.key for d in OUTPUT3_CONTRACT.rubric_dimensions]
    floors = {k: int(v) for k, v in OUTPUT3_CONTRACT.get_dimension_floors().items()}
    clarified = assign_clarified_graph_verdict(dict(card), dim_keys, floors)
    assert clarified["verdict"] == "PASS"


def test_output2_retains_narrative_signal_density_checks() -> None:
    """Verify Output 02 narrative signal density gates remain active and unchanged."""
    assert OUTPUT2_CONTRACT.output_id == 2
    sample_recap = (
        "## 3. Key Updates & Technical Substance\n"
        "- **System**: `system:hr360` integrates with `system:tyk-gateway` under API SLA of 250ms (`[00:04:12]`).\n"
    )
    density_res = compute_technical_signal_density(
        sample_recap, {"archetype": MeetingArchetype.ARCHITECTURE_DEEP_DIVE.value}
    )
    assert hasattr(density_res, "tsdi_score")
    assert density_res.tsdi_score > 0.0
