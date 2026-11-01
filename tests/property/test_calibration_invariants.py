"""Property-based tests for decision calibration, rank invariance, and cache substitution under Decision H-8.

Verifies mathematical invariants:
1. Probability Boundedness: Calibrated confidence is strictly bounded in [0.0, 1.0].
2. Anti-Certainty on Singletons: Singleton candidate predictions cannot exceed decisive multi-candidate confidence.
3. Fail-Closed Error Bound: Zero autonomous decisions always produces upper_bound_error_95 == 1.0.
4. DecisionIntelligence Rank Invariance: Permuting evidence rows or mutating scores leaves extracted facts identical.
5. Cache Substitution Security: Invariant violations (entity, polarity, clearance, advisory, Merkle root) strictly fail closed.
"""
from __future__ import annotations

import random
from typing import List, Optional

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dual_pipeline.decision_engine_core.evidence_pack import build_decision_evidence_pack
from dual_pipeline.retrieval_release_core.decision_calibration import (
    CalibrationModelWeights,
    DecisionAction,
    OperatingThresholds,
    compute_autonomous_tradeoffs,
    make_calibrated_decision,
    predict_calibrated_confidence,
    verify_safe_cache_substitution,
)


@settings(max_examples=100, deadline=None)
@given(
    top_score=st.floats(min_value=-5.0, max_value=5.0, allow_nan=False),
    second_score=st.one_of(st.none(), st.floats(min_value=-5.0, max_value=5.0, allow_nan=False)),
    candidate_count=st.integers(min_value=0, max_value=100),
)
def test_property_calibrated_confidence_bounded_in_unit_interval(
    top_score: float, second_score: Optional[float], candidate_count: int
) -> None:
    """Property: Calibrated probability is strictly within [0.0, 1.0] under arbitrary score distributions."""
    weights = CalibrationModelWeights()
    prob = predict_calibrated_confidence(top_score, second_score, candidate_count, weights)
    assert 0.0 <= prob <= 1.0, f"Calibrated probability {prob} out of bounds"


@settings(max_examples=100, deadline=None)
@given(
    top_score=st.floats(min_value=0.1, max_value=1.0, allow_nan=False),
    margin=st.floats(min_value=0.05, max_value=0.50, allow_nan=False),
    candidate_count=st.integers(min_value=2, max_value=20),
)
def test_property_singleton_anti_certainty_invariant(
    top_score: float, margin: float, candidate_count: int
) -> None:
    """Property: A singleton candidate never manufactures higher confidence than a decisive multi-candidate choice."""
    weights = CalibrationModelWeights()
    second_score = max(0.0, top_score - margin)

    prob_singleton = predict_calibrated_confidence(top_score, None, 1, weights)
    prob_multi = predict_calibrated_confidence(top_score, second_score, candidate_count, weights)

    assert prob_singleton < prob_multi, (
        f"Singleton prob {prob_singleton} unexpectedly >= multi-candidate prob {prob_multi}"
    )


@settings(max_examples=100, deadline=None)
@given(
    n_abstain=st.integers(min_value=1, max_value=20),
    n_hitl=st.integers(min_value=0, max_value=20),
)
def test_property_fail_closed_error_bound_on_zero_autonomous(
    n_abstain: int, n_hitl: int
) -> None:
    """Property: When autonomous count is zero, upper bound error is strictly 1.0 (fail closed)."""
    weights = CalibrationModelWeights()
    thresholds = OperatingThresholds(tau_auto=0.99, tau_abstain=0.01)

    decisions = []
    labels = []
    supported = []

    for _ in range(n_abstain):
        dec = make_calibrated_decision(0.01, 0.00, 1, weights, thresholds)
        dec.action = DecisionAction.ABSTAIN
        decisions.append(dec)
        labels.append(1)
        supported.append(True)

    for _ in range(n_hitl):
        dec = make_calibrated_decision(0.50, 0.49, 10, weights, thresholds)
        dec.action = DecisionAction.HITL_REVIEW
        decisions.append(dec)
        labels.append(1)
        supported.append(True)

    tradeoffs = compute_autonomous_tradeoffs(decisions, labels, supported)
    assert tradeoffs["autonomous_count"] == 0
    assert tradeoffs["coverage"] == 0.0
    assert tradeoffs["upper_bound_error_95"] == 1.0, (
        f"Zero autonomous decisions must fail closed with error bound 1.0, got {tradeoffs['upper_bound_error_95']}"
    )


