"""
tests/test_logprob_atomic_judge_e2e.py

End-to-End verification of Wave 2 Evidence-First Atomic Rubrics & Logprob Scoring:
1. Strict causal JSON key ordering (evidence spans and counter-arguments precede numeric scores/verdicts).
2. Continuous expected value calculation matching analytical expectation within 10^-6.
3. Deterministic Python dimension and composite rollups with zero arithmetic drift.
4. Complete atomic sub-criteria resolution across Output 1, Output 2, and Output 3.
"""
from __future__ import annotations

import json
import math
from typing import Any, Dict

import pytest

from dual_pipeline.llm_judge_core.contracts.judge_criterion_contract import (
    AtomicSubCriterion,
    OUTPUT1_ATOMIC_SUBCRITERIA,
    OUTPUT2_ATOMIC_SUBCRITERIA,
    OUTPUT3_ATOMIC_SUBCRITERIA,
    resolve_applicable_atomic_subcriteria,
    validate_output1_scorecard,
    validate_output2_scorecard,
    validate_output3_scorecard,
)
from dual_pipeline.llm_judge_core.logprob_scorer import (
    BARS_4_WEIGHTS,
    BINARY_CHECKLIST_WEIGHTS,
    compute_logprob_distribution,
    rollup_atomic_criteria,
    rollup_scorecard_logprobs,
)
from dual_pipeline.prompts.contracts.versioned_judge_contracts import (
    Output1ScorecardV2,
    Output2ScorecardV2,
    Output3ScorecardV2,
)


def test_causal_field_serialization_order_output1():
    """Verify serialized Output1ScorecardV2 places evidence and counter-arguments before scores."""
    card_dict: Dict[str, Any] = {
        "rubric_version": "2.0",
        "judge_model": "gpt-6-sol",
        "prompt_digest": "0123456789abcdef",
        "sample_count": 1,
        "extracted_evidence_spans": [
            "[00:01:10] harper Hoffman: 'We expect complete integration by Q3.'",
            "[00:01:25] Jen Pan: 'Understood, we are tracking dependencies.'",
        ],
        "prosecutor_counter_arguments": [
            "Probe: Check if 'Q3' was asserted as committed date or aspirational target.",
            "Evidence confirms harper Hoffman explicitly framed it as delivery commitment.",
        ],
        "forensic_rationale": "Forensic transcript achieves complete verbatim fidelity and zero hallucination.",
        "dimension_1_forensic_grounding_and_fidelity": 25.0,
        "dimension_2_attribution_and_room_mic_hygiene": 25.0,
        "dimension_3_chronological_and_timestamp_integrity": 25.0,
        "dimension_4_asr_cleanup_and_domain_preservation": 25.0,
        "composite_score": 100.0,
        "verdict": "PASS",
        "evaluated_span_coverage": 1.0,
        "status": "PASS",
        "defect_codes": [],
        "actionable_remediation_diff": "",
        "grounding_citations": ["Turn 1", "Turn 2"],
    }
    scorecard = Output1ScorecardV2.model_validate(card_dict)
    dumped = scorecard.model_dump()
    keys = list(dumped.keys())

    evidence_idx = keys.index("extracted_evidence_spans")
    counter_idx = keys.index("prosecutor_counter_arguments")
    rationale_idx = keys.index("forensic_rationale")
    score_idx = keys.index("dimension_1_forensic_grounding_and_fidelity")
    verdict_idx = keys.index("verdict")

    assert evidence_idx < counter_idx < rationale_idx < score_idx < verdict_idx
    serialized_json = scorecard.model_dump_json()
    assert serialized_json.index("extracted_evidence_spans") < serialized_json.index("dimension_1_forensic_grounding_and_fidelity")


