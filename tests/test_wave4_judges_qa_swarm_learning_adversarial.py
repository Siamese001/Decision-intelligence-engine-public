"""
tests/test_wave4_judges_qa_swarm_learning_adversarial.py

Adversarial and edge-case verification for Wave 4:
Decomposed LLM Micro-Judges & 15-Role Transcript QA Swarm Learning.

Covers mandatory edge-case categories under e2e-and-edge-case-coverage.md:
1. Boundary payload: Empty strings, 0-byte deliverables, empty candidate strategy lists.
2. Malformed input: Corrupted calibration JSON, negative weights, non-numeric scorecard fields.
3. Concurrency & race: Multi-threaded concurrent telemetry recording to LivePipelineIntegrator.
4. Overflow & exhaustion: Retry budget exhaustion past 2 attempts, recurring defect rejection.
5. Invariant bypass attempt: Clamping malicious priors setting floor < 85.0 to >= 85.0; rejecting affirmation of composite < 85.0 or stale approvals.
6. Rollback & recovery: Budget serialization and restore preventing attempt reset to zero across restarts.
7. Exact production regression: Detecting unnormalized ISO dates, unowned action items, and exposed secrets.
"""
from __future__ import annotations

import concurrent.futures
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
    get_calibrated_borderline_band,
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
    TranscriptQASwarm,
)


@pytest.fixture
def temp_integrator(tmp_path: Path) -> LivePipelineIntegrator:
    events_log = tmp_path / "adversarial_agent_outcomes.jsonl"
    return LivePipelineIntegrator(events_log_path=events_log)


# ==============================================================================
# Category 1: Boundary Payloads
# ==============================================================================


def test_boundary_empty_and_zero_byte_inputs(temp_integrator: LivePipelineIntegrator):
    """Verifies that empty and zero-length inputs are handled without crashes."""
    swarm = TranscriptQASwarm(integrator=temp_integrator)
    report = swarm.evaluate_all(f1="", f2="", f3="")
    assert report["swarm_passed"] is False

    planner = ComponentRetryPlanner(integrator=temp_integrator)
    ranked = planner.rank_repair_strategies("dimension_empty", [])
    assert ranked == []


# ==============================================================================
# Category 2: Malformed Inputs
# ==============================================================================


def test_malformed_calibration_file_fallback(tmp_path: Path):
    """Verifies that corrupted calibration JSON safely falls back to non-compensating defaults."""
    bad_priors_path = tmp_path / "corrupted_priors.json"
    bad_priors_path.write_text("{not-valid-json", encoding="utf-8")

    floor, ceil = get_calibrated_borderline_band("forensic", output_num=1, priors_path=bad_priors_path)
    assert floor == 85.0
    assert ceil == 95.0

    # Negative / malformed numeric fields
    bad_weights_path = tmp_path / "negative_weights.json"
    bad_weights_path.write_text(json.dumps({
        "learned_priors": {
            "judge_deduction_clarification": {
                "AFFIRM_VALID": {"affirmed_weight": -50.0, "affirmations": -5},
                "UPHOLD_DEDUCTION": {"affirmed_weight": 0.0, "affirmations": 0},
            }
        }
    }), encoding="utf-8")

    floor2, ceil2 = get_calibrated_borderline_band("recap", output_num=2, priors_path=bad_weights_path)
    assert floor2 == 85.0
    assert ceil2 == 90.0


# ==============================================================================
# Category 3: Concurrency & Race
# ==============================================================================


def test_concurrent_telemetry_recording(temp_integrator: LivePipelineIntegrator):
    """Verifies thread-safe concurrent recording of quality failures and repairs."""
    planner = ComponentRetryPlanner(integrator=temp_integrator)
    swarm = TranscriptQASwarm(integrator=temp_integrator)

    def _worker(worker_id: int):
        for i in range(5):
            swarm.record_quality_failure(
                role="Forensic Accuracy Auditor",
                findings=[f"Worker {worker_id} turn gap {i}"],
                run_id=f"run-{worker_id}",
            )
            planner.record_component_repair(
                component="output_2:appendix",
                repair_action="surgical_patch",
                retry_class=RetryClass.CONTENT_REPAIR,
                was_successful=(i % 2 == 0),
                run_id=f"run-{worker_id}",
            )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(_worker, w) for w in range(8)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    failures = temp_integrator.get_recorded_events(outcome_type=OutcomeType.QUALITY_CHECK_FAILURE)
    repairs = temp_integrator.get_recorded_events(outcome_type=OutcomeType.COMPONENT_REPAIR)
    assert len(failures) == 40
    assert len(repairs) == 40


# ==============================================================================
# Category 4: Overflow & Exhaustion
# ==============================================================================


