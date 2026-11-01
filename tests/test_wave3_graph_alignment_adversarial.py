"""tests/test_wave3_graph_alignment_adversarial.py

Adversarial and edge-case verification for Wave 3 Graph Projection Alignment:
- Category 1: Boundary payload (empty action_updates passes cleanly, 0 mutations)
- Category 2: Malformed input (unsupported entity prefix in entity_upserts fails _check_prefixes)
- Category 3: Invariant bypass attempt (non-empty action_updates is rejected, RELATED_TO is penalized)
- Category 4: Exact regression (mismatched quote pointer offsets fail X3 cross-output coherence)
"""
from __future__ import annotations

import pytest
from dual_pipeline.transcript_qa_core.gate3 import _check_prefixes
from dual_pipeline.llm_judge_core.graph_checks import (
    compute_action_exclusion_score,
    compute_predicate_specificity_score,
)
from dual_pipeline.prompts.contracts.versioned_judge_contracts import MeetingXExitContract


# Category 1: Boundary payload
def test_boundary_empty_action_updates_passes_dim5():
    """Verify empty action_updates array passes Dimension 5 with full 15 points."""
    f3_payload = {
        "graph_mutations": {
            "action_updates": []
        }
    }
    score, defects = compute_action_exclusion_score(f3_payload)
    assert score == 15
    assert len(defects) == 0


# Category 2: Malformed input
def test_malformed_unsupported_prefix_in_entity_upserts_fails():
    """Verify entity IDs with invalid/unsupported prefixes fail _check_prefixes."""
    invalid_entities = [
        {"id": "unknown:bad-node", "name": "Bad Node"},
        {"id": "random_slug", "name": "No Prefix"},
    ]
    passed, failures = _check_prefixes(invalid_entities)
    assert passed is False
    assert len(failures) > 0
    assert "without allowed prefix" in failures[0]


# Category 3: Invariant bypass attempt
def test_invariant_bypass_non_empty_action_updates_fails_dim5():
    """Verify promoting tactical action items into action_updates fails Dimension 5 with 0/15."""
    f3_payload = {
        "graph_mutations": {
            "action_updates": [
                {
                    "action_id": "action:sprint-task-1",
                    "description": "Deploy test server",
                    "owner_id": "person:mitch-garstka",
                }
            ]
        }
    }
    score, defects = compute_action_exclusion_score(f3_payload)
    assert score == 0
    assert any("tactical action items" in d for d in defects)


def test_invariant_bypass_generic_predicate_related_to_penalized():
    """Verify using generic predicate RELATED_TO still receives penalty."""
    f3_payload = {
        "graph_mutations": {
            "relationship_signals": [
                {
                    "from_id": "system:lens",
                    "to_id": "system:dual-model",
                    "relationship": "RELATED_TO",
                    "evidence_ids": ["ev-1"],
                }
            ]
        }
    }
    score, generic_found, defect_codes = compute_predicate_specificity_score(f3_payload)
    assert score < 15
    assert any("RELATED_TO" in d for d in generic_found)


# Category 4: Exact production regression
def test_regression_offset_length_mismatch_fails_x3():
    """Verify X3 fails when quote pointer offset span length does not match len(exact_text)."""
    f1_text = "The system requirements were reviewed and ratified."
    f3_payload = {
        "evidence_index": {
            "ev-01": {
                "exact_text": "reviewed and ratified.",
                "quote_pointer": [0, 10],  # 10 != len("reviewed and ratified.")
            }
        }
    }
    passed, errors = MeetingXExitContract.validate_x3_cross_output_coherence(
        f1_text=f1_text,
        f2_text="",
        f3_data=f3_payload,
    )
    assert passed is False
    assert any("span length" in e for e in errors)