@settings(max_examples=50, deadline=None)
@given(
    num_records=st.integers(min_value=2, max_value=10),
)
def test_property_decision_intelligence_rank_and_score_invariance(num_records: int) -> None:
    """Property: Permuting evidence rows and mutating scores produces identical facts/risks/constraints."""
    sample_records = [
        {
            "record": {
                "record_id": f"rec_{i}",
                "title": f"Project Fact {i}",
                "retrieval_text": f"Fact details on component {i}.",
                "evidence_ids": [f"ev_{i}"],
                "record_type": "source_chunk",
                "currentness_state": "current",
            },
            "retrieval_metadata": {"answer_use": "factual_answer"},
            "fused_score": 0.015 + i * 0.001,
            "dense_score": 0.50 + i * 0.05,
        }
        for i in range(num_records)
    ]

    base_packet = {
        "approved": list(sample_records),
        "candidate": [],
        "external_public": [],
        "graph_paths": [],
        "counterevidence": [],
        "gaps": [],
    }
    query_result = {"generation_id": "gen_1"}

    base_pack = build_decision_evidence_pack(query_result=query_result, evidence_packet=base_packet)

    # Permute order and alter scores
    permuted_records = list(sample_records)
    random.shuffle(permuted_records)
    for r in permuted_records:
        r["fused_score"] = random.uniform(0.01, 0.99)
        r["dense_score"] = random.uniform(0.1, 0.9)

    permuted_packet = {
        "approved": permuted_records,
        "candidate": [],
        "external_public": [],
        "graph_paths": [],
        "counterevidence": [],
        "gaps": [],
    }
    permuted_pack = build_decision_evidence_pack(query_result=query_result, evidence_packet=permuted_packet)

    # Invariant: Extracted facts, risks, constraints sets are 100% identical regardless of ranking/scores
    base_fact_ids = {r["record_id"] for r in base_pack["facts"]}
    permuted_fact_ids = {r["record_id"] for r in permuted_pack["facts"]}
    assert base_fact_ids == permuted_fact_ids
    assert len(base_pack["risks"]) == len(permuted_pack["risks"])
    assert len(base_pack["constraints"]) == len(permuted_pack["constraints"])


@settings(max_examples=50, deadline=None)
@given(
    is_advisory=st.booleans(),
    merkle_diverged=st.booleans(),
    audience_expanded=st.booleans(),
)
def test_property_safe_cache_substitution_adversarial_guards(
    is_advisory: bool, merkle_diverged: bool, audience_expanded: bool
) -> None:
    """Property: Any advisory flag, Merkle root divergence, or audience expansion strictly blocks cache reuse."""
    source_merkle = "sha256:aaa"
    target_merkle = "sha256:bbb" if merkle_diverged else "sha256:aaa"
    source_aud = ["internal_consulting"]
    target_aud = ["internal_consulting", "external_public"] if audience_expanded else ["internal_consulting"]

    safe, rejections = verify_safe_cache_substitution(
        source_query="test query",
        target_query="test query",
        similarity_score=0.99,
        source_entity="SameEntity",
        target_entity="SameEntity",
        source_is_negated=False,
        target_is_negated=False,
        source_timestamp="2026-08-28T10:00:00Z",
        target_timestamp="2026-08-28T10:00:00Z",
        source_clearance="internal",
        target_clearance="internal",
        source_merkle_root=source_merkle,
        target_merkle_root=target_merkle,
        source_audiences=source_aud,
        target_audiences=target_aud,
        is_internal_advisory=is_advisory,
    )

    should_be_safe = (not is_advisory) and (not merkle_diverged) and (not audience_expanded)
    assert safe == should_be_safe
    if is_advisory:
        assert "INTERNAL_ADVISORY_QUARANTINE_BLOCKED" in rejections
    if merkle_diverged:
        assert "SUBGRAPH_MERKLE_ROOT_MISMATCH" in rejections
    if audience_expanded:
        assert "AUDIENCE_CLEARANCE_EXPANSION_BLOCKED" in rejections
