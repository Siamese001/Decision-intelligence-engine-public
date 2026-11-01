#!/usr/bin/env python3
"""Unit tests for wave-based implementation plan governance and validator.

Verifies:
1. Compliant wave-based implementation plans pass validation.
2. Flat or un-wavered plans are rejected with actionable diagnostics.
3. Missing mandatory subsections (Milestones, Acceptance Criteria, Receipt Gate) fail.
4. Non-consecutive wave numbering fails closed.
5. Legacy exemptions are respected.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from tools.validate_implementation_plan import (
    DEFAULT_LEGACY_EXEMPTIONS,
    validate_file,
    validate_plan_content,
)

ROOT = Path(__file__).resolve().parents[1]


class ImplementationPlanGovernanceTests(unittest.TestCase):
    def test_compliant_wave_plan_passes(self) -> None:
        """Verify that a properly structured wave-based plan passes validation."""
        valid_plan = """# Implementation Plan: Example Feature

## Wave 1: Core Foundation & Tooling
### Objective & Scope
Establish foundation and core settings.

### Milestones & Deliverables
- [NEW] `tools/example_tool.py`
- [MODIFY] `config/settings.py`

### Acceptance Criteria
- Representative input produces the declared output and expected sandbox state.
- Boundary tests pass; linter reports zero warnings.

### Runtime Receipt & Completion Gate
- Run: `python -m example --input fixtures/sample.json`
- Input: Representative input from `fixtures/sample.json`.
- Expected: Example output contains the expected decision and updates only the sandbox graph.
- Verify independently: Read the produced output and query the sandbox graph for the expected state.
- Fail if: Output or graph state differs from the expected result, or any declared criterion fails.
- Record: Runtime, input digest, command, run ID, raw output, independent query result, and timestamp.

## Wave 2: Downstream Integration
### Objective & Scope
Integrate with dual pipeline components.

### Milestones & Deliverables
- [NEW] `dual_pipeline/integration.py`

### Acceptance Criteria
- The end-to-end integration run returns the expected response and keeps the active generation unchanged.

### Runtime Receipt & Completion Gate
- Run: `python -m example.integrate --input fixtures/integration.json`
- Input: Representative integration case from `fixtures/integration.json`.
- Expected: The integrated workflow returns the expected response and preserves the active generation.
- Verify independently: Inspect the response and read back the generation through the supported query path.
- Fail if: Either observed result does not match the declared expectation.
- Record: Runtime, input digest, command, run ID, raw output, independent readback, and timestamp.
"""
        is_valid, errors = validate_plan_content(valid_plan, "valid_plan.md")
        self.assertTrue(is_valid, f"Plan should be valid, but failed with: {errors}")
        self.assertEqual(len(errors), 0)

    def test_flat_monolithic_plan_is_rejected(self) -> None:
        """Verify that plans without wave decomposition are rejected."""
        flat_plan = """# Unstructured Implementation Plan

## Proposed Changes
We will modify several files in the repository to add features.
- Edit file A
- Edit file B

## Verification Plan
Run tests.
"""
        is_valid, errors = validate_plan_content(flat_plan, "flat_plan.md")
        self.assertFalse(is_valid)
        self.assertTrue(
            any("Implementation plan must be wave-based" in err for err in errors),
            f"Expected wave-based error message, got: {errors}",
        )

    def test_missing_title_is_rejected(self) -> None:
        """Verify that plans without a top-level title are rejected."""
        no_title_plan = """## Wave 1: Initial Setup
### Objective & Scope
Initial setup scope.

### Milestones & Deliverables
- [NEW] `file.py`

### Acceptance Criteria
- Tests pass.

### Runtime Receipt & Completion Gate
- Receipt verified.
"""
        is_valid, errors = validate_plan_content(no_title_plan, "no_title.md")
        self.assertFalse(is_valid)
        self.assertTrue(any("Missing required top-level title" in err for err in errors))

    def test_missing_objective_is_rejected(self) -> None:
        """Verify that waves missing Objective & Scope are rejected."""
        missing_objective = """# Plan Title

## Wave 1: Setup
### Milestones & Deliverables
- [NEW] `file.py`

### Acceptance Criteria
- Criteria met.

### Runtime Receipt & Completion Gate
- Receipt confirmed.
"""
        is_valid, errors = validate_plan_content(missing_objective, "missing_objective.md")
        self.assertFalse(is_valid)
        self.assertTrue(any("Objective & Scope" in err for err in errors))

    def test_missing_milestones_is_rejected(self) -> None:
        """Verify that waves missing Milestones & Deliverables are rejected."""
        missing_milestones = """# Plan Title

## Wave 1: Setup
### Objective & Scope
Setup scope.

### Acceptance Criteria
- Criteria met.

