"""
Wave 3 E2E Test: Adversarial LLM Judge, Position-Swapped Batching, and Calibration Protocol.
Tests end-to-end integration of DecomposedMultiJudge, BoundedAdversarialProbe,
BatchedJudgeEvaluator position-swap verification, and calibration metrics.
"""

import json
import pytest
from dual_pipeline.llm_judge import DecomposedMultiJudge
from dual_pipeline.llm_judge_core.batch_judge import BatchMicroJudgeEvaluator
from dual_pipeline.llm_judge_core.calibration_protocol import (
    CalibrationCaseOutcome,
    PredeclaredTestCase,
    compute_expected_calibration_error,
    evaluate_calibration_cohort,
)
from dual_pipeline.llm_judge_core.judge_clarification import (
    evaluate_judge_clarification,
)
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality


@pytest.fixture
def clean_fixtures():
    date = "09/10/2026"
    f1 = f"""# APEX_TRANS Tech Services Discovery -- HITL-Resolved Verbatim Transcript

**Date:** {date}  
**Time:** 03:34 PM - 05:00 PM ET  
**Platform:** Microsoft Teams (Apex Global Enterprises)  
**Participants:**
- **Apex Energy Transmission (APEX_TRANS):** Alex Rivera (Technology Lead), Jordan Taylor (Technical Services Lead)
- **PartnerAdvisory:** Anece Elakkari (Solutions Lead)

## Ambiguity Lock Register
| ID | Status | Category | Scope | Resolution | Confidence |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `ALR-01` | `RESOLVED` | Speaker Attribution | Turn [00:00:25] | Reconciled roles for Alex Rivera and Jordan Taylor. | 98% |

## Transcript Content

[00:00:25] **Alex Rivera**: Tenika is technical services and I am the technology lead.

[00:46:22] **Jordan Taylor**: Technical services is identifying the root cause.
"""
    f2 = f"""# Subject: Executive Recap: APEX_TRANS Tech Services Discovery ({date})

**Date:** {date}  
**Time:** 03:34 PM - 05:00 PM ET  
**Attendees:**
- **Apex Energy Transmission (APEX_TRANS):** Alex Rivera (Technology Lead), Jordan Taylor (Technical Services Lead)
- **PartnerAdvisory:** Anece Elakkari (Solutions Lead)

---

## Meeting Objective
Establish operational baseline across APEX_TRANS systems.

## Key Discussion Points & Findings
- **Governance Split:** Alex Rivera leads evaluations while Jordan Taylor oversees technical services [00:00:25].

## Next Steps & Action Items
| # | Action Item | Owner | Target Date | Priority | Epistemic Status |
|---|---|---|---|---|---|
| 1 | Conduct live architecture walkthrough | Alex Rivera | 09/11/2026 | HIGH | Grounded |

## Open Decisions / Blockers
- **Integration Milestone Selection Gate (09/25/2026):** Executive decision owner to select milestone.

## Business Meaning and Value Case
Consolidating telemetry reduces operator cognitive fatigue.

## Executive Decision
Alex Rivera committed to walk through monitoring systems on 09/11/2026.

## Bounded Delivery Slice
Candidate slice: Automated telemetry correlation.
"""
    f3 = {
        "schema_version": "5.7",
        "meeting_id": "meeting-e2e-pos",
        "title": "APEX_TRANS Tech Services Discovery",
        "canonical_topic": "APEX_TRANS Tech Services Discovery",
        "classification": "confidential",
        "audiences": ["nee-engineering", "partner_advisory-leadership"],
        "observed_at": date,
        "evidence_index": {
            "ev-01": {
                "speaker": "person:mitchell-simmons",
                "source_classification": "technical",
                "text": "Alex Rivera leads technology evaluations.",
                "exact_text": "Tenika is technical services and I am the technology lead.",
                "locator": {"kind": "timestamp", "value": "00:00:25"},
            }
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "org:apex_trans", "type": "org", "name": "Apex Energy Transmission", "mutation": "create", "observed_at": date},
                {"id": "person:mitchell-simmons", "type": "person", "name": "Alex Rivera", "mutation": "create", "observed_at": date},
            ],
            "relationship_signals": [
                {"signal_id": "sig-01", "from_id": "person:mitchell-simmons", "to_id": "org:apex_trans", "relationship": "CONTRIBUTES_TO", "evidence_ids": ["ev-01"]},
            ],
            "action_updates": [],
            "decision_updates": [],
            "risk_updates": [],
            "proposal_updates": [],
        },
        "evidence_only_statement_ids": [],
    }
    raw = "[00:00:25] Alex Rivera: Tenika is technical services and I am the technology lead.\n\n[00:46:22] Jordan Taylor: Technical services is identifying the root cause.\n"
    meta = {
        "meeting_name": "NEET_Tech_Services_Discovery",
        "date": date,
        "time": "03:34 PM - 05:00 PM ET",
        "classification": "confidential",
        "audiences": ["nee-engineering", "partner_advisory-leadership"],
    }
    return {"f1": f1, "f2": f2, "f3": f3, "raw": raw, "meta": meta}


