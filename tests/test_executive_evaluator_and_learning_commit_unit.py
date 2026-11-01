"""Unit tests for dual_pipeline.evals.strategic.executive_evaluator and uwg_core.learning_commit."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from dual_pipeline.evals.strategic.executive_evaluator import (
    ExecutiveStrategicEvaluator,
)
from dual_pipeline.uwg_core.learning_commit import (
    _check_duplicate_apply,
    _collect_applied_params,
    _find_simulation_receipt,
    _parse_learning_proposal_and_approval,
    _resolve_and_validate_learning_params,
    _validate_simulation_receipt,
    _values_match,
)


def test_executive_strategic_evaluator_dimensions_and_scorecard() -> None:
    evaluator = ExecutiveStrategicEvaluator()
    d1 = evaluator.evaluate_dimension_1_ea_capability_depth()
    assert d1.passed is True
    assert d1.score == 100.0

    with pytest.raises(ValueError, match="cannot be None"):
        evaluator.evaluate_dimension_1_ea_capability_depth(None)

    d1_partial = evaluator.evaluate_dimension_1_ea_capability_depth(
        {"l1_domain": "Grid", "l2_capability": "Monitoring"}
    )
    assert d1_partial.passed is False
    assert d1_partial.score == 50.0

    d2 = evaluator.evaluate_dimension_2_latent_multi_hop()
    assert d2.passed is True
    assert d2.details["max_discovered_hops"] >= 4

    d2_short = evaluator.evaluate_dimension_2_latent_multi_hop([("a", "rel", "b")])
    assert d2_short.passed is False

    d3 = evaluator.evaluate_dimension_3_cross_silo_contradictions()
    assert d3.passed is True

    scorecard = evaluator.run_full_evaluation()
    assert len(scorecard.dimension_scores) == 10
    assert scorecard.overall_score == 100.0


def test_learning_commit_parameter_resolution_and_validation() -> None:
    ext = {"target_parameter": "hitl.ambiguity_margin_threshold", "proposed_value": 0.22}
    params = _collect_applied_params(ext, {"applied_parameters": {"extra_param": 1.0}})
    assert params["hitl.ambiguity_margin_threshold"] == 0.22
    assert params["extra_param"] == 1.0

    with pytest.raises(ValueError, match="No parameters specified"):
        _collect_applied_params({}, {})

    proposal = {
        "proposal_id": "prop-1",
        "extensions": {
            "com.partner_advisory.nee.learning": {
                "target_parameter": "hitl.ambiguity_margin_threshold",
                "proposed_value": 0.99,
            }
        },
    }
    with pytest.raises(ValueError, match="out of bounds"):
        _resolve_and_validate_learning_params(proposal, {})


def test_learning_commit_simulation_and_duplicate_checks(tmp_path: Path) -> None:
    proposal = {"proposal_id": "prop-1", "canonical_digest": {"value": "d" * 64}}
    valid_sim = {"status": "SIMULATED_PASS", "success": True, "proposal_digest": "d" * 64, "proposal_id": "prop-1"}
    _validate_simulation_receipt(valid_sim, proposal, "prop-1")

    with pytest.raises(ValueError, match="REPLAY_QUALIFICATION_FAILED"):
        _validate_simulation_receipt({"status": "FAIL", "success": False}, proposal, "prop-1")

    with pytest.raises(ValueError, match="REPLAY_TAMPERED"):
        _validate_simulation_receipt(
            {"status": "PASS", "success": True, "proposal_digest": "e" * 64},
            proposal,
            "prop-1",
        )

    sim_path = tmp_path / "prop-1_simulation_receipt.json"
    sim_path.write_text('{"status": "PASS", "success": true}', encoding="utf-8")
    found = _find_simulation_receipt(proposal, "prop-1", tmp_path, {})
    assert found is not None and found["status"] == "PASS"

    _check_duplicate_apply("prop-1", tmp_path)
    (tmp_path / "prop-1_applied_receipt.json").write_text('{"status": "APPLIED"}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="DUPLICATE"):
        _check_duplicate_apply("prop-1", tmp_path)

    assert _values_match(0.2000001, 0.2000002) is True


def test_parse_learning_proposal_and_approval_guards() -> None:
    with pytest.raises(ValueError, match="requires 'proposal'"):
        _parse_learning_proposal_and_approval({})

    with pytest.raises(ValueError, match="requires 'approval_receipt'"):
        _parse_learning_proposal_and_approval({"proposal": {"proposal_id": "p1"}})

    with patch(
        "dual_pipeline.hitl_core.envelope.validate_approval_origin",
        return_value=(False, "synthetic_hook"),
    ):
        with pytest.raises(PermissionError, match="non-human approval origin"):
            _parse_learning_proposal_and_approval(
                {"proposal": {"proposal_id": "p1"}, "approval_receipt": {"id": "a1"}}
            )
