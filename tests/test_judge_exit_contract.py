"""Unit tests for Wave 4 Exit Decision Governance and Contract Verification.

Tests deterministic reference validation, semantic evidence entailment,
unevaluated QA roles reporting NOT_EVALUATED, raw score retention on HITL clarification,
and fail-closed exit gates on floor/verdict failures.
"""
from __future__ import annotations

from typing import Any, Dict

import pytest

from dual_pipeline.llm_judge_core.judge_clarification import evaluate_judge_clarification
from dual_pipeline.llm_judge_core.judges.forensic import ForensicTranscriptJudge
from dual_pipeline.release_thresholds import ReleaseVerdict, CANONICAL_RELEASE_POLICY
from dual_pipeline.transcript_qa_core.graph_reference_validator import (
    check_conflicting_duplicates,
    check_graph_reference_integrity,
    check_semantic_evidence_entailment,
)
from dual_pipeline.transcript_qa_core.role_consistency_validator import (
    RoleConsistencyReport,
    RoleConsistencyValidator,
)


def test_conflicting_duplicates_detected():
    data = {
        "entities": [
            {"name": "Alice", "role": "Lead Architect", "category": "Person"},
            {"name": "Alice", "role": "Intern", "category": "Person"},
        ]
    }
    ok, errs = check_conflicting_duplicates(data)
    assert not ok
    assert any("Conflicting duplicate" in e and "Alice" in e for e in errs)


def test_non_conflicting_duplicates_allowed():
    data = {
        "entities": [
            {"name": "Bob", "role": "Engineer", "category": "Person"},
            {"name": "Bob", "role": "Engineer", "category": "Person"},
        ]
    }
    ok, errs = check_conflicting_duplicates(data)
    assert ok
    assert len(errs) == 0


def test_dangling_graph_reference_integrity():
    data = {
        "entities": [{"name": "Carol", "category": "Person"}],
        "decisions": [
            {
                "topic": "Storage Engine",
                "owner": "Dave",  # Dave is not in entities!
                "stakeholders": ["Carol"],
            }
        ],
    }
    ok, errs = check_graph_reference_integrity(data)
    assert not ok
    assert any("Dangling entity reference 'Dave'" in e for e in errs)


def test_graph_reference_integrity_passes_when_resolved():
    data = {
        "entities": [
            {"name": "Carol", "category": "Person"},
            {"name": "Dave", "category": "Person"},
        ],
        "decisions": [
            {
                "topic": "Storage Engine",
                "owner": "Dave",
                "stakeholders": ["Carol"],
            }
        ],
    }
    ok, errs = check_graph_reference_integrity(data)
    assert ok
    assert len(errs) == 0


def test_unrelated_quote_entailment_fails():
    data = {
        "decisions": [
            {
                "topic": "Distributed Consensus Raft Implementation",
                "claim": "The team agreed to deploy Raft for metadata consistency across all cluster nodes",
                "evidence": [
                    "Let us order some pizza and soda for lunch today before the meeting ends"
                ],
            }
        ]
    }
    ok, errs = check_semantic_evidence_entailment(data)
    assert not ok
    assert any("Unrelated evidence quote lacking semantic overlap" in e for e in errs)


def test_entailed_quote_passes():
    data = {
        "decisions": [
            {
                "topic": "Distributed Consensus Raft Implementation",
                "claim": "Deploy Raft for metadata consensus across nodes",
                "evidence": [
                    "We decided to deploy Raft across the nodes to ensure metadata consensus"
                ],
            }
        ]
    }
    ok, errs = check_semantic_evidence_entailment(data)
    assert ok
    assert len(errs) == 0


def test_role_consistency_unevaluated_reports_not_evaluated():
    # Empty transcript or no roles to check
    healed, rep = RoleConsistencyValidator.validate_and_heal("", {})
    assert rep.status == "NOT_EVALUATED"
    assert rep.is_consistent is True
    assert rep.roles_checked == 0


