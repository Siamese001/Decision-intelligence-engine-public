"""
tests/test_copilot_composer_adversarial.py

Adversarial and Edge-Case Qualification Tests for Wave 6: Local Copilot Composer & Feedback Loop.
Covers mandatory edge-case categories:
1. Boundary Payload: Empty assertions, None/empty contexts, 0 max_words, malicious HTML markup targets.
2. Malformed Input & Invariant Bypass: Simulated hallucinations, ungrounded tokens, disabled feature flag.
3. Timeout Fail-Closed: Simulated and latency-budget timeouts strictly fail closed to silence (body="").
4. Learning Proposal Boundary & Clamp: Threshold upper/lower bounds, non-mutation auto_apply invariants.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List
import pytest

from dual_pipeline.answer_ledger_core.feedback import (
    synthesize_feedback_threshold_proposal,
)
from dual_pipeline.live_meeting_recorder_core.copilot_composer import (
    ComposerResult,
    LocalCopilotComposer,
    _validate_groundedness,
    _compute_relevance,
    _tokenize,
)


@pytest.fixture
def enabled_composer() -> LocalCopilotComposer:
    return LocalCopilotComposer(enabled=True, timeout_seconds=0.450)


@pytest.fixture
def sample_assertions() -> List[Dict[str, Any]]:
    return [
        {
            "assertion_id": "ast_scale_1",
            "subject": "Storage Cluster",
            "predicate": "requires_sharding",
            "object_literal": "storage cluster capacity requires 3-way sharding",
            "claim_text": "storage cluster capacity requires 3-way sharding",
            "insight_type": "risk",
            "confidence": 0.92,
        },
        {
            "assertion_id": "ast_slo_1",
            "subject": "API Gateway",
            "predicate": "baseline_target",
            "object_literal": "API latency target is 99.9% under 50ms",
            "claim_text": "API latency target is 99.9% under 50ms",
            "insight_type": "prior_decision",
            "confidence": 0.95,
        },
    ]


# ==============================================================================
# Category 1: Boundary Payloads
# ==============================================================================


def test_composer_empty_assertions_returns_redundant_or_low_novelty(enabled_composer: LocalCopilotComposer):
    """Empty assertion list must fail closed to empty body and appropriate failure reason."""
    result = enabled_composer.compose(
        assertions=[],
        dialogue_context="Discussion about database storage cluster",
    )
    assert isinstance(result, ComposerResult)
    assert result.body == ""
    assert result.failure_reason == "REDUNDANT_OR_LOW_NOVELTY"
    assert result.rendered_count == 0


def test_composer_none_and_empty_dialogue_context(
    enabled_composer: LocalCopilotComposer,
    sample_assertions: List[Dict[str, Any]],
):
    """None or empty string dialogue context handles safely without throwing exceptions."""
    for ctx in [None, "", "   "]:
        result = enabled_composer.compose(
            assertions=sample_assertions,
            dialogue_context=ctx,
        )
        assert isinstance(result, ComposerResult)
        assert result.is_enabled is True
        # If context is empty, select_best_insight may find no matching keywords and return empty body
        if result.body != "":
            assert len(result.body.split()) <= 40
            assert result.groundedness >= 0.98


def test_composer_zero_or_severely_constrained_max_words(
    enabled_composer: LocalCopilotComposer,
    sample_assertions: List[Dict[str, Any]],
):
    """max_words=0 or tiny budgets must fail closed without emitting truncated or malformed sentences."""
    result = enabled_composer.compose(
        assertions=sample_assertions,
        dialogue_context="storage cluster capacity requires sharding",
        max_words=3,
    )
    assert result.body == ""
    assert result.failure_reason in ("WORD_BUDGET_EXHAUSTED", "REDUNDANT_OR_LOW_NOVELTY")


def test_composer_markup_injection_in_target_and_speaker(
    enabled_composer: LocalCopilotComposer,
    sample_assertions: List[Dict[str, Any]],
):
    """HTML or script tags in target and speaker must be sanitized and never spoken."""
    malicious_target = "<script>alert('xss')</script>Alice"
    malicious_utterance = {"speaker": "<b>Bob<iframe src='malicious.site'></b>"}

    result = enabled_composer.compose(
        assertions=sample_assertions,
        dialogue_context="storage cluster capacity sharding",
        target=malicious_target,
        selected_utterance=malicious_utterance,
    )

    if result.body:
        assert "<script>" not in result.body
        assert "<iframe>" not in result.body
        assert "<b>" not in result.body
        assert "<" not in result.body
        assert ">" not in result.body


# ==============================================================================
# Category 2: Malformed Input & Invariant Bypass Attempts
# ==============================================================================


def test_composer_dormant_when_flag_disabled(sample_assertions: List[Dict[str, Any]]):
    """When enabled=False (default), composer must immediately return empty body with COMPOSER_DISABLED."""
    disabled_composer = LocalCopilotComposer(enabled=False)
    assert disabled_composer.enabled is False

    result = disabled_composer.compose(
        assertions=sample_assertions,
        dialogue_context="storage cluster capacity sharding",
    )
    assert result.body == ""
    assert result.is_enabled is False
    assert result.failure_reason == "COMPOSER_DISABLED"


def test_composer_rejects_hallucination_and_fails_closed(
    enabled_composer: LocalCopilotComposer,
    sample_assertions: List[Dict[str, Any]],
):
    """Simulated hallucination or ungrounded synthesis must fail closed with UNGROUNDED_COMPOSITION_REJECTED."""
    result = enabled_composer.compose(
        assertions=sample_assertions,
        dialogue_context="storage cluster capacity requires sharding",
        simulate_hallucination=True,
    )
    assert result.body == ""
    assert result.groundedness == 0.0
    assert result.failure_reason == "UNGROUNDED_COMPOSITION_REJECTED"


def test_groundedness_validation_rejects_unattested_entities():
    """_validate_groundedness strictly flags ungrounded entity intrusions."""
    raw_claim = "storage cluster capacity requires 3-way sharding"
    safe_target = "Alice"
    spk = "Bob"

    # Fully grounded
    grounded_composed = "Alice, storage cluster capacity requires 3-way sharding. How are we mitigating this risk?"
    assert _validate_groundedness(grounded_composed, raw_claim, safe_target, spk) is True

    # Hallucinated extra entities not in claim, target, or framing words
    hallucinated_composed = "Alice, storage cluster capacity requires quantum photonics teleportation architecture."
    assert _validate_groundedness(hallucinated_composed, raw_claim, safe_target, spk) is False


# ==============================================================================
# Category 3: Timeout & Failure Modes (Fail-Closed to Silence)
# ==============================================================================


def test_composer_simulated_timeout_strictly_fails_closed(
    enabled_composer: LocalCopilotComposer,
    sample_assertions: List[Dict[str, Any]],
):
    """Timeout invariant: timeout MUST strictly fail closed to silence (body=""). NEVER flip to speaking."""
    result = enabled_composer.compose(
        assertions=sample_assertions,
        dialogue_context="storage cluster capacity sharding",
        simulate_timeout=True,
    )
    assert result.body == "", "CRITICAL: Timeout must never emit commentary text"
    assert result.failure_reason == "COMPOSER_TIMEOUT"


def test_composer_real_latency_budget_exceeded_fails_closed(
    sample_assertions: List[Dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
):
    """When wall-clock execution exceeds timeout budget, result is strictly silent."""
    # Zero-second timeout budget ensures immediate expiration
    budget_composer = LocalCopilotComposer(enabled=True, timeout_seconds=0.000001)

    result = budget_composer.compose(
        assertions=sample_assertions,
        dialogue_context="storage cluster capacity requires sharding",
    )
    assert result.body == ""
    assert result.failure_reason == "COMPOSER_TIMEOUT"


# ==============================================================================
# Category 4: Learning Proposal Boundary & Invariant Clamping
# ==============================================================================


def test_learning_proposal_clamps_to_upper_bound_ceiling(tmp_path: Path):
    """Synthesizing proposal at ceiling (0.90) clamps proposed value without exceeding 0.90."""
    proposal = synthesize_feedback_threshold_proposal(
        answer_id="ans_boundary_max",
        rating="wrong",
        note="Testing max threshold clamp",
        current_threshold=0.88,
        proposals_dir=tmp_path,
    )
    assert proposal is not None
    cal = proposal["extensions"]["com.partner_advisory.nee.calibration"]
    assert cal["current_value"] == 0.88
    # 0.88 + 0.05 = 0.93 -> clamped to 0.90
    assert cal["proposed_value"] == 0.90
    assert cal["proposed_value"] <= 0.90


def test_learning_proposal_clamps_to_lower_bound_floor(tmp_path: Path):
    """Synthesizing proposal below floor (0.50) clamps cleanly to 0.50."""
    proposal = synthesize_feedback_threshold_proposal(
        answer_id="ans_boundary_min",
        rating="not_useful",
        note="Testing min threshold",
        current_threshold=0.40,
        proposals_dir=tmp_path,
    )
    assert proposal is not None
    cal = proposal["extensions"]["com.partner_advisory.nee.calibration"]
    assert cal["current_value"] == 0.50
    assert cal["proposed_value"] == 0.55


def test_learning_proposal_invariants_immutable_across_all_payloads(tmp_path: Path):
    """Verify that auto_apply=False and approval_required=True are universal invariants."""
    for rating in ["wrong", "not_useful"]:
        proposal = synthesize_feedback_threshold_proposal(
            answer_id=f"ans_{rating}",
            rating=rating,
            note="Safety test",
            current_threshold=0.60,
            proposals_dir=tmp_path,
        )
        assert proposal is not None
        assert proposal["auto_apply"] is False, "Violation: auto_apply must be False"
        assert proposal["approval_required"] is True, "Violation: approval_required must be True"
        assert proposal["status"] == "proposed"
        assert proposal["extensions"]["com.partner_advisory.nee.calibration"]["auto_mutation_permitted"] is False
        assert proposal["extensions"]["com.partner_advisory.nee.calibration"]["requires_release_gate"] is True
