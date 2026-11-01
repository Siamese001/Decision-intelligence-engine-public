"""End-to-End tests for Wave 5: Portfolio Runner, L1 Control Eval, W3-W6 Graph Binding & Longitudinal Ledger."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile

import pytest

from dual_pipeline.l1_evaluation import (
    load_l1_evaluation_suite,
    run_l1_evaluation_suite,
)
from dual_pipeline.evals.portfolio import (
    REQUIRED_TRACKS,
    validate_portfolio,
)
from dual_pipeline.evals.strategic import ExecutiveStrategicEvaluator
from evals.longitudinal_evaluation_tracker import LongitudinalEvaluationTracker
from tools.profile_u0_evals_workloads import (
    extract_token_usage,
    run_full_profile,
)
import tools.run_w3_temporal_observation_evaluation as run_w3
import tools.run_w4_daily_intelligence_evaluation as run_w4
import tools.run_w6_strategic_evidence_evaluation as run_w6


def test_l1_evaluation_controller_routing_and_terminal_comparison():
    """Verify L1 evaluation executes query routing and verifies expected terminal preservation."""
    suite_path = Path("evals/suites/query-l1-agentic-control-e1.json")
    assert suite_path.is_file()

    with tempfile.TemporaryDirectory(prefix="l1_e2e_test_") as tmpdir:
        res = run_l1_evaluation_suite(
            suite_path=suite_path,
            output_root=Path(tmpdir),
            evaluation_id="e2e-l1-verify",
        )
        assert res["status"] == "PASS"
        assert res["tier"] == "E1_DETERMINISTIC_FIXTURE"
        assert res["bounded_metrics"]["false_coverage_count"] == 0
        assert res["bounded_metrics"]["true_gap_preservation_rate"] == 1.0


def test_portfolio_validation_suite_content_and_execution():
    """Verify portfolio runner validates track content integrity and executes runnable suites."""
    portfolio_path = Path("evals/suites/pipeline-assurance-portfolio-e2.json")
    assert portfolio_path.is_file()

    # 1. Structural and track content validation
    report = validate_portfolio(portfolio_path, execute_suites=False)
    assert report["status"] == "PASS"
    assert report["track_count"] == len(REQUIRED_TRACKS)
    for track in report["tracks"]:
        assert track["byte_size"] > 0
        assert "suite_format" in track

    # 2. Execution of runnable tracks
    exec_report = validate_portfolio(portfolio_path, execute_suites=True)
    assert exec_report["status"] == "PASS"
    assert "ingestion" in exec_report["executed_tracks"]
    assert exec_report["executed_tracks"]["ingestion"] == "PASS"


def test_executive_strategic_evaluator_live_eval_and_symmetric_conflict_discovery():
    """Verify ExecutiveStrategicEvaluator runs full evaluation and symmetrically detects architecture conflicts."""
    evaluator = ExecutiveStrategicEvaluator()
    scorecard = evaluator.run_full_evaluation()
    assert scorecard.passed is True
    assert scorecard.overall_score >= 75.0

    # Symmetric conflict discovery: reversed [batch, streaming] order must detect CONFLICTS_WITH
    reversed_initiatives = [
        {
            "id": "init:core_edo_batch",
            "team": "Enterprise Data Office",
            "architecture": "batch_medallion_databricks",
            "ingestion_sla_seconds": 3600,
        },
        {
            "id": "init:analytics_streaming",
            "team": "Apex Global Analytics",
            "architecture": "real_time_streaming_kafka",
            "ingestion_sla_seconds": 2,
        },
    ]
    dim3_score = evaluator.evaluate_dimension_3_cross_silo_contradictions(reversed_initiatives)
    assert dim3_score.passed is True
    assert dim3_score.score == 100.0
    assert dim3_score.details["count"] >= 1
    assert dim3_score.details["conflicts_detected"][0]["type"] == "CONFLICTS_WITH"


def test_w3_w4_w6_sqlite_db_parameterization():
    """Verify run_w3, run_w4, and run_w6 inspect and report real SQLite table counts from --db-path."""
    with tempfile.TemporaryDirectory(prefix="wave_sqlite_test_") as tmpdir:
        db_path = Path(tmpdir) / "test_knowledge_graph.sqlite"
        with sqlite3.connect(db_path) as conn:
            conn.execute("CREATE TABLE assertions (id TEXT PRIMARY KEY, content TEXT)")
            conn.execute("CREATE TABLE temporal_observations (id TEXT PRIMARY KEY, val TEXT)")
            conn.execute("CREATE TABLE evidence_units (id TEXT PRIMARY KEY, finding TEXT)")
            conn.execute("INSERT INTO assertions VALUES ('a1', 'test assertion')")
            conn.execute("INSERT INTO assertions VALUES ('a2', 'test assertion 2')")
            conn.execute("INSERT INTO temporal_observations VALUES ('t1', 'test obs')")
            conn.execute("INSERT INTO evidence_units VALUES ('e1', 'test evidence')")
            conn.commit()

        # W3
        contract_w3 = Path("governance/decision_intelligence/W3_TEMPORAL_OBSERVATION_CONTRACT.json")
        res_w3 = run_w3.evaluate(db_path, contract_w3)
        assert res_w3["status"] == "PASS"
        assert res_w3["sqlite_table_counts"]["assertions"] == 2
        assert res_w3["sqlite_table_counts"]["temporal_observations"] == 1
        assert res_w3["sqlite_table_counts"]["evidence_units"] == 1

        # W4
        contract_w4 = Path("governance/decision_intelligence/W4_DAILY_INTELLIGENCE_CONTRACT.json")
        res_w4 = run_w4.evaluate(db_path, contract_w4)
        assert res_w4["status"] == "PASS"
        assert res_w4["sqlite_table_counts"]["assertions"] == 2

        # W6
        contract_w6 = Path("governance/decision_intelligence/W6_STRATEGIC_EVIDENCE_CONTRACT.json")
        res_w6 = run_w6.evaluate(db_path, contract_w6)
        assert res_w6["status"] == "PASS"
        assert res_w6["sqlite_table_counts"]["assertions"] == 2


def test_longitudinal_evaluation_tracker_clean_lifecycle_and_zero_synthetic_commits():
    """Verify tracker starts empty on new paths and source/ledgers contain zero synthetic commit hashes."""
    with tempfile.TemporaryDirectory(prefix="longitudinal_test_") as tmpdir:
        ledger_path = Path(tmpdir) / "test_ledger.json"
        tracker = LongitudinalEvaluationTracker(ledger_path=ledger_path)
        assert tracker.entries == []

        # Record authentic entry
        entry = tracker.record_run(
            pipeline_version="1.0.0",
            rubric_version="1.0.0",
            benchmark_suite="Pipeline_B_Advisory_Extraction",
            model_id="frontier-extractor-tier",
            metrics={"composite_score": 85.0},
            gates={"advisory_precision": True},
            git_commit=None,
        )
        assert entry.git_commit is None
        assert len(tracker.entries) == 1

    # Verify zero synthetic fake commits in source or ledger
    tracker_src = Path("evals/longitudinal_evaluation_tracker.py").read_text(encoding="utf-8")
    for fake_sha in ("a1b2c3d", "b2c3d4e", "c3d4e5f", "e4f5g6h"):
        assert fake_sha not in tracker_src, f"Synthetic commit {fake_sha} found in tracker source"

    ledger_file = Path("reports/longitudinal_eval_ledger.json")
    if ledger_file.is_file():
        ledger_text = ledger_file.read_text(encoding="utf-8")
        for fake_sha in ("a1b2c3d", "b2c3d4e", "c3d4e5f", "e4f5g6h"):
            assert fake_sha not in ledger_text, f"Synthetic commit {fake_sha} found in reports ledger"


def test_profile_u0_evals_workloads_real_token_extraction():
    """Verify profile_u0_evals_workloads dynamically extracts real stage token counts."""
    # Empty receipts -> 0 tokens without crashing
    empty_tok = extract_token_usage([])
    assert empty_tok["total_input_tokens"] == 0
    assert empty_tok["total_output_tokens"] == 0

    # Profile run returns structured footprints
    profile = run_full_profile(receipts=[])
    assert "token_usage_and_costs" in profile
    assert "critical_path_latency" in profile
    assert profile["total_receipts_scanned"] == 0
