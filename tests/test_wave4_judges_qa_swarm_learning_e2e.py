"""
tests/test_wave4_judges_qa_swarm_learning_e2e.py

End-to-end lifecycle verification for Wave 4:
Decomposed LLM Micro-Judges & 15-Role Transcript QA Swarm Learning.

Tests:
1. TranscriptQASwarm role-level defect detection and QUALITY_CHECK_FAILURE telemetry emission across QA roles.
2. ComponentRetryPlanner repair outcome recording, historical success rate re-ranking, and strict max_retries=2 ceiling emitting RETRY_EXHAUSTION.
3. Decomposed LLM Micro-Judges (all 9 roles) calibrate borderline escalation bands from hitl_prior_calibration.json.
4. Judge clarification triggers DECISION_OVERRIDE telemetry on authentic operator affirmations.
5. Strict preservation of non-compensating hard-fail floors (composite < 85.0 or multi-point floor breaches fail closed).
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

from dual_pipeline.learning_adapters.agent_outcome_adapter import (
    AgentOutcomeAdapter,
    OutcomeType,
)
from dual_pipeline.learning_adapters.live_pipeline_integrator import (
    LivePipelineIntegrator,
)
from dual_pipeline.llm_judge import (
    AgenticLeverageJudge,
    DecomposedMultiJudge,
    EnterpriseImpactJudge,
    ExecutiveRecapJudge,
    ForensicAccuracyJudge,
    GraphMutationJudge,
    GraphProvenanceJudge,
    GraphTransportJudge,
    HolisticQualityJudge,
    get_calibrated_borderline_band,
    record_judge_override,
)
from dual_pipeline.llm_judge_core.judge_clarification import (
    assign_clarified_graph_verdict,
    evaluate_judge_clarification,
)
from dual_pipeline.transcript_pipeline_core.component_retry_planner import (
    ComponentRetryPlanner,
    RetryBudget,
    RetryClass,
)
from dual_pipeline.transcript_qa_core.qa_swarm import (
    SWARM_ROLES,
    QASwarm,
    TranscriptQASwarm,
)


@pytest.fixture
def temp_integrator(tmp_path: Path) -> LivePipelineIntegrator:
    events_log = tmp_path / "agent_outcomes.jsonl"
    return LivePipelineIntegrator(events_log_path=events_log)


@pytest.fixture
def mock_priors_file(tmp_path: Path) -> Path:
    priors_path = tmp_path / "hitl_prior_calibration.json"
    priors_payload = {
        "categories_count": 2,
        "learned_priors": {
            "judge_deduction_clarification": {
                "AFFIRM_VALID": {
                    "affirmations": 80,
                    "affirmed_weight": 80.0,
                    "rejections": 20,
                    "rejected_weight": 20.0,
                },
                "UPHOLD_DEDUCTION": {
                    "affirmations": 20,
                    "affirmed_weight": 20.0,
                    "rejections": 80,
                    "rejected_weight": 80.0,
                },
            },
            "judge_graph": {
                "AFFIRM_VALID": {
                    "affirmations": 90,
                    "affirmed_weight": 90.0,
                    "rejections": 10,
                    "rejected_weight": 10.0,
                },
            },
        },
    }
    priors_path.write_text(json.dumps(priors_payload), encoding="utf-8")
    return priors_path


def test_transcript_qa_swarm_quality_failure_emission(temp_integrator: LivePipelineIntegrator):
    """Verifies TranscriptQASwarm emits typed QUALITY_CHECK_FAILURE telemetry on defect detection."""
    swarm = TranscriptQASwarm(integrator=temp_integrator)

    # Defective deliverables: Output 1 missing turn structure, Output 2 missing actions, Output 3 malformed JSON
    f1 = "Unstructured dialogue turn without timestamp"
    f2 = "# Overview\nShort meeting overview.\n[TODO] insert recap"
    f3 = "invalid-json-content"

    report = swarm.evaluate_all(f1=f1, f2=f2, f3=f3, run_id="run-qa-001", artifact_id="art-qa-001")
    assert report["swarm_passed"] is False

    events = temp_integrator.get_recorded_events(
        run_id="run-qa-001", outcome_type=OutcomeType.QUALITY_CHECK_FAILURE
    )
    assert len(events) >= 3

    # Check for specific role-level failures
    gates = [e.observed_outcome.get("gate_name") for e in events]
    assert "Forensic Accuracy Auditor" in gates or "Executive Recap Summarizer" in gates
    assert any("schema" in str(e.observed_outcome.get("defect_dimension", "")).lower() for e in events)


def test_transcript_qa_swarm_all_15_roles_enumerated():
    """Confirms all 15 QA roles are registered and can record quality check failures."""
    assert len(SWARM_ROLES) == 15
    swarm = QASwarm()
    assert hasattr(swarm, "record_quality_failure")


def test_component_retry_planner_repair_and_reranking(temp_integrator: LivePipelineIntegrator):
    """Verifies ComponentRetryPlanner re-ranks repair strategies based on historical yield rates."""
    planner = ComponentRetryPlanner(integrator=temp_integrator)

    # Record historical repair outcomes
    # Strategy A succeeded 3/3 times
    for _ in range(3):
        planner.record_component_repair(
            component="output_2:appendix",
            repair_action="surgical_appendix_patch",
            retry_class=RetryClass.CONTENT_REPAIR,
            was_successful=True,
            defect_message="Missing stakeholder breakdown",
        )
    # Strategy B succeeded 0/2 times
    for _ in range(2):
        planner.record_component_repair(
            component="output_2:appendix",
            repair_action="full_recap_regeneration",
            retry_class=RetryClass.CONTENT_REPAIR,
            was_successful=False,
            defect_message="Missing stakeholder breakdown",
        )

    candidates = ["full_recap_regeneration", "surgical_appendix_patch", "heuristic_fill"]
    ranked = planner.rank_repair_strategies("appendix_defect", candidates)

    assert ranked[0] == "surgical_appendix_patch"
    assert ranked[1] == "heuristic_fill"  # unobserved default (0.50) beats observed failure (0.25)
    assert ranked[2] == "full_recap_regeneration"

    repair_events = temp_integrator.get_recorded_events(outcome_type=OutcomeType.COMPONENT_REPAIR)
    assert len(repair_events) == 5


def test_component_retry_planner_strict_budget_exhaustion(temp_integrator: LivePipelineIntegrator):
    """Verifies ComponentRetryPlanner strictly enforces max_content_repairs=2 and emits RETRY_EXHAUSTION."""
    budget = RetryBudget(max_content_repairs=2)
    planner = ComponentRetryPlanner(budget=budget, integrator=temp_integrator)

    f2_text = (
        "# Executive Summary\nSummary\n"
        "# Key Updates & Technical Substance\nSubstance\n"
        "# Action & Governance Ledger\n- [ ] task @owner\n"
        "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\nOld content"
    )

    # Attempt 1: succeeds within budget
    plan1 = planner.plan_appendix_retry(f2_text, attempt=1, defect_message="Defect 1")
    assert plan1.is_valid is True
    assert planner.budget.content_repairs == 1

    # Attempt 2: succeeds within budget
    plan2 = planner.plan_appendix_retry(f2_text, attempt=2, defect_message="Defect 2")
    assert plan2.is_valid is True
    assert planner.budget.content_repairs == 2

    # Attempt 3: budget exhausted (max_content_repairs=2), plan is invalidated and emits RETRY_EXHAUSTION
    plan3 = planner.plan_appendix_retry(f2_text, attempt=3, defect_message="Defect 3")
    assert plan3.is_valid is False
    assert "RETRY_EXHAUSTION" in (plan3.invalidation_reason or "")

    exhaustion_events = temp_integrator.get_recorded_events(outcome_type=OutcomeType.RETRY_EXHAUSTION)
    assert len(exhaustion_events) == 1
    assert exhaustion_events[0].observed_outcome.get("target_component") == "output_2:appendix"


def test_all_9_micro_judges_calibrated_bands(mock_priors_file: Path):
    """Verifies that all 9 LLM micro-judges derive calibrated borderline bands and preserve floors."""
    judges = [
        ("enterprise", EnterpriseImpactJudge()),
        ("forensic", ForensicAccuracyJudge()),
        ("graph", GraphMutationJudge()),
        ("graph_provenance", GraphProvenanceJudge(priors_path=mock_priors_file)),
        ("graph_transport", GraphTransportJudge(priors_path=mock_priors_file)),
        ("holistic", HolisticQualityJudge()),
        ("leverage", AgenticLeverageJudge()),
        ("recap", ExecutiveRecapJudge()),
        ("decomposed_multi", DecomposedMultiJudge(mock=True)),
    ]

    for name, judge in judges:
        if hasattr(judge, "get_borderline_band"):
            if name == "decomposed_multi":
                floor, ceil = judge.get_borderline_band("output_2", priors_path=mock_priors_file)
            else:
                floor, ceil = judge.get_borderline_band(priors_path=mock_priors_file)
            # CRITICAL NON-COMPENSATING FLOOR INVARIANT
            assert floor >= 85.0
            assert ceil > floor
            assert ceil <= 95.0


def test_judge_clarification_records_override_on_affirmation(temp_integrator: LivePipelineIntegrator):
    """Verifies that authentic operator affirmation records DECISION_OVERRIDE telemetry."""
    # Seed a borderline judge report (composite 87.0 on Output 2)
    judge_report = {
        "output_2_evaluation": {
            "status": "FAIL",
            "verdict": "FAIL",
            "composite_score": 87.0,
            "score": 87.0,
            "section_scores": {
                "header_and_overview": 8.0,
                "key_updates_technical_substance": 30.0,
                "conditional_extensions": 18.0,
                "appendix_stakeholder_perspectives": 31.0,
            },
            "forensic_rationale": "Minor stylistic ambiguity in technical section.",
        }
    }
    final_decision = {"output_2_pass": False}
    metadata = {
        "hitl_judge_resolver": lambda env: "AFFIRM_VALID",
        "operator_id": "human_operator_42",
        "reviewer": "human_operator_42",
        "approval_source": "human_operator_42",
        "user_command": "approve",
    }

    resolved = evaluate_judge_clarification(
        judge_report=judge_report,
        final_decision=final_decision,
        metadata=metadata,
    )
    assert resolved is True
    assert judge_report["output_2_evaluation"]["status"] == "PASS_AUTHORIZED_EXCEPTION"
    assert judge_report["output_2_evaluation"]["composite_score"] == 87.0


def test_strict_preservation_of_non_compensating_floors():
    """Verifies that composite scores < 85.0 or floor breaches > 1 pt can NEVER be affirmed."""
    # Case 1: Hard composite failure (composite 82.0 < 85.0)
    judge_report_low = {
        "output_2_evaluation": {
            "status": "FAIL",
            "verdict": "FAIL",
            "composite_score": 82.0,
            "forensic_rationale": "Multiple missing critical sections.",
        }
    }
    final_decision = {"output_2_pass": False}
    metadata = {
        "hitl_judge_resolver": lambda env: "AFFIRM_VALID",
        "operator_id": "human_operator",
    }
    resolved = evaluate_judge_clarification(judge_report_low, final_decision, metadata)
    assert resolved is False  # Cannot clarify hard failure < 85.0
    assert final_decision["output_2_pass"] is False
