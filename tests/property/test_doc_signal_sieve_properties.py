"""Wave 4 Hypothesis property-based tests: idempotency, zero Q1-Q7 leakage, and canonical ID convergence."""

from __future__ import annotations

import copy
from hypothesis import given, settings, strategies as st

from dual_pipeline.document_pipeline_core.entity_canonicalizer import (
    CANONICAL_PERSON_ALIASES,
    canonicalize_projection_entities,
    resolve_canonical_person,
)
from dual_pipeline.document_pipeline_core.unit_decomposition import (
    QUARANTINED_UNIT_CLASSES,
    classify_unit_sieve,
)


@settings(max_examples=50, deadline=None)
@given(
    alias_key=st.sampled_from(sorted(CANONICAL_PERSON_ALIASES.keys())),
    upper_case=st.booleans(),
    extra_spaces=st.integers(min_value=1, max_value=3),
)
def test_property_canonical_person_convergence_and_idempotency(
    alias_key: str,
    upper_case: bool,
    extra_spaces: int,
) -> None:
    """Any casing or whitespace perturbation of the 8 NEA name-variant pairs converges to a single canonical ID."""
    expected_id, expected_name = CANONICAL_PERSON_ALIASES[alias_key]
    perturbed = (" " * extra_spaces).join(alias_key.split())
    if upper_case:
        perturbed = perturbed.upper()
    got_id, got_name = resolve_canonical_person(f"person:{perturbed}", perturbed)
    assert got_id == expected_id
    assert got_name == expected_name

    # Idempotency under projection canonicalization
    proj = {
        "graph_mutations": {
            "entity_upserts": [
                {"id": f"person:{alias_key.replace(' ', '-')}", "type": "person", "name": perturbed, "evidence_ids": ["ev-1"]},
                {"id": expected_id, "type": "person", "name": expected_name, "evidence_ids": ["ev-2"]},
            ],
            "relationship_signals": [
                {"from_id": f"person:{alias_key.replace(' ', '-')}", "to_id": "org:apex_global-energy", "relationship": "LEADS", "evidence_ids": ["ev-1"]},
                {"from_id": expected_id, "to_id": "org:apex_global-energy", "relationship": "LEADS", "evidence_ids": ["ev-2"]},
            ],
        }
    }
    once = canonicalize_projection_entities(copy.deepcopy(proj))
    twice = canonicalize_projection_entities(copy.deepcopy(once))
    assert once == twice
    assert len(once["graph_mutations"]["entity_upserts"]) == 1
    assert once["graph_mutations"]["entity_upserts"][0]["id"] == expected_id
    assert len(once["graph_mutations"]["relationship_signals"]) == 1


@settings(max_examples=40, deadline=None)
@given(
    quarantine_cue=st.sampled_from([
        "READ ME THEN DELETE [Account Name] Lorem ipsum",
        "ILLUSTRATIVE Maria Delgado EV553 demo scenario",
        "Clayton Act and Sherman Act antitrust compliance",
        "Let's Play a Game! Classroom Activity Discussion (Document) Roger Bannister Maya Angelou",
        "Break! Be back at 2:15 PM Small Group Dinners Matsu Wild Ginger Purple Il Bistro",
    ]),
    footer_year=st.sampled_from(["", "© 2026 PartnerAdvisory, Inc. All rights reserved.", "Confidential 2026"]),
)
def test_property_zero_q1_q7_quarantine_leakage(quarantine_cue: str, footer_year: str) -> None:
    """Any unit containing Q1-Q7 quarantine markers classifies into QUARANTINED_UNIT_CLASSES regardless of footer year."""
    content = f"{quarantine_cue}\n{footer_year}".strip()
    sieve_cls, legacy_mat, is_sub = classify_unit_sieve("", content, 0)
    assert sieve_cls in QUARANTINED_UNIT_CLASSES
    assert legacy_mat in ("QUARANTINED", "STRUCTURAL_DIVIDER")
    assert is_sub is False
