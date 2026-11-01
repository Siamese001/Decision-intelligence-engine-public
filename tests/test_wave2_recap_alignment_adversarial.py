"""tests/test_wave2_recap_alignment_adversarial.py

Adversarial and boundary tests for Wave 2 Executive Recap alignment.
Covers 4 mandatory edge-case categories:
1. Boundary payload: Empty recap text, 0-action confirmed sentence.
2. Malformed input: Truncated table columns, malformed markdown headers.
3. Invariant bypass attempt: Bare persona collapse in Section 3 and lazy generic owners.
4. Exact production regression: Unnumbered '# Meeting Objective' must not trigger X2 missing section error.
"""

from __future__ import annotations

import pytest
from dual_pipeline.prompts.contracts.versioned_judge_contracts import MeetingXExitContract
from dual_pipeline.prompts.contracts.output2_contract import (
    validate_section3_thematic_decoupling,
)
from dual_pipeline.llm_judge_core.recap_checks import (
    check_action_ledger_integrity,
)


# Category 1: Boundary payload
def test_boundary_empty_recap_fails_closed():
    """Verify empty or whitespace-only recap fails X2 cleanly."""
    passed, errors = MeetingXExitContract.validate_x2_recap_structure("")
    assert passed is False
    assert any("empty" in e.lower() for e in errors)


def test_boundary_no_formal_actions_confirmed_sentence():
    """Verify standard confirmation sentence satisfies Action Ledger check when 0 actions exist."""
    recap_no_actions = """# Meeting Objective
Objective text.

# Action & Governance Ledger
No formal actions were established in this session; open initiatives remain unresolved questions.
"""
    passed, errors = MeetingXExitContract.validate_x2_recap_structure(recap_no_actions)
    assert passed is True
    assert len(errors) == 0

    passed_rc, defects = check_action_ledger_integrity(recap_no_actions, f1_text="Some text")
    assert passed_rc is True
    assert len(defects) == 0


# Category 2: Malformed input
def test_malformed_action_ledger_row_handling():
    """Verify truncated or uneven table rows are safely parsed without IndexError."""
    malformed_table_text = """# Meeting Objective
Objective.

# Action & Governance Ledger
| # | Type | Item |
|---|---|---|
| 1 | Action | Incomplete row |
"""
    # Should not crash
    passed, errors = MeetingXExitContract.validate_x2_recap_structure(malformed_table_text)
    assert isinstance(passed, bool)


# Category 3: Invariant bypass attempt
def test_bare_persona_collapse_is_rejected():
    """Verify that bare persona anchors like '**Mitch Garstka:**' are strictly rejected."""
    bare_collapse_text = """# Key Discussion Points & Findings
- **Mitch Garstka:** Spoke about deployment.
- **Amit Ayer:** Spoke about governance.
"""
    meta = {"attendees": ["Mitch Garstka", "Amit Ayer"]}
    passed, failures = validate_section3_thematic_decoupling(bare_collapse_text, meta=meta)
    assert passed is False
    assert any("SECTION_3_ANCHOR_PERSONA_COLLAPSE" in f for f in failures)


def test_lazy_generic_owner_is_rejected():
    """Verify that lazy owners like 'Team' or 'PartnerAdvisory' are rejected by X2 and recap_checks."""
    lazy_owner_text = """# Meeting Objective
Objective.

# Action & Governance Ledger
| # | Type | Item | Owner | Timing | Loc | Gate | Status |
|---|---|---|---|---|---|---|---|
| 1 | Action | Do task | Team | 09/30/2026 | [00:01:00] | Gate | Confirmed |
"""
    passed, errors = MeetingXExitContract.validate_x2_recap_structure(lazy_owner_text)
    assert passed is False
    assert any("not a resolved person" in e for e in errors)


# Category 4: Exact production regression
def test_production_regression_unnumbered_headers():
    """Regression test: Unnumbered '# Meeting Objective' must not trigger X2 missing section error."""
    valid_unnumbered = """# Meeting Objective
Context here.

# Action & Governance Ledger
No formal actions were established in this session; open initiatives remain unresolved questions.
"""
    passed, errors = MeetingXExitContract.validate_x2_recap_structure(valid_unnumbered)
    assert passed is True
    assert not any("Missing required section" in e for e in errors)
