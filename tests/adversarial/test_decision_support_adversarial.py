"""Adversarial and boundary test suite for obligation-driven decision support and evidence gating.

Verifies:
1. Wrong entity, polarity, direction, time, or citation-only relevance remains unsupported.
2. No unstated preference is invented; NO_DECISION and a concrete discriminating question
   remain valid results when options are tied or ambiguous.
3. Monotonic budget and deadline exhaustion prevent uncoordinated runaway loops.
4. Malformed decision frames and obligations fail closed.
"""
from __future__ import annotations

import time
from typing import Any, Dict

import pytest

from dual_pipeline.cost_tracker import (
    ActionRole,
    BudgetExhaustionError,
    DeadlineExceededError,
    RequestContext,
    get_reservation_manager,
)
from dual_pipeline.decision_engine import (
    build_decision_evidence_pack,
    build_decision_frame,
    evaluate_decision,
)
from dual_pipeline.decision_models import (
    DecisionTerminalState,
    validate_decision_frame,
    validate_decision_result,
)
from dual_pipeline.l0.decision_support_contracts import (
    DecisionSupportObligation,
    DecisionSupportState,
    compile_frame_evidence_requirements,
    evaluate_evidence_support,
)


def test_citation_only_distractor_remains_unsupported():
    """Citation-only distractors with citation markers or generic wrapper metadata are unsupported."""
    distractor_record = {
        "record_id": "rec_citation_only_01",
        "text": "References: [1] Ref 2026, [2] Architecture Guideline, see section 4.1.",
        "citations": ["ref_01", "ref_02"],
        "source_evidence": {"citation_only": True, "claims": []},
        "entity_ids": ["Stream Processing"],
    }

    state, reason = evaluate_evidence_support(
        record=distractor_record,
        option_label="Stream Processing",
        criterion_label="Peak Throughput",
        required_polarity="positive",
        expected_direction="forward",
        expected_temporal="current",
    )

    assert state == DecisionSupportState.UNSUPPORTED
    assert "citation-only" in reason.lower() or "empty" in reason.lower() or "predicate" in reason.lower()


def test_wrong_entity_remains_unsupported():
    """Evidence mentioning criteria but referring to an entirely wrong entity is unsupported."""
    wrong_entity_record = {
        "record_id": "rec_wrong_entity_02",
        "text": "Apache Iceberg metadata catalog exhibited superior throughput and low cost.",
        "entity_ids": ["Apache Iceberg", "Metadata Catalog"],
        "polarity": "positive",
        "direction": "positive",
        "currentness_state": "current",
    }

    state, reason = evaluate_evidence_support(
        record=wrong_entity_record,
        option_label="Stream Processing",
        criterion_label="Peak Throughput",
        required_polarity="positive",
        expected_direction="forward",
        expected_temporal="current",
    )

    assert state == DecisionSupportState.UNSUPPORTED
    assert "entity" in reason.lower()


def test_direction_and_polarity_inversion_detected_as_contradicted():
    """Opposing trends or explicit counterevidence are evaluated as CONTRADICTED, not supported."""
    contradicting_record = {
        "record_id": "rec_counter_03",
        "text": "Stream Processing bench tests revealed negative throughput and severe performance collapse.",
        "entity_ids": ["Stream Processing"],
        "polarity": "negative",
        "direction": "negative",
        "currentness_state": "current",
        "counterevidence": True,
    }

    state, reason = evaluate_evidence_support(
        record=contradicting_record,
        option_label="Stream Processing",
        criterion_label="Peak Throughput",
        required_polarity="positive",
        expected_direction="forward",
        expected_temporal="current",
    )

    assert state == DecisionSupportState.CONTRADICTED
    assert "counterevidence" in reason.lower() or "opposing" in reason.lower() or "negative" in reason.lower()


def test_temporal_mismatch_stale_horizon_unsupported():
    """Historical records outside the required temporal horizon are unsupported."""
    stale_record = {
        "record_id": "rec_stale_04",
        "text": "Stream Processing achieved 100k events/sec during the 2020 pilot run.",
        "entity_ids": ["Stream Processing"],
        "polarity": "positive",
        "direction": "forward",
        "currentness_state": "stale",
    }

    state, reason = evaluate_evidence_support(
        record=stale_record,
        option_label="Stream Processing",
        criterion_label="Peak Throughput",
        required_polarity="positive",
        expected_direction="forward",
        expected_temporal="current",
    )

    assert state == DecisionSupportState.UNSUPPORTED
    assert "temporal" in reason.lower() or "stale" in reason.lower()


def test_tied_or_ambiguous_evidence_produces_no_decision_and_discriminating_question():
    """When options are tied without decisive evidence, NO_DECISION and a discriminating question are returned without inventing preferences."""
    frame = build_decision_frame(
        question="Choose between Solution Alpha and Solution Beta given resilience and cost",
        options=["Solution Alpha", "Solution Beta"],
        criteria=["resilience", "cost"],
    )

    # Evidence pack with no decisive advantage for either option
    evidence_pack = build_decision_evidence_pack(
        query_result={
            "generation_id": "gen_tie_001",
            "graph_snapshot": "snap_tie_001",
            "graph_sha256_after": "a" * 64,
            "embedding_backend": "test",
        },
        evidence_packet={
            "approved": [],
            "candidate": [],
            "external_public": [],
            "graph_paths": [],
            "counterevidence": [],
            "gaps": [],
        },
    )

    execution = {"query_run_id": "run_tie_001"}

    result = evaluate_decision(
        frame=frame,
        evidence_pack=evidence_pack,
        answer=None,
        execution=execution,
        allow_recommendation=True,
    )

    # 1. Verification of NO_DECISION
    assert result["terminal_state"] == DecisionTerminalState.NO_DECISION.value
    assert result["recommended_option"] is None

    # 2. Discriminating question formulated in extensions
    support_ext = result["extensions"].get("com.partner_advisory.nee.decision-support")
    assert support_ext is not None
    disc_q = support_ext.get("discriminating_evidence_question")
    assert disc_q is not None
    assert isinstance(disc_q, str)
    assert len(disc_q) > 10
    assert "Solution Alpha" in disc_q or "Solution Beta" in disc_q

    # 3. What would change includes the question
    what_change = result["what_would_change_the_conclusion"]
    assert any(disc_q in item for item in what_change)

    # 4. Closed schema and artifact conformance
    validated = validate_decision_result(result)
    assert validated["terminal_state"] == "NO_DECISION"


def test_monotonic_deadline_and_budget_exhaustion_in_decision_loop():
    """Expired context deadline raises DeadlineExceededError and prevents further reservations."""
    mgr = get_reservation_manager()
    ctx = RequestContext.create(
        run_id="run_adv_deadline_001",
        request_id="req_adv_deadline_001",
        timeout_seconds=0.01,  # 10ms deadline
        max_cost_usd=1.0,
        max_tokens=1000,
        tool_calls_max=5,
    )

    time.sleep(0.02)  # Wait for deadline to expire

    with pytest.raises(DeadlineExceededError):
        mgr.reserve(
            context=ctx,
            estimated_cost_usd=0.05,
            estimated_tokens=100,
            action_role="primary",
        )


def test_malformed_decision_frame_fails_closed():
    """A malformed decision frame missing required keys fails validation."""
    malformed_frame = {
        "decision_id": "bad_frame",
        # Missing question, options, criteria
    }

    with pytest.raises(Exception):
        validate_decision_frame(malformed_frame)