def test_judge_clarification_retains_raw_score():
    judge_report: Dict[str, Any] = {
        "output_3_evaluation": {
            "composite_score": 86.0,
            "dimension_1": 15.0,
            "dimension_2": 15.0,
            "dimension_3": 13.0,
            "dimension_4": 15.0,
            "dimension_5": 14.0,
            "dimension_6": 14.0,
            "verdict": "HITL_REQUIRED",
            "status": "HITL_REQUIRED",
            "forensic_rationale": "Borderline floor deduction on dimension 3",
        }
    }
    final_decision: Dict[str, Any] = {
        "output_1_pass": True,
        "output_2_pass": True,
        "output_3_pass": False,
        "output_3_llm_score_pass": False,
    }
    metadata: Dict[str, Any] = {
        "meeting_name": "Test Meeting",
        "date": "2026-10-08",
        "operator_id": "human_tester",
        "approval_source": "human_operator",
        "user_command": "approve candidate",
    }

    # Resolver unconditionally affirms valid
    def mock_resolver(envelope):
        return "AFFIRM_VALID"

    clarified = evaluate_judge_clarification(
        judge_report=judge_report,
        final_decision=final_decision,
        metadata=metadata,
        interactive_resolver=mock_resolver,
    )
    assert clarified is True
    # Invariant: composite_score must retain raw 86.0, never overwritten with arbitrary 90.0
    out3_eval = judge_report["output_3_evaluation"]
    assert out3_eval["composite_score"] == 86.0
    assert out3_eval["verdict"] == "PASS_AUTHORIZED_EXCEPTION"
    assert out3_eval["status"] == "PASS_AUTHORIZED_EXCEPTION"
    assert out3_eval.get("authorized_exception") is True


def test_composite_release_blocks_high_score_with_judge_fail():
    # Scores are high (95, 95, 95) but Output 2 judge gave FAIL (e.g. floor failure)
    verdict, write_auth, meta = CANONICAL_RELEASE_POLICY.evaluate_composite_release(
        s1=95, s2=95, s3=95,
        v1="PASS", v2="FAIL", v3="PASS",
        deterministic_qa_passed=True,
        reconciliation_passed=True,
    )
    assert verdict == ReleaseVerdict.FAIL
    assert write_auth is False
    assert meta["out2_pass"] is False


def test_composite_release_permits_authorized_exception():
    # Score is 86 (below 90 pass threshold) but has authorized exception
    verdict, write_auth, meta = CANONICAL_RELEASE_POLICY.evaluate_composite_release(
        s1=92, s2=91, s3=86,
        v1="PASS", v2="PASS", v3="PASS_AUTHORIZED_EXCEPTION",
        deterministic_qa_passed=True,
        reconciliation_passed=True,
        out3_authorized_exception=True,
    )
    assert verdict == ReleaseVerdict.PASS
    assert write_auth is True
    assert meta["out3_pass"] is True


def test_forensic_judge_fails_closed_on_incomplete_evaluated_span(monkeypatch):
    judge = ForensicTranscriptJudge(model="gpt-5.6-sol", use_local_model=False)

    # Monkeypatch TokenProfiler to simulate aggressive truncation
    class MockProfiler:
        def __init__(self, default_model=None):
            pass
        def truncate_to_token_limit(self, text, max_tokens, model=None):
            return text[:20]  # truncate to 20 chars

    monkeypatch.setattr("dual_pipeline.token_optimization.TokenProfiler", MockProfiler)
    monkeypatch.setattr(judge, "use_local_model", False)

    class DummyClient:
        pass

    def dummy_generate(*args, **kwargs):
        return {"verdict": "PASS", "composite_score": 95.0}

    monkeypatch.setattr("dual_pipeline.llm_judge_core.judges.forensic._generate_client_forensic", dummy_generate)

    f1_long = "Alice: We discussed the entire agenda in deep detail from start to finish." * 5
    raw_long = f1_long

    res = judge.evaluate(f1_text=f1_long, raw_source_text=raw_long, client=DummyClient(), mock=False)
    assert res.get("incomplete_evaluated_span") is True
    assert res.get("evaluated_span_complete") is False
    assert res.get("verdict") == "FAIL"
    assert "Incomplete evaluated span" in res.get("forensic_rationale", "")
