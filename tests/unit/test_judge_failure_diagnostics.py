"""
Unit tests for Judge Failure Diagnostics and Structured Taxonomy Recording (Wave 1).
Validates schema compliance, null handling, classification taxonomy, and report generation.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.transcript_qa_core.judge_failure_diagnostics import (
    FailureType,
    JudgeFailureDiagnosticsCollector,
    JudgeFailureRecord,
    classify_qa_failure,
    suggest_remediation,
)
from dual_pipeline.transcript_qa_core.evaluator import evaluate_judge_and_release


def test_judge_failure_record_schema_and_null_fidelity():
    """Verify that JudgeFailureRecord serializes exact required schema and explicit nulls."""
    rec = JudgeFailureRecord(
        output="03",
        attempt=2,
        judge="GraphExtractionJudge",
        gate="3.14",
        criterion="Evidence grounding",
        status="FAIL",
        score=72,
        threshold=80,
        failure_type=FailureType.GROUNDING.value,
        expected="Grounded citation",
        observed="Unrelated evidence ev-03",
        evidence_ids=["ev-03"],
        root_cause="Citation mismatch",
        remediation="Prune invalid citation",
        retry_scope="output_3",
        llm_required=False,
        retry_count=1,
        elapsed_ms=1450,
    )
    d = rec.to_dict()
    assert d["output"] == "03"
    assert d["attempt"] == 2
    assert d["judge"] == "GraphExtractionJudge"
    assert d["gate"] == "3.14"
    assert d["criterion"] == "Evidence grounding"
    assert d["status"] == "FAIL"
    assert d["score"] == 72
    assert d["threshold"] == 80
    assert d["failure_type"] == "GROUNDING"
    assert d["expected"] == "Grounded citation"
    assert d["observed"] == "Unrelated evidence ev-03"
    assert d["evidence_ids"] == ["ev-03"]
    assert d["root_cause"] == "Citation mismatch"
    assert d["remediation"] == "Prune invalid citation"
    assert d["retry_scope"] == "output_3"
    assert d["llm_required"] is False
    assert d["retry_count"] == 1
    assert d["elapsed_ms"] == 1450

    # Test explicit null serialization when fields are absent
    rec_null = JudgeFailureRecord(
        output="02",
        attempt=1,
        judge="ExecutiveRecapJudge",
        gate=None,
        criterion="Strategic depth",
        status="FAIL",
        score=None,
        threshold=None,
        failure_type="CONTENT",
        expected="Deep strategic trade-offs",
        observed="Shallow discussion points",
        evidence_ids=None,
        root_cause="Missing trade-offs",
        remediation="Expand trade-off matrix",
        retry_scope="output_2",
        llm_required=True,
        retry_count=0,
        elapsed_ms=None,
    )
    d_null = rec_null.to_dict()
    assert d_null["gate"] is None
    assert d_null["score"] is None
    assert d_null["threshold"] is None
    assert d_null["evidence_ids"] is None
    assert d_null["elapsed_ms"] is None


def test_failure_type_classification():
    """Verify categorization into CONTENT, GROUNDING, EVALUATOR, TIMEOUT, INFRASTRUCTURE."""
    assert classify_qa_failure("Gate_3_14", "Proposal evidence citation 'ev-01' lacks topical overlap") == FailureType.GROUNDING
    assert classify_qa_failure("Gate_1_1", "Evidence locator timestamp does not resolve") == FailureType.GROUNDING
    assert classify_qa_failure("Gate_2_5", "Word count underflow: 250 words") == FailureType.CONTENT
    assert classify_qa_failure("Gate_3_1", "Invalid syntax_valid or schema_version_5_7") == FailureType.EVALUATOR
    assert classify_qa_failure("Gate_Network", "LLM request timed out after 60s") == FailureType.TIMEOUT
    assert classify_qa_failure("Gate_Network", "HTTP 429 rate limit exceeded or connection locked") == FailureType.INFRASTRUCTURE


def test_suggest_remediation():
    """Verify targeted actionable remediations based on failure type."""
    rem, llm = suggest_remediation("Gate_3_14", "evidence citation 'ev-03' lacks topical overlap", FailureType.GROUNDING)
    assert "Prune invalid citation" in rem
    assert llm is False

    rem, llm = suggest_remediation("Gate_2_5", "Word count underflow", FailureType.CONTENT)
    assert "Regenerate" in rem
    assert llm is True


def test_collector_extraction_from_qa_report(tmp_path: Path):
    """Verify extraction of QA failures and JSON report generation."""
    collector = JudgeFailureDiagnosticsCollector(root_dir=tmp_path)
    mock_qa_report = {
        "status": "FAIL",
        "output_03_canonical_signal_projection": {
            "status": "FAIL",
            "passed": False,
            "gates": {
                "Gate_3_14_Projection_Evidence_Reconciliation": {
                    "passed": False,
                    "failures": [
                        "Proposal 'p-01' evidence citation 'ev-03' lacks topical overlap with proposal description."
                    ]
                }
            }
        }
    }
    extracted = collector.extract_from_qa_report(mock_qa_report, attempt=2, retry_count=1, elapsed_ms=1450)
    assert extracted == 1
    assert len(collector.failures) == 1

    f = collector.failures[0]
    assert f.output == "03"
    assert f.attempt == 2
    assert f.gate == "3.14"
    assert f.failure_type == "GROUNDING"
    assert f.evidence_ids == ["ev-03"]
    assert f.retry_scope == "output_3"
    assert f.llm_required is False

    rep_path = collector.save_report(tmp_path / "reports" / "judge_failure_diagnostics.json")
    assert rep_path.exists()
    with open(rep_path, "r", encoding="utf-8") as fp:
        data = json.load(fp)
    assert "failures" in data
    assert len(data["failures"]) == 1
    assert "summary" in data
    assert "metadata" in data
    assert data["metadata"]["total_failures"] == 1


def test_collector_preserves_successful_results():
    """Verify successful judge results are preserved separately from failures."""
    collector = JudgeFailureDiagnosticsCollector()
    mock_judge_report = {
        "status": "FAIL",
        "output_1_evaluation": {
            "composite_score": 95,
            "verdict": "PASS",
            "forensic_rationale": "Forensic transcript achieves complete verbatim fidelity.",
        },
        "output_2_evaluation": {
            "composite_score": 60,
            "verdict": "FAIL",
            "release_threshold": 80,
            "forensic_rationale": "Executive recap lacks depth and omissions in discussion points.",
            "actionable_remediation_diff": "Expand discussion points with key technical decisions.",
        },
    }
    extracted = collector.extract_from_judge_report(mock_judge_report, attempt=1, retry_count=0)
    assert extracted == 1
    assert len(collector.failures) == 1
    assert "output_01_forensictranscriptjudge" in collector.successful_results
    assert collector.successful_results["output_01_forensictranscriptjudge"]["score"] == 95


def test_evaluate_judge_and_release_emits_diagnostics(tmp_path: Path):
    """Verify end-to-end integration: evaluate_judge_and_release populates judge_failure_diagnostics."""
    meta = {
        "output_dir": str(tmp_path / "meeting_output"),
        "attempt": 1,
        "retry_count": 0,
        "write_diagnostics": True,
    }
    f1 = "# Transcript\n\nSpeaker 1: Hello\n"
    f2 = "# Recap\n\nDiscussion points\n"
    f3 = json.dumps({"graph_mutations": {}})

    qa_rep, judge_rep, decision = evaluate_judge_and_release(
        f1_text=f1,
        f2_text=f2,
        f3_text=f3,
        metadata=meta,
        mock=True,
    )
    assert "judge_failure_diagnostics" in judge_rep
    assert "judge_failure_diagnostics" in decision
    diag = decision["judge_failure_diagnostics"]
    assert "failures" in diag
    assert "summary" in diag


def test_reports_judge_failure_diagnostics_json_schema():
    """Verify that reports/judge_failure_diagnostics.json exists and strictly validates."""
    report_file = Path("reports/judge_failure_diagnostics.json")
    if not report_file.exists():
        pytest.skip("reports/judge_failure_diagnostics.json has not been compiled yet")

    with open(report_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert "metadata" in data
    assert "summary" in data
    assert "failures" in data
    assert "retry_history" in data
    assert "successful_results" in data

    valid_types = {"CONTENT", "GROUNDING", "EVALUATOR", "TIMEOUT", "INFRASTRUCTURE"}
    required_keys = {
        "output", "attempt", "judge", "gate", "criterion", "status",
        "score", "threshold", "failure_type", "expected", "observed",
        "evidence_ids", "root_cause", "remediation", "retry_scope",
        "llm_required", "retry_count", "elapsed_ms"
    }

    assert len(data["failures"]) > 0
    for idx, fail in enumerate(data["failures"]):
        missing = required_keys - set(fail.keys())
        assert not missing, f"Failure record {idx} missing keys: {missing}"
        assert fail["status"] == "FAIL"
        assert fail["failure_type"] in valid_types, f"Invalid failure_type: {fail['failure_type']}"
        assert isinstance(fail["attempt"], int)
        assert isinstance(fail["retry_count"], int)
        assert isinstance(fail["llm_required"], bool)
        assert isinstance(fail["output"], str)
        assert isinstance(fail["judge"], str)
        assert isinstance(fail["root_cause"], str)
        assert isinstance(fail["remediation"], str)


def test_collector_accumulates_across_attempts(tmp_path: Path):
    """Verify collector accumulates failures and retry history across sequential attempts."""
    diag_file = tmp_path / "judge_failure_diagnostics.json"

    # Attempt 1
    c1 = JudgeFailureDiagnosticsCollector(root_dir=tmp_path)
    c1.record_failure(JudgeFailureRecord(
        output="03", attempt=1, judge="GraphExtractionGate", gate="3.1",
        criterion="Gate_3_1_Schema", status="FAIL", score=None, threshold=None,
        failure_type="EVALUATOR", expected="valid schema", observed="invalid schema",
        evidence_ids=None, root_cause="Schema syntax error", remediation="Fix syntax",
        retry_scope="output_3", llm_required=False, retry_count=0, elapsed_ms=100.0,
    ))
    c1.record_attempt(attempt=1, outputs={"output_1": True, "output_2": True, "output_3": False})
    c1.save_report(diag_file)

    # Attempt 2 loads Attempt 1 and appends
    c2 = JudgeFailureDiagnosticsCollector(root_dir=tmp_path)
    c2.load_existing(diag_file)
    assert len(c2.failures) == 1
    assert len(c2.retry_history) == 1

    c2.record_failure(JudgeFailureRecord(
        output="03", attempt=2, judge="GraphExtractionGate", gate="3.2",
        criterion="Gate_3_2_Integrity", status="FAIL", score=None, threshold=None,
        failure_type="GROUNDING", expected="valid edges", observed="missing edge",
        evidence_ids=["ev-01"], root_cause="Missing edge", remediation="Link node",
        retry_scope="output_3", llm_required=False, retry_count=1, elapsed_ms=120.0,
    ))
    c2.record_attempt(attempt=2, outputs={"output_1": True, "output_2": True, "output_3": False})
    c2.save_report(diag_file)

    with open(diag_file, "r", encoding="utf-8") as fp:
        saved = json.load(fp)

    assert saved["metadata"]["total_attempts"] == 2
    assert saved["metadata"]["total_failures"] == 2
    assert len(saved["retry_history"]) == 2
    assert saved["retry_history"][0]["attempt"] == 1
    assert saved["retry_history"][1]["attempt"] == 2


def test_eval_cache_excludes_failure_caching():
    """Verify update_evaluation_cache excludes FAIL, SKIPPED, and DETERMINISTIC_QA_FAILED cards."""
    from dual_pipeline.transcript_qa_core.eval_cache import (
        _EVALUATION_CACHE,
        update_evaluation_cache,
    )
    k_fail = "out3:test_fail_hash:model:high:phash:deptag"
    fail_card = {
        "status": "FAIL",
        "verdict": "FAIL",
        "composite_score": 0,
        "reason": "DETERMINISTIC_QA_FAILED",
        "is_valid_scorecard": False,
    }
    update_evaluation_cache(k_fail, fail_card)
    assert k_fail not in _EVALUATION_CACHE

    k_skip = "out3:test_skip_hash:model:high:phash:deptag"
    skip_card = {
        "status": "SKIPPED",
        "verdict": "FAIL",
        "composite_score": 0,
        "reason": "DETERMINISTIC_QA_FAILED",
        "is_valid_scorecard": False,
    }
    update_evaluation_cache(k_skip, skip_card)
    assert k_skip not in _EVALUATION_CACHE