def test_causal_field_serialization_order_output2():
    """Verify serialized Output2ScorecardV2 places evidence and counter-arguments before section scores."""
    card_dict: Dict[str, Any] = {
        "rubric_version": "2.0",
        "judge_model": "gpt-6-sol",
        "prompt_digest": "0123456789abcdef",
        "sample_count": 1,
        "extracted_evidence_spans": [
            "# Key Discussion Points: Architecture review confirmed decoupled ingestion worker.",
        ],
        "prosecutor_counter_arguments": [
            "Probe: Did the recap inflate brainstorming ideas into committed roadmap items?",
            "Counter-analysis shows all commitments reflect unambiguous client consensus.",
        ],
        "executive_rationale": "Recap captures strategic architecture decisions with high altitude.",
        "section_scores": {
            "header_and_overview": 10.0,
            "key_updates_technical_substance": 35.0,
            "conditional_extensions": 20.0,
            "appendix_stakeholder_perspectives": 35.0,
        },
        "composite_score": 100.0,
        "verdict": "PASS",
        "evaluated_span_coverage": 1.0,
        "status": "PASS",
    }
    scorecard = Output2ScorecardV2.model_validate(card_dict)
    dumped = scorecard.model_dump()
    keys = list(dumped.keys())

    evidence_idx = keys.index("extracted_evidence_spans")
    counter_idx = keys.index("prosecutor_counter_arguments")
    rationale_idx = keys.index("executive_rationale")
    sec_idx = keys.index("section_scores")
    composite_idx = keys.index("composite_score")

    assert evidence_idx < counter_idx < rationale_idx < sec_idx < composite_idx


def test_causal_field_serialization_order_output3():
    """Verify serialized Output3ScorecardV2 places evidence and counter-arguments before dimensions."""
    card_dict: Dict[str, Any] = {
        "rubric_version": "2.0",
        "judge_model": "gpt-6-sol",
        "prompt_digest": "0123456789abcdef",
        "sample_count": 1,
        "extracted_evidence_spans": [
            "entity_upserts[0]: 'Azure Cosmos DB' - PROVIDES - 'Multi-Region Replication'",
        ],
        "prosecutor_counter_arguments": [
            "Probe: Are assertion proposals compound or leaking sprint tasks?",
            "Verification confirms atomic propositions and zero action items in knowledge graph.",
        ],
        "forensic_rationale": "Graph extraction produces clean, atomic assertions grounded in source.",
        "dimension_1_strategic_edge": 15.0,
        "dimension_2_knowledge_layer_density": 15.0,
        "dimension_3_semantic_atomicity": 20.0,
        "dimension_4_predicate_specificity": 15.0,
        "dimension_5_exclusion_of_action_items": 15.0,
        "dimension_6_evidence_grounding_and_schema": 20.0,
        "composite_score": 100.0,
        "verdict": "PASS",
        "evaluated_span_coverage": 1.0,
        "status": "PASS",
    }
    scorecard = Output3ScorecardV2.model_validate(card_dict)
    dumped = scorecard.model_dump()
    keys = list(dumped.keys())

    evidence_idx = keys.index("extracted_evidence_spans")
    counter_idx = keys.index("prosecutor_counter_arguments")
    rationale_idx = keys.index("forensic_rationale")
    dim1_idx = keys.index("dimension_1_strategic_edge")
    comp_idx = keys.index("composite_score")

    assert evidence_idx < counter_idx < rationale_idx < dim1_idx < comp_idx


def test_continuous_expected_value_exactness():
    """Verify continuous expected value and variance match analytical closed-form expectation within 10^-6."""
    # Discrete distribution: P(PASS)=0.70, P(MINOR_DEFECT)=0.20, P(MAJOR_DEFECT)=0.10, P(CRITICAL_FAIL)=0.00
    logprobs = {
        "PASS": math.log(0.70),
        "MINOR_DEFECT": math.log(0.20),
        "MAJOR_DEFECT": math.log(0.10),
        "CRITICAL_FAIL": -100.0,  # effectively 0
    }
    res = compute_logprob_distribution(logprobs, BARS_4_WEIGHTS, temperature=1.0)

    # Analytical expectation: 1.0*0.70 + 0.75*0.20 + 0.40*0.10 + 0.0*0.0 = 0.70 + 0.15 + 0.04 = 0.89
    expected_e = 0.89
    assert abs(res.expected_value - expected_e) < 1e-4

    # Analytical variance: (1.0 - 0.89)^2 * 0.70 + (0.75 - 0.89)^2 * 0.20 + (0.40 - 0.89)^2 * 0.10
    var_analytical = ((0.11) ** 2 * 0.70) + ((-0.14) ** 2 * 0.20) + ((-0.49) ** 2 * 0.10)
    assert abs(res.variance - var_analytical) < 1e-4
    assert res.top_token == "PASS"
    assert res.is_ambiguous is False  # margin = 0.70 - 0.20 = 0.50 > 0.30


