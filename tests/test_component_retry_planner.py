"""Unit tests for Wave 5 Component Retry Planner and Dependency Invalidation.

Tests:
1. Scoped appendix-only retry planning with protected baseline hashes.
2. Transcript speaker correction invalidation across downstream recap and graph criteria.
3. Protected section invariance verification and mutation rejection.
4. Retry budget tracking across distinct retry classes (Transport, Evaluator, Content).
5. Halting on no-op repairs and recurring unchanged defects.
6. Surgical patcher rejecting missing targets and duplicate headings.
"""
from __future__ import annotations

import pytest

from dual_pipeline.transcript_pipeline_core.component_retry_planner import (
    ComponentPlan,
    ComponentRetryPlanner,
    RetryBudget,
    RetryClass,
)
from dual_pipeline.transcript_qa_core.surgical_patcher import (
    ProtectedPathMutationError,
    SurgicalPatcher,
)


def _sample_recap() -> str:
    return """# Subject: APEX_TRANS Discovery
**Date:** 09/10/2026
**Attendees:**
- Alice (Lead)

# Meeting Objective
Align on vision for Insights Hub across wind, solar, and transmission.

# Key Discussion Points & Findings
Data is siloed across T&D domains with legacy SharePoint files.

# Strategic Decisions & Conditional Recommendations
Architecture decision to adopt event-driven streaming.

# Action & Governance Ledger
| # | Action Item | DRI | Target Date | Success Measure |
|---|---|---|---|---|
| 1 | Align on integration scope | Alice | 09/25/2026 | Document approved |

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown
Alice noted that the current streaming pipeline has 200ms latency.
"""


def test_appendix_retry_plan_targets_only_appendix():
    planner = ComponentRetryPlanner()
    plan = planner.plan_appendix_retry(_sample_recap())

    assert plan.retry_class == RetryClass.CONTENT_REPAIR
    assert plan.target_component == "output_2:appendix"
    assert "appendix_stakeholder_perspectives" in plan.affected_criteria
    assert "output_1" in plan.protected_paths
    assert "output_3" in plan.protected_paths
    assert any("appendix" in k for k in plan.baseline_hashes)


def test_speaker_attribution_invalidates_downstream_criteria():
    planner = ComponentRetryPlanner()
    plan = planner.plan_dependency_invalidation(
        event_type="SPEAKER_CORRECTION",
        changed_artifact="output_1",
        detail="Corrected turn attribution from Bob to Alice",
    )

    assert plan.retry_class == RetryClass.DEPENDENCY_REVALIDATION
    assert plan.target_component == "output_1:speakers"
    assert "output_2:dimension_2_technical_substance" in plan.affected_criteria
    assert "output_2:appendix_stakeholder_perspectives" in plan.affected_criteria
    assert "output_3:entities" in plan.affected_criteria
    assert "output_3:decisions" in plan.affected_criteria
    assert "Transcript speaker correction" in (plan.dependency_reason or "")


def test_invariance_verification_detects_mutation_in_protected_section():
    planner = ComponentRetryPlanner()
    recap = _sample_recap()
    plan = planner.plan_appendix_retry(recap)

    current_hashes = dict(plan.baseline_hashes)
    # Modify appendix hash (allowed)
    for k in current_hashes:
        if "appendix" in k:
            current_hashes[k] = "new_appendix_hash_12345"

    ok, violations = planner.verify_protected_component_invariance(plan, current_hashes)
    assert ok is True
    assert len(violations) == 0

    # Tamper with protected section
    for k in current_hashes:
        if "header_and_overview" in k or "meeting_objective" in k or "overview" in k:
            current_hashes[k] = "tampered_objective_hash"
            break

    ok, violations = planner.verify_protected_component_invariance(plan, current_hashes)
    assert ok is False
    assert any("was mutated" in v for v in violations)


def test_retry_budget_enforces_distinct_limits():
    budget = RetryBudget(
        max_total_attempts=4,
        max_content_repairs=2,
        max_evaluator_repairs=1,
        max_transport_retries=2,
    )

    # Content repair 1
    can, _ = budget.can_attempt(RetryClass.CONTENT_REPAIR)
    assert can is True
    budget.record_attempt(RetryClass.CONTENT_REPAIR)

    # Content repair 2
    can, _ = budget.can_attempt(RetryClass.CONTENT_REPAIR)
    assert can is True
    budget.record_attempt(RetryClass.CONTENT_REPAIR)

    # Content repair 3 (should fail due to content limit)
    can, reason = budget.can_attempt(RetryClass.CONTENT_REPAIR)
    assert can is False
    assert "Content repair budget exhausted" in reason

    # Evaluator repair (should be allowed)
    can, _ = budget.can_attempt(RetryClass.EVALUATOR_REPAIR)
    assert can is True
    budget.record_attempt(RetryClass.EVALUATOR_REPAIR)

    # Transport retry (should be allowed: total 3 < 4)
    can, _ = budget.can_attempt(RetryClass.TRANSPORT)
    assert can is True
    budget.record_attempt(RetryClass.TRANSPORT)

    # Global limit hit (4/4)
    can, reason = budget.can_attempt(RetryClass.TRANSPORT)
    assert can is False
    assert "Global retry budget exhausted" in reason


def test_noop_patch_and_recurring_defect_detection():
    planner = ComponentRetryPlanner()
    target = "output_2:appendix"
    initial_text = "Initial appendix content"

    # First attempt: records candidate hash
    is_halt, _ = planner.check_noop_or_recurring_defect(target, initial_text, "Missing evidence")
    assert is_halt is False

    # Second attempt with identical text (no-op patch)
    is_halt, reason = planner.check_noop_or_recurring_defect(target, initial_text, "Missing evidence")
    assert is_halt is True
    assert "NO_OP_PATCH" in reason

    # Attempt with changed text but recurring identical defect
    is_halt, reason = planner.check_noop_or_recurring_defect(target, "New appendix text", "Missing evidence")
    assert is_halt is True
    assert "RECURRING_DEFECT" in reason


def test_surgical_patcher_rejects_missing_target():
    doc = "# Subject: Test\n## Section A\nBody A"
    with pytest.raises(ValueError, match="missing-target insertion rejected"):
        SurgicalPatcher.apply_surgical_patch(
            original_text=doc,
            target_section="Nonexistent Section",
            new_section_content="New body",
        )


def test_surgical_patcher_rejects_duplicate_target_headings():
    doc = "# Subject: Test\n## Target\nBody 1\n## Target\nBody 2"
    with pytest.raises(ValueError, match="Duplicate target heading"):
        SurgicalPatcher.apply_surgical_patch(
            original_text=doc,
            target_section="Target",
            new_section_content="New body",
        )