### Runtime Receipt & Completion Gate
- Receipt confirmed.
"""
        is_valid, errors = validate_plan_content(missing_milestones, "missing_milestones.md")
        self.assertFalse(is_valid)
        self.assertTrue(any("Milestones & Deliverables" in err for err in errors))

    def test_empty_milestones_items_is_rejected(self) -> None:
        """Verify that wave plans with empty milestones sections fail."""
        empty_milestones = """# Plan Title

## Wave 1: Setup
### Objective & Scope
Setup scope.

### Milestones & Deliverables
No items specified here just free text with no deliverables.

### Acceptance Criteria
- Criteria met.

### Runtime Receipt & Completion Gate
- Receipt confirmed.
"""
        is_valid, errors = validate_plan_content(empty_milestones, "empty_milestones.md")
        self.assertFalse(is_valid)
        self.assertTrue(any("must contain at least one itemized output" in err for err in errors))

    def test_missing_acceptance_criteria_is_rejected(self) -> None:
        """Verify that waves missing Acceptance Criteria are rejected."""
        missing_criteria = """# Plan Title

## Wave 1: Setup
### Objective & Scope
Setup scope.

### Milestones & Deliverables
- [NEW] `file.py`

### Runtime Receipt & Completion Gate
- Receipt confirmed.
"""
        is_valid, errors = validate_plan_content(missing_criteria, "missing_criteria.md")
        self.assertFalse(is_valid)
        self.assertTrue(any("Acceptance Criteria" in err for err in errors))

    def test_missing_runtime_receipt_is_rejected(self) -> None:
        """Verify that waves missing Runtime Receipt / Completion Gate fail."""
        missing_receipt = """# Plan Title

## Wave 1: Setup
### Objective & Scope
Setup scope.

### Milestones & Deliverables
- [NEW] `file.py`

### Acceptance Criteria
- Passes.
"""
        is_valid, errors = validate_plan_content(missing_receipt, "missing_receipt.md")
        self.assertFalse(is_valid)
        self.assertTrue(any("Runtime Receipt & Completion Gate" in err for err in errors))

    def test_non_consecutive_waves_rejected(self) -> None:
        """Verify that gaps in wave numbering (e.g. Wave 1 followed by Wave 3) fail."""
        gap_plan = """# Plan Title

## Wave 1: Setup
### Objective & Scope
Setup scope 1.
### Milestones & Deliverables
- [NEW] `file1.py`
### Acceptance Criteria
- Passes.
### Runtime Receipt & Completion Gate
- Receipt 1.

## Wave 3: Advanced
### Objective & Scope
Setup scope 3.
### Milestones & Deliverables
- [NEW] `file3.py`
### Acceptance Criteria
- Passes.
### Runtime Receipt & Completion Gate
- Receipt 3.
"""
        is_valid, errors = validate_plan_content(gap_plan, "gap_plan.md")
        self.assertFalse(is_valid)
        self.assertTrue(any("Non-sequential wave numbering" in err for err in errors))

    def test_invalid_starting_wave_rejected(self) -> None:
        """Verify that plans starting at Wave > 1 fail."""
        late_start = """# Plan Title

## Wave 4: Late Wave
### Objective & Scope
Late wave scope.
### Milestones & Deliverables
- [NEW] `file.py`
### Acceptance Criteria
- Passes.
### Runtime Receipt & Completion Gate
- Receipt.
"""
        is_valid, errors = validate_plan_content(late_start, "late_start.md")
        self.assertFalse(is_valid)
        self.assertTrue(any("First wave must be numbered Wave 0 or Wave 1" in err for err in errors))

    def test_legacy_exemptions_handling(self) -> None:
        """Verify that legacy plans are marked as exempt without error."""
        exempt_file = ROOT / "documentation" / "LLM_AS_JUDGE_REFACTOR_IMPLEMENTATION_PLAN.md"
        if exempt_file.is_file():
            is_valid, errors = validate_file(exempt_file, legacy_exemptions=DEFAULT_LEGACY_EXEMPTIONS)
            self.assertTrue(is_valid)
            self.assertTrue(any("EXEMPT" in err for err in errors))

    def test_brain_implementation_plan_validates(self) -> None:
        """Verify that the current conversation implementation_plan.md satisfies wave governance."""
        brain_dir = Path.home() / ".gemini" / "antigravity-ide" / "brain" / "53d7292e-8bf6-4945-bbb2-db5c50235c6d"
        plan_path = brain_dir / "implementation_plan.md"
        if plan_path.is_file():
            is_valid, errors = validate_file(plan_path)
            self.assertTrue(is_valid, f"Current implementation_plan.md failed validation: {errors}")


if __name__ == "__main__":
    unittest.main()