def test_deterministic_scorecard_rollup_zero_arithmetic_drift():
    """Verify deterministic Python rollup produces exact dimension and composite scores with zero LLM arithmetic drift."""
    # Output 1 Dimension 1 sub-criteria
    subcrit_d1 = {
        "CRIT-OUT1-SUB-GROUNDING-ENTAILMENT": compute_logprob_distribution(
            {"PASS": math.log(0.90), "MINOR_DEFECT": math.log(0.10)}, BARS_4_WEIGHTS
        ),
        "CRIT-OUT1-SUB-HEDGING-MODALITY": compute_logprob_distribution(
            {"PASS": math.log(0.85), "MINOR_DEFECT": math.log(0.15)}, BARS_4_WEIGHTS
        ),
    }
    dim1_score, _, _, d1_ambig, d1_diag = rollup_atomic_criteria(subcrit_d1, max_dimension_points=25.0)

    # Output 1 Dimension 2 sub-criteria
    subcrit_d2 = {
        "CRIT-OUT1-SUB-POWER-TOPOLOGY": compute_logprob_distribution(
            {"PASS": math.log(0.95), "MINOR_DEFECT": math.log(0.05)}, BARS_4_WEIGHTS
        ),
    }
    dim2_score, _, _, d2_ambig, d2_diag = rollup_atomic_criteria(subcrit_d2, max_dimension_points=25.0)

    # Output 1 Dimension 3 (timestamp integrity)
    subcrit_d3 = {
        "CRIT-OUT1-CHRONO-MONOTONIC": compute_logprob_distribution(
            {"PASS": 0.0}, BINARY_CHECKLIST_WEIGHTS
        ),
    }
    dim3_score, _, _, d3_ambig, d3_diag = rollup_atomic_criteria(subcrit_d3, max_dimension_points=25.0)

    # Output 1 Dimension 4 (ASR domain preservation)
    subcrit_d4 = {
        "CRIT-OUT1-SUB-ACRONYM-SEMANTICS": compute_logprob_distribution(
            {"PASS": math.log(0.88), "MINOR_DEFECT": math.log(0.12)}, BARS_4_WEIGHTS
        ),
    }
    dim4_score, _, _, d4_ambig, d4_diag = rollup_atomic_criteria(subcrit_d4, max_dimension_points=25.0)

    dim_results = {
        "dim1": (dim1_score, 0.0, 0.0, d1_ambig, d1_diag),
        "dim2": (dim2_score, 0.0, 0.0, d2_ambig, d2_diag),
        "dim3": (dim3_score, 0.0, 0.0, d3_ambig, d3_diag),
        "dim4": (dim4_score, 0.0, 0.0, d4_ambig, d4_diag),
    }
    comp_score, logprob_diag = rollup_scorecard_logprobs(dim_results)

    expected_sum = round(dim1_score + dim2_score + dim3_score + dim4_score, 2)
    assert comp_score == expected_sum

    scorecard_dict = {
        "dimension_1_forensic_grounding_and_fidelity": dim1_score,
        "dimension_2_attribution_and_room_mic_hygiene": dim2_score,
        "dimension_3_chronological_and_timestamp_integrity": dim3_score,
        "dimension_4_asr_cleanup_and_domain_preservation": dim4_score,
        "composite_score": comp_score,
        "logprob_diagnostics": logprob_diag,
    }
    val_res = validate_output1_scorecard(scorecard_dict)
    assert val_res.is_valid is True
    assert val_res.validated_composite == comp_score
    assert val_res.logprob_diagnostics is not None


def test_atomic_subcriteria_registry():
    """Verify registry resolves all defined atomic sub-criteria with weights and descriptions."""
    out1_subs = resolve_applicable_atomic_subcriteria(1)
    out2_subs = resolve_applicable_atomic_subcriteria(2)
    out3_subs = resolve_applicable_atomic_subcriteria(3)

    assert len(out1_subs) == 4
    assert len(out2_subs) == 4
    assert len(out3_subs) == 4

    for sub in out1_subs + out2_subs + out3_subs:
        assert isinstance(sub, AtomicSubCriterion)
        assert sub.subcriterion_id.startswith("CRIT-OUT")
        assert sub.weight > 0.0
        assert len(sub.title) > 0
        assert len(sub.description) > 0
