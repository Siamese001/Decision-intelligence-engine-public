"""Tests for Test Suite Orchestrator & Concurrency MCP Server (Rank 5)."""
import json
from pathlib import Path

from tools.mcp_test_orchestrator_server import (
    audit_wave_test_coverage,
    extract_test_failure_diagnostics,
    provision_isolated_test_db,
    run_governed_test_suite,
)


def test_run_governed_test_suite_class_a_violation():
    res = run_governed_test_suite("tests/", is_receipt_run=False)
    data = json.loads(res)
    assert "error" in data
    assert "Class A debug runs MUST be scoped" in data["error"]


def test_run_governed_test_suite_valid_scope():
    # Run a fast single test
    res = run_governed_test_suite("tests/test_mcp_judge_eval_server.py::test_calibrate_hybrid_weights_legacy_3engine", is_receipt_run=False)
    data = json.loads(res)
    assert data["status"] == "PASSED"
    assert data["metrics"]["passed"] == 1


def test_provision_isolated_test_db():
    res = provision_isolated_test_db()
    data = json.loads(res)
    assert data["status"] == "PROVISIONED"
    assert "sandbox_id" in data
    assert Path(data["isolated_db_path"]).is_file()


def test_extract_test_failure_diagnostics():
    sample_trace = """
=================================== FAILURES ===================================
__________________________________ test_fail ___________________________________
    def test_fail():
>       assert False
E       AssertionError: assert False

tests/test_foo.py:10: AssertionError
=========================== short test summary info ============================
"""
    res = extract_test_failure_diagnostics(sample_trace)
    data = json.loads(res)
    assert data["detected_failure_count"] == 1
    assert data["failures"][0]["test_name"] == "test_fail"
    assert "AssertionError" in data["failures"][0]["error_snippet"]


def test_audit_wave_test_coverage():
    res = audit_wave_test_coverage("Wave 1", "tests/test_mcp_speech_server.py,tests/test_mcp_contract_server.py")
    data = json.loads(res)
    assert data["wave"] == "Wave 1"
    assert data["all_fixtures_present"] is True
    assert data["declared_test_count"] == 2
