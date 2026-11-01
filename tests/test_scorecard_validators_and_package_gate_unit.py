"""Unit tests for dual_pipeline.release_policy_core.scorecard_validators and package_gate."""
from __future__ import annotations

import pytest

from dual_pipeline.release_policy_core.package_gate import (
    _check_decision_authorization,
    _check_judge_report_preconditions,
    _has_rubric_dimensions,
    _has_stage_incomplete_span,
    _populate_scorecard_default_provenance,
    _verify_mandatory_meeting_scorecards,
)
from dual_pipeline.release_policy_core.scorecard_validators import (
    _record_floor_error,
    _resolve_effective_mode,
    _val_num,
    _validate_frozen_skip_shas,
    _validate_out2_fallback_dims,
    evaluate_document_release_gate,
    validate_micro_judge_scorecard,
    validate_scorecard_provenance,
)


def test_val_num_and_floor_errors() -> None:
    errs: list[str] = []
    assert _val_num(15.5, 0.0, 20.0, "dim1", errs) == 15.5
    assert _val_num(True, 0.0, 20.0, "dim_bool", errs) is None
    assert _val_num(float("nan"), 0.0, 20.0, "dim_nan", errs) is None
    assert _val_num(25.0, 0.0, 20.0, "dim_high", errs) is None
    assert len(errs) == 3

    flr_errs: list[str] = []
    _record_floor_error("dim_a", 10.0, 15.0, "PASS", flr_errs)
    assert any("more than 1 point below" in e for e in flr_errs)

    flr_errs2: list[str] = []
    _record_floor_error("dim_b", 14.5, 15.0, "PASS", flr_errs2)
    assert any("below mandatory floor" in e for e in flr_errs2)


def test_validate_out2_fallback_and_provenance() -> None:
    errs: list[str] = []
    tot = _validate_out2_fallback_dims(
        {"dimension_scores": {"d1": 12.0, "d2": 18.0}, "verdict": "PASS"},
        errs,
    )
    assert tot == 30.0
    assert len(errs) == 1

    card = {"effective_model": "judge-v1", "is_provider_fallback": False}
    ok, p_errs = validate_scorecard_provenance(card)
    assert ok is True
    assert p_errs == []
    assert card["judge_model"] == "judge-v1"

    assert _resolve_effective_mode({"modality": "document"}, None) == "document"
    assert _resolve_effective_mode({}, "meeting") == "meeting"

    sha_err = _validate_frozen_skip_shas(
        {"candidate_sha256": "aaa", "frozen_sha256": "bbb"},
        candidate_sha256="aaa",
        expected_sha256="bbb",
    )
    assert sha_err is not None and "does not match" in sha_err


def test_validate_micro_judge_scorecard_skip_and_out2() -> None:
    skip_card = {
        "status": "SKIPPED",
        "verdict": "SKIPPED",
        "reason": "NOT_APPLICABLE_DOCUMENT_MODE",
        "pipeline_mode": "DOCUMENT_DIRECT_PROJECTION",
        "judge_model": "judge-v1",
    }
    ok, errs = validate_micro_judge_scorecard(skip_card, output_num=1)
    assert ok is True, errs

    out2_card = {
        "verdict": "PASS",
        "composite_score": 88.0,
        "section_scores": {
            "header_and_overview": 8.0,
            "key_updates_technical_substance": 30.0,
            "conditional_extensions": 18.0,
            "appendix_stakeholder_perspectives": 32.0,
        },
        "judge_model": "judge-v1",
        "is_fallback": False,
    }
    ok2, errs2 = validate_micro_judge_scorecard(out2_card, output_num=2, require_provenance=True)
    assert ok2 is True, errs2

    doc_gate = evaluate_document_release_gate({"composite_score": 95.0, "verdict": "PASS"})
    assert doc_gate["status"] == "PASS"


def test_package_gate_authorization_and_preconditions() -> None:
    with pytest.raises(RuntimeError, match="authoritative_write_authorized=False"):
        _check_decision_authorization({"status": "REJECTED"}, "pkg_a")

    with pytest.raises(RuntimeError, match="MOCK_SYNTHESIS_NOT_AUTHORITATIVE"):
        _check_decision_authorization(
            {"authoritative_write_authorized": True, "is_mock": True},
            "pkg_a",
        )

    with pytest.raises(RuntimeError, match="output_3_llm_score_pass=False"):
        _check_decision_authorization(
            {"authoritative_write_authorized": True, "output_3_llm_score_pass": False},
            "pkg_a",
        )

    _check_decision_authorization(
        {"authoritative_write_authorized": True, "output_3_llm_score_pass": True},
        "pkg_a",
    )

    assert _has_stage_incomplete_span([(1, {"incomplete_evaluated_span": True})], {}) is True
    assert _has_stage_incomplete_span([(1, {"status": "PASS"})], {}) is False
    assert _has_rubric_dimensions({"dimension_1": 18.0}) is True

    card: dict[str, object] = {}
    _populate_scorecard_default_provenance(card, {"judge_model": "j1", "rubric_version": "2.1"})
    assert card["judge_model"] == "j1"

    with pytest.raises(RuntimeError, match="missing mandatory Output 1 scorecard"):
        _verify_mandatory_meeting_scorecards([(1, None)], {"mode": "meeting"}, "pkg_m")

    with pytest.raises(RuntimeError, match="short-circuited"):
        _check_judge_report_preconditions({"short_circuit": True}, "pkg_m")

    with pytest.raises(RuntimeError, match="mock provider"):
        _check_judge_report_preconditions({"evaluator_model": "mock"}, "pkg_m")