def test_e2e_evaluate_parallel_clean_pass(clean_fixtures):
    """Verifies complete end-to-end evaluation pass across Outputs 01, 02, and 03."""
    judge = DecomposedMultiJudge(mock=True)
    res = judge.evaluate_parallel(
        output_1_text=clean_fixtures["f1"],
        output_2_text=clean_fixtures["f2"],
        output_3_text=json.dumps(clean_fixtures["f3"]),
        raw_transcript=clean_fixtures["raw"],
        metadata=clean_fixtures["meta"],
    )

    assert res["status"] == "PASS"
    assert res["composite_score"] >= 85.0
    assert res["rubric_version"] == "2.0"
    assert res["output_1_evaluation"]["verdict"] == "PASS"
    assert res["output_2_evaluation"]["verdict"] == "PASS"
    assert res["output_3_evaluation"]["verdict"] == "PASS"
    assert res["cross_artifact_reconciliation"]["passed"] is True
    assert res["adversarial_audit"]["hard_veto"] is False


def test_e2e_position_swapped_batch_evaluation(clean_fixtures):
    """Verifies that BatchMicroJudgeEvaluator conducts position-swapped validation without ordering bias."""
    batch_evaluator = BatchMicroJudgeEvaluator(mock=True, max_batch_size=5)

    nodes = [
        {"id": "node_01", "name": "Item 1", "content": "PartnerAdvisory strategy wedge", "political_materiality": 9},
        {"id": "node_02", "name": "Item 2", "content": "APEX_TRANS insights telemetry", "political_materiality": 8},
    ]

    batch_result = batch_evaluator.evaluate_batch(
        nodes=nodes,
        reference_text=clean_fixtures["raw"],
        verify_position_swap=True,
        mock=True,
    )

    assert batch_result.position_swap_verified is True
    assert batch_result.position_bias_detected is False
    assert batch_result.swap_consistency_rate == 1.0
    assert len(batch_result.evaluated_items) == 2


def test_e2e_calibration_protocol_and_ece():
    """Verifies calibration cohort report aggregation and Expected Calibration Error computation."""
    outcomes = [
        CalibrationCaseOutcome(
            case_id=f"case_ta_{i}",
            human_label="PASS",
            judge_verdict="PASS",
            composite_scores={"composite": 96.0},
            outcome_category="TRUE_ACCEPTANCE",
            calls_made=3,
            tokens_consumed=1200,
        )
        for i in range(10)
    ] + [
        CalibrationCaseOutcome(
            case_id=f"case_tr_{i}",
            human_label="FAIL",
            judge_verdict="FAIL",
            composite_scores={"composite": 30.0},
            outcome_category="TRUE_REJECTION",
            calls_made=3,
            tokens_consumed=1100,
        )
        for i in range(20)
    ]

    report = evaluate_calibration_cohort(outcomes)
    assert report.total_cases == 30
    assert report.true_acceptance_count == 10
    assert report.true_rejection_count == 20
    assert report.false_acceptance_rate == 0.0
    assert report.false_rejection_rate == 0.0

    confidences = [0.96] * 10 + [0.98] * 20
    actuals = [1.0] * 30
    ece = compute_expected_calibration_error(confidences, actuals)
    assert ece <= 0.05


def test_e2e_judge_clarification_neutral_priors():
    """Verifies evaluate_judge_clarification handles borderline scores, neutral priors, and fail-closed security."""
    judge_report = {
        "output_2_evaluation": {
            "status": "FAIL",
            "verdict": "FAIL",
            "composite_score": 88.0,
            "is_valid_scorecard": True,
            "executive_rationale": "Borderline executive altitude deduction (88/100).",
        }
    }
    final_decision = {
        "output_1_pass": True,
        "output_2_pass": False,
        "output_3_pass": True,
        "attempt": 3,
        "max_attempts": 3,
    }
    metadata = {
        "meeting_name": "Test_Discovery",
        "approval_source": "human_operator",
        "prior_user_command": "approve implementation",
    }

    # 1. Affirmation by operator upgrades to PASS_AUTHORIZED_EXCEPTION
    resolved = evaluate_judge_clarification(
        judge_report=judge_report,
        final_decision=final_decision,
        metadata=metadata,
        interactive_resolver=lambda env: "AFFIRM_VALID",
    )
    assert resolved is True
    assert final_decision["output_2_pass"] is True
    assert judge_report["output_2_evaluation"]["verdict"] == "PASS_AUTHORIZED_EXCEPTION"
    assert judge_report["output_2_evaluation"]["composite_score"] == 88.0

    # 2. When resolver is absent, fails closed (UPHOLD_DEDUCTION)
    report_unresolved = {
        "output_2_evaluation": {
            "status": "FAIL",
            "verdict": "FAIL",
            "composite_score": 87.0,
            "is_valid_scorecard": True,
            "executive_rationale": "Borderline deduction without operator.",
        }
    }
    decision_unresolved = {
        "output_1_pass": True,
        "output_2_pass": False,
        "output_3_pass": True,
        "attempt": 3,
        "max_attempts": 3,
    }
    unresolved_status = evaluate_judge_clarification(
        judge_report=report_unresolved,
        final_decision=decision_unresolved,
        metadata=metadata,
        interactive_resolver=None,
    )
    assert unresolved_status is False
    assert decision_unresolved["output_2_pass"] is False
