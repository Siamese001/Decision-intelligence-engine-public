"""tests/test_wave3_graph_alignment_e2e.py

End-to-End verification of Wave 3 Stage 03 Graph Projection Alignment:
1. 'dependency' is recognized in Output3Config.allowed_statement_kinds.
2. 'CONTEXT_FOR' is accepted in graph_checks and semantic_linters without generic predicate penalty.
3. 8 canonical entity prefixes pass Gate 3 _check_prefixes.
4. _ground_single_evidence synchronizes exact_text and offsets to preserve X3 quote pointer integrity.
"""
from __future__ import annotations

import pytest
from dual_pipeline.prompts.config import Output3Config
from dual_pipeline.llm_judge_core.graph_checks import (
    GENERIC_PREDICATES as JUDGE_GENERIC_PREDICATES,
    compute_predicate_specificity_score,
)
from dual_pipeline.transcript_qa_core.semantic_linters import (
    GENERIC_PREDICATES as LINTER_GENERIC_PREDICATES,
    lint_predicate_domain,
)
from dual_pipeline.transcript_qa_core.gate3 import (
    ALLOWED_PREFIXES,
    _check_prefixes,
    _ground_single_evidence,
    _normalize_grounding,
)
from dual_pipeline.prompts.contracts.versioned_judge_contracts import MeetingXExitContract


def test_dependency_in_output3_config():
    """Verify 'dependency' is an allowed statement kind in Output3Config."""
    cfg = Output3Config()
    assert "dependency" in cfg.allowed_statement_kinds


def test_context_for_not_penalized_as_generic():
    """Verify CONTEXT_FOR is not classified as a generic predicate in judge or linters."""
    assert "CONTEXT_FOR" not in JUDGE_GENERIC_PREDICATES
    assert "CONTEXT_FOR" not in LINTER_GENERIC_PREDICATES

    # Check predicate scoring
    f3_payload = {
        "graph_mutations": {
            "relationship_signals": [
                {
                    "from_id": "system:lens",
                    "to_id": "decision:model-selection",
                    "relationship": "CONTEXT_FOR",
                    "evidence_ids": ["ev-1"],
                }
            ]
        }
    }
    score, generic_found, defect_codes = compute_predicate_specificity_score(f3_payload)
    assert not any("CONTEXT_FOR" in d for d in generic_found)
    assert score == 15


def test_canonical_prefixes_in_gate3():
    """Verify that Gate 3 enforces the 8 canonical entity prefixes."""
    expected_prefixes = (
        "person:", "org:", "system:", "workstream:", "project:", "metric:", "role:", "policy:",
    )
    for p in expected_prefixes:
        assert p in ALLOWED_PREFIXES

    valid_entities = [
        {"id": f"{p}example-node", "name": "Example"}
        for p in expected_prefixes
    ]
    passed, failures = _check_prefixes(valid_entities)
    assert passed is True
    assert len(failures) == 0


def test_ground_single_evidence_syncs_offsets_and_passes_x3():
    """Verify _ground_single_evidence updates exact_text, offsets, and quote_pointer, passing X3."""
    f1_text = "Mitch Garstka confirmed that the model agreement threshold is 90% across all runs."
    norm_f1 = _normalize_grounding(f1_text)
    f1_sentences = [f1_text]

    # Evidence with a slightly inexact sentence that will be matched
    ev = {
        "exact_text": "confirmed that the model agreement threshold is 90% across all runs.",
        "text": "confirmed that the model agreement threshold is 90% across all runs.",
        "start_offset": 0,
        "end_offset": 50,
        "quote_pointer": [0, 50],
        "locator": {"kind": "transcript", "quote_pointer": [0, 50]},
    }

    grounded = _ground_single_evidence(ev, norm_f1, f1_sentences, f1_text)
    assert grounded is True

    # Verify offsets were synced to exact match in f1_text
    expected_slice = ev["exact_text"]
    assert expected_slice in f1_text
    start = ev["start_offset"]
    end = ev["end_offset"]
    assert (end - start) == len(expected_slice)
    assert f1_text[start:end] == expected_slice
    assert ev["quote_pointer"] == [start, end]
    assert ev["locator"]["quote_pointer"] == [start, end]

    # Validate cross-output coherence with MeetingXExitContract
    f3_payload = {
        "evidence_index": {
            "ev-01": ev,
        }
    }
    passed_x3, errors_x3 = MeetingXExitContract.validate_x3_cross_output_coherence(
        f1_text=f1_text,
        f2_text="",
        f3_data=f3_payload,
    )
    assert passed_x3 is True, f"X3 failed with errors: {errors_x3}"