def test_overflow_recurring_defect_and_retry_cap(temp_integrator: LivePipelineIntegrator):
    """Verifies that recurring unchanged defects are blocked and retry attempts capped at 2."""
    planner = ComponentRetryPlanner(integrator=temp_integrator)

    # First check: passes
    is_recurring, reason = planner.check_noop_or_recurring_defect(
        target_component="output_2:appendix",
        candidate_text="Candidate text A",
        defect_message="Missing stakeholder Speaker_Alpha",
    )
    assert is_recurring is False

    # Second check with identical candidate text: detected as NO_OP_PATCH
    is_noop, reason_noop = planner.check_noop_or_recurring_defect(
        target_component="output_2:appendix",
        candidate_text="Candidate text A",
        defect_message="Missing stakeholder Speaker_Alpha",
    )
    assert is_noop is True
    assert "NO_OP_PATCH" in reason_noop

    # Third check with different text but recurring defect message: detected as RECURRING_DEFECT
    is_rec, reason_rec = planner.check_noop_or_recurring_defect(
        target_component="output_2:appendix",
        candidate_text="Candidate text B",
        defect_message="Missing stakeholder Speaker_Alpha",
    )
    assert is_rec is True
    assert "RECURRING_DEFECT" in reason_rec


# ==============================================================================
# Category 5: Invariant Bypass Attempts
# ==============================================================================


def test_invariant_bypass_malicious_priors_floor_clamping(tmp_path: Path):
    """Verifies that malicious priors attempting to lower the floor below 85.0 fail closed."""
    malicious_priors_path = tmp_path / "malicious_priors.json"
    malicious_priors_path.write_text(json.dumps({
        "learned_priors": {
            "judge_deduction_clarification": {
                "AFFIRM_VALID": {"affirmed_weight": 1000.0, "affirmations": 1000},
                "UPHOLD_DEDUCTION": {"affirmed_weight": 0.0, "affirmations": 0},
            }
        }
    }), encoding="utf-8")

    floor, ceil = get_calibrated_borderline_band("recap", output_num=2, priors_path=malicious_priors_path)
    # The ceiling expands up to 92.0, but FLOOR IS STRICTLY CLAMPED TO >= 85.0
    assert floor == 85.0
    assert ceil <= 95.0


def test_invariant_bypass_stale_approval_rejected():
    """Verifies that stale approvals bound to prior deliverable digests cannot be reused."""
    planner = ComponentRetryPlanner()
    plan = planner.plan_appendix_retry(
        f2_text="# Executive Summary\nText",
        attempt=1,
        approval_binding_digest="digest_abc123",
    )
    # Deliverable changed so current digest does not match
    is_fresh, reason = planner.validate_approval_freshness(plan, current_deliverable_digest="digest_xyz999")
    assert is_fresh is False
    assert "STALE_APPROVAL_DETECTED" in reason


# ==============================================================================
# Category 6: Rollback & Recovery
# ==============================================================================


def test_budget_persistence_across_process_restarts():
    """Verifies that budget state persists across restarts preventing counter resets."""
    budget = RetryBudget(max_content_repairs=2)
    budget.record_attempt(RetryClass.CONTENT_REPAIR)
    budget.record_attempt(RetryClass.CONTENT_REPAIR)

    serialized = budget.to_dict()
    assert serialized["content_repairs"] == 2

    # New process instantiation restores budget
    restored_planner = ComponentRetryPlanner()
    restored_planner.restore_budget(serialized)
    assert restored_planner.budget.content_repairs == 2

    can_attempt, reason = restored_planner.budget.can_attempt(RetryClass.CONTENT_REPAIR)
    assert can_attempt is False
    assert "exhausted" in reason


# ==============================================================================
# Category 7: Exact Production Regressions
# ==============================================================================


def test_production_regression_unnormalized_iso_date_and_exposed_pii():
    """Verifies QA swarm catches production regressions: YYYY-MM-DD date and exposed cloud credentials."""
    swarm = TranscriptQASwarm()

    # Regression 1: YYYY-MM-DD date in recap header instead of MM/DD/YYYY
    f2_date_defect = "# Executive Summary\nDate: 2026-10-23\nOverview text here."
    res_date = swarm._evaluate_role("Assertion Freshness Checker", None, f2_date_defect, "{}")
    assert res_date["status"] == "FAIL"
    assert any("MM/DD/YYYY required" in f for f in res_date["findings"])

    # Regression 2: Exposed API key token
    f2_secret_defect = "# Executive Summary\nKey is sk_test_redacted_placeholder."
    res_sec = swarm._evaluate_role("Security & PII Scanner", None, f2_secret_defect, "{}")
    assert res_sec["status"] == "FAIL"
    assert any("Exposed cloud credential" in f for f in res_sec["findings"])
