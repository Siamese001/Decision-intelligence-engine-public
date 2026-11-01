"""Adversarial and boundary regression tests for Wave 5."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import pytest

from dual_pipeline.l1_evaluation import (
    L1EvaluationError,
    _case_measurement,
    evaluate_l1_treatment,
    load_l1_evaluation_suite,
)
from dual_pipeline.evals.portfolio import (
    REQUIRED_TRACKS,
    validate_portfolio,
    _validate_track_suite_content,
)
from dual_pipeline.evals.strategic import ExecutiveStrategicEvaluator
from evals.longitudinal_evaluation_tracker import LongitudinalEvaluationTracker


def test_adversarial_boundary_payload_none_and_empty_strategic_dimensions():
    """Category 1 (Boundary payload): Verify None payloads raise ValueError and empty payloads return 0.0."""
    evaluator = ExecutiveStrategicEvaluator()

    # Dim 1: None raises ValueError; {} returns 0.0 / passed=False
    with pytest.raises(ValueError, match="capability_payload cannot be None"):
        evaluator.evaluate_dimension_1_ea_capability_depth(None)

    empty_dim1 = evaluator.evaluate_dimension_1_ea_capability_depth({})
    assert empty_dim1.passed is False
    assert empty_dim1.score == 0.0

    # Dim 2: None raises ValueError; [] returns 0.0 / passed=False
    with pytest.raises(ValueError, match="graph_edges cannot be None"):
        evaluator.evaluate_dimension_2_latent_multi_hop(None)

    empty_dim2 = evaluator.evaluate_dimension_2_latent_multi_hop([])
    assert empty_dim2.passed is False
    assert empty_dim2.score == 0.0

    # Dim 3: None raises ValueError; [] returns 0.0 / passed=False
    with pytest.raises(ValueError, match="initiatives cannot be None"):
        evaluator.evaluate_dimension_3_cross_silo_contradictions(None)

    empty_dim3 = evaluator.evaluate_dimension_3_cross_silo_contradictions([])
    assert empty_dim3.passed is False
    assert empty_dim3.score == 0.0


def test_adversarial_zero_byte_portfolio_tracks_and_missing_tracks():
    """Category 1 & 4 (Boundary payload & exhaustion): 0-byte tracks and missing tracks fail closed."""
    with tempfile.TemporaryDirectory(prefix="adv_portfolio_") as tmpdir:
        empty_suite = Path(tmpdir) / "empty_track.json"
        empty_suite.write_text("", encoding="utf-8")

        with pytest.raises(ValueError, match="is empty"):
            _validate_track_suite_content(empty_suite, {"track_id": "test_empty"})

        # Python track with 0 test_ functions
        no_tests_py = Path(tmpdir) / "no_tests.py"
        no_tests_py.write_text("x = 42\ndef helper(): pass\n", encoding="utf-8")
        with pytest.raises(ValueError, match="contains zero test_ functions"):
            _validate_track_suite_content(no_tests_py, {"track_id": "test_py"})

        # Portfolio with 9 valid tracks but duplicate track ID triggers validate_portfolio check
        from dual_pipeline.core import read_json, seal_artifact
        base_portfolio = read_json(Path("evals/suites/pipeline-assurance-portfolio-e2.json"))
        bad_tracks = list(base_portfolio["tracks"])
        bad_tracks[0] = dict(bad_tracks[1])  # duplicate track
        base_portfolio["tracks"] = bad_tracks
        sealed_bad = seal_artifact(base_portfolio)
        bad_portfolio_path = Path(tmpdir) / "bad_portfolio.json"
        bad_portfolio_path.write_text(json.dumps(sealed_bad), encoding="utf-8")
        with pytest.raises(ValueError, match="portfolio must contain exactly"):
            validate_portfolio(bad_portfolio_path)


def test_adversarial_corrupted_json_ledger_raises_without_wiping():
    """Category 6 (Rollback & recovery): Corrupt JSON in longitudinal ledger raises error instead of silent wipe."""
    with tempfile.TemporaryDirectory(prefix="adv_ledger_") as tmpdir:
        ledger_path = Path(tmpdir) / "corrupt_ledger.json"
        corrupt_content = '{"ledger_contract": "urn:nee...", "entries": [corrupted json syntax'
        ledger_path.write_text(corrupt_content, encoding="utf-8")

        # Must raise ValueError, NOT catch Exception and reset entries to []
        with pytest.raises(ValueError, match="Corrupted longitudinal evaluation ledger"):
            LongitudinalEvaluationTracker(ledger_path=ledger_path)

        # File content must remain intact (not overwritten or wiped)
        assert ledger_path.read_text(encoding="utf-8") == corrupt_content


def test_adversarial_exact_production_regressions_initiatives_and_l1_terminals():
    """Category 7 (Exact production regression): Order-independence of initiatives and mutated L1 terminals fail closed."""
    evaluator = ExecutiveStrategicEvaluator()

    # Reversing order: [batch, streaming] vs [streaming, batch] both detect CONFLICTS_WITH
    batch_init = {
        "id": "init:batch_data",
        "architecture": "batch_warehouse",
        "ingestion_sla_seconds": 7200,
    }
    streaming_init = {
        "id": "init:streaming_data",
        "architecture": "real_time_streaming",
        "ingestion_sla_seconds": 5,
    }

    forward_score = evaluator.evaluate_dimension_3_cross_silo_contradictions([streaming_init, batch_init])
    reverse_score = evaluator.evaluate_dimension_3_cross_silo_contradictions([batch_init, streaming_init])

    assert forward_score.passed is True
    assert reverse_score.passed is True
    assert forward_score.score == reverse_score.score == 100.0

    # Mutated L1 expected terminal diverges from controller -> status FAIL
    mock_case = {
        "case_id": "test_mutation_case",
        "family": "true_gap",
        "initial_terminal_class": "EXPLICIT_GAP",
        "expected_terminals": {"bounded": "COMPLETED", "off": "EXPLICIT_GAP"},
        "metrics": {"bounded_elapsed_ms": 50, "off_elapsed_ms": 40},
    }
    # For true_gap, controller resolves EXPLICIT_GAP. Since expected is COMPLETED, it must fail.
    res, measurement = _case_measurement(mock_case, mode="bounded")
    assert res["status"] == "FAIL"
    assert measurement["terminal_preserved"] is False
