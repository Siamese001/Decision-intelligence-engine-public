"""Adversarial and edge-case tests for Wave 5 component-level retry governance.

Covers mandatory edge-case categories:
1. Boundary payload: empty target paths, zero-length patch replacements, 0-byte candidate components.
2. Malformed input: duplicate section headings, missing-target insertion, special regex metacharacters.
3. Overflow & exhaustion: retry budget depletion, global counter ceilings, no-op patches, recurring defects.
4. Invariant bypass attempts: patching protected paths, unauthorized cross-deliverable mutation, post-heal drift.
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


# =========================================================================
# Category 1: Boundary Payloads
# =========================================================================

def test_boundary_empty_document_and_targets():
    """Empty or whitespace document or target should reject missing-target insertion."""
    with pytest.raises(ValueError, match="missing-target insertion rejected"):
        SurgicalPatcher.apply_surgical_patch(
            original_text="",
            target_section="Bounded Execution Wedge",
            new_section_content="Some repair content",
        )

    with pytest.raises(ValueError, match="missing-target insertion rejected"):
        SurgicalPatcher.apply_surgical_patch(
            original_text="## Valid Section\nContent here.\n",
            target_section="",
            new_section_content="Some repair content",
        )


def test_boundary_zero_length_patch_replacement():
    """Empty patch replacement should cleanly empty the target body while preserving other sections."""
    doc = (
        "# Title\n\n"
        "## Alpha Section\nAlpha body text.\n\n"
        "## Beta Section\nBeta body text.\n"
    )
    patched, audit = SurgicalPatcher.apply_surgical_patch(
        original_text=doc,
        target_section="Beta Section",
        new_section_content="",
        protected_sections=["Alpha Section"],
    )
    assert "## Alpha Section\nAlpha body text." in patched
    assert "## Beta Section\n\n" in patched
    assert "Beta body text" not in patched
    assert audit["target_found"] is True


def test_boundary_zero_and_minimal_budgets():
    """Zero-budget configuration immediately denies attempts with descriptive reason."""
    zero_budget = RetryBudget(max_total_attempts=0)
    can_try, reason = zero_budget.can_attempt(RetryClass.CONTENT_REPAIR)
    assert can_try is False
    assert "Global retry budget exhausted" in reason

    zero_content = RetryBudget(max_content_repairs=0)
    can_try, reason = zero_content.can_attempt(RetryClass.CONTENT_REPAIR)
    assert can_try is False
    assert "Content repair budget exhausted" in reason

    # But transport is still open in zero_content
    can_transport, _ = zero_content.can_attempt(RetryClass.TRANSPORT)
    assert can_transport is True


def test_boundary_empty_f2_for_appendix_plan():
    """Empty document passed to appendix retry planner yields valid plan without error."""
    planner = ComponentRetryPlanner()
    plan = planner.plan_appendix_retry(f2_text="")
    assert plan.target_component == "output_2:appendix"
    assert "output_1" in plan.protected_paths
    assert len(plan.baseline_hashes) == 0


# =========================================================================
# Category 2: Malformed Input
# =========================================================================

def test_malformed_duplicate_target_headings():
    """Duplicate target headings must be rejected to prevent ambiguous or partial patching."""
    doc = (
        "## Bounded Execution Wedge\nFirst occurrence body.\n\n"
        "## Bounded Execution Wedge\nDuplicate occurrence body.\n"
    )
    with pytest.raises(ValueError, match="Duplicate target heading"):
        SurgicalPatcher.apply_surgical_patch(
            original_text=doc,
            target_section="Bounded Execution Wedge",
            new_section_content="Patched content",
        )


def test_malformed_missing_target_heading():
    """Non-existent target section must be rejected without creating phantom sections."""
    doc = "## Existing Section\nBody.\n"
    with pytest.raises(ValueError, match="Target section 'NonExistent' not found in artifact"):
        SurgicalPatcher.apply_surgical_patch(
            original_text=doc,
            target_section="NonExistent",
            new_section_content="Patched content",
        )


def test_malformed_special_regex_characters_in_heading():
    """Target headings with brackets, asterisks, or regex symbols should match safely."""
    doc = (
        "## Bounded [Execution] * Wedge (Risk + Scope?)\nOriginal body.\n\n"
        "## Next Section\nNext body.\n"
    )
    patched, audit = SurgicalPatcher.apply_surgical_patch(
        original_text=doc,
        target_section="Bounded [Execution] * Wedge (Risk + Scope?)",
        new_section_content="Repaired regex-safe body.",
        protected_sections=["Next Section"],
    )
    assert audit["target_found"] is True
    assert "Repaired regex-safe body." in patched
    assert "Next body." in patched


# =========================================================================
# Category 3: Overflow & Exhaustion
# =========================================================================

def test_exhaustion_individual_retry_class_budgets():
    """Each retry class adheres to its specific budget limit."""
    budget = RetryBudget(
        max_total_attempts=10,
        max_content_repairs=2,
        max_evaluator_repairs=1,
        max_transport_retries=3,
    )

    # Exhaust evaluator repair
    assert budget.can_attempt(RetryClass.EVALUATOR_REPAIR)[0] is True
    budget.record_attempt(RetryClass.EVALUATOR_REPAIR)
    can_try, reason = budget.can_attempt(RetryClass.EVALUATOR_REPAIR)
    assert can_try is False
    assert "Evaluator repair budget exhausted" in reason

    # Content repair still has budget
    assert budget.can_attempt(RetryClass.CONTENT_REPAIR)[0] is True
    budget.record_attempt(RetryClass.CONTENT_REPAIR)
    budget.record_attempt(RetryClass.CONTENT_REPAIR)
    can_try, reason = budget.can_attempt(RetryClass.CONTENT_REPAIR)
    assert can_try is False
    assert "Content repair budget exhausted" in reason

    # Transport still has budget
    assert budget.can_attempt(RetryClass.TRANSPORT)[0] is True


def test_exhaustion_global_retry_budget():
    """Global ceiling blocks further retries even if individual classes have room."""
    budget = RetryBudget(
        max_total_attempts=3,
        max_content_repairs=5,
        max_transport_retries=5,
    )
    budget.record_attempt(RetryClass.CONTENT_REPAIR)
    budget.record_attempt(RetryClass.CONTENT_REPAIR)
    budget.record_attempt(RetryClass.TRANSPORT)

    # Total is 3/3
    can_try, reason = budget.can_attempt(RetryClass.CONTENT_REPAIR)
    assert can_try is False
    assert "Global retry budget exhausted (3/3)" in reason


def test_exhaustion_noop_and_recurring_defect_detection():
    """Repeated identical repair output or recurring identical defect stops loop."""
    planner = ComponentRetryPlanner()

    # First attempt: defect logged
    is_stop, msg = planner.check_noop_or_recurring_defect(
        target_component="output_2:appendix",
        candidate_text="Initial repair text",
        defect_message="Missing stakeholder perspective for Alice",
    )
    assert is_stop is False
    assert msg == "OK"

    # Second attempt with IDENTICAL candidate text -> NO_OP_PATCH
    is_stop, msg = planner.check_noop_or_recurring_defect(
        target_component="output_2:appendix",
        candidate_text="Initial repair text",
        defect_message="Different defect message",
    )
    assert is_stop is True
    assert "NO_OP_PATCH" in msg

    # Third attempt with DIFFERENT text but SAME original defect message -> RECURRING_DEFECT
    is_stop, msg = planner.check_noop_or_recurring_defect(
        target_component="output_2:appendix",
        candidate_text="Second repair text with new words",
        defect_message="Missing stakeholder perspective for Alice",
    )
    assert is_stop is True
    assert "RECURRING_DEFECT" in msg


# =========================================================================
# Category 4: Invariant Bypass Attempts
# =========================================================================

def test_bypass_attempt_patch_declared_protected_path():
    """Attempting to target a section declared as protected must fail closed immediately."""
    doc = (
        "## Executive Summary\nProtected summary.\n\n"
        "## Key Updates\nUpdates body.\n"
    )
    # Direct match
    with pytest.raises(ProtectedPathMutationError, match="declared as a protected path"):
        SurgicalPatcher.apply_surgical_patch(
            original_text=doc,
            target_section="Executive Summary",
            new_section_content="Hacked summary",
            protected_sections=["Executive Summary", "Key Updates"],
        )

    # Substring / envelope formatted match
    with pytest.raises(ProtectedPathMutationError, match="declared as a protected path"):
        SurgicalPatcher.apply_surgical_patch(
            original_text=doc,
            target_section="sections['Executive Summary']",
            new_section_content="Hacked summary",
            protected_sections=["Executive Summary"],
        )


def test_bypass_attempt_mutate_protected_sections_during_healing():
    """If deterministic healer alters a protected section, patcher must raise ProtectedPathMutationError."""
    # Build text where Key Updates has malformed syntax that healer would alter,
    # but Key Updates is explicitly marked protected.
    doc = (
        "# Meeting Overview\n\n"
        "## Key Updates (Technical Substance)\n"
        "- Alice said something.\n\n"
        "## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n"
        "Old appendix body.\n"
    )
    # When patching appendix, if Key Updates was tampered with in new_sections, verification catches it.
    original_map = {"key updates (technical substance)": "- Alice said something.\n\n"}
    # Direct test of _verify_invariance
    from dual_pipeline.transcript_qa_core.surgical_patcher import _verify_invariance
    with pytest.raises(ProtectedPathMutationError, match="Invariance violation"):
        _verify_invariance(
            protected=["key updates (technical substance)"],
            original_map=original_map,
            new_sections=[("Key Updates (Technical Substance)", "- Mutated text!\n\n")],
            normalize_fn=SurgicalPatcher.normalize_section_name,
        )


def test_bypass_attempt_unauthorized_cross_deliverable_mutation():
    """Verify that verify_protected_component_invariance catches changes in non-target sections."""
    planner = ComponentRetryPlanner()
    baseline = {
        "header": "hash_head",
        "key_updates_technical_substance": "hash_orig_ku",
        "appendix_detailed_stakeholder_perspectives": "hash_orig_app",
    }
    plan = ComponentPlan(
        retry_class=RetryClass.CONTENT_REPAIR,
        target_component="output_2:appendix",
        writable_paths=["# Appendix"],
        protected_paths=["key_updates_technical_substance"],
        affected_criteria=["appendix_stakeholder_perspectives"],
        baseline_hashes=baseline,
    )

    # Tampered non-appendix section
    tampered_hashes = {
        "header": "hash_head",
        "key_updates_technical_substance": "hash_TAMPERED",
        "appendix_detailed_stakeholder_perspectives": "hash_NEW_APP",  # expected to change
    }

    ok, violations = planner.verify_protected_component_invariance(plan, tampered_hashes)
    assert ok is False
    assert len(violations) == 1
    assert "key_updates_technical_substance" in violations[0]
