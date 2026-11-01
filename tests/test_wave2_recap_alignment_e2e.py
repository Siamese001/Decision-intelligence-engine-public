"""tests/test_wave2_recap_alignment_e2e.py

End-to-End verification of Wave 2 Executive Recap alignment:
1. Unnumbered '# Meeting Objective' headers pass X2 and output2_contract validation.
2. Action items with 'Unassigned (Pending Owner Assignment)' pass X2 and recap_checks.
3. Proposed action timing with 'TBD (Proposed)' passes X2 and recap_checks.
4. Section 3 bold anchors with actor-plus-action format pass output2_contract.
"""

from __future__ import annotations

import pytest
from dual_pipeline.prompts.contracts.versioned_judge_contracts import MeetingXExitContract
from dual_pipeline.prompts.contracts.output2_contract import (
    validate_section3_thematic_decoupling,
    OUTPUT2_CONTRACT,
)
from dual_pipeline.llm_judge_core.recap_checks import (
    check_action_ledger_integrity,
)


@pytest.fixture
def compliant_recap_text() -> str:
    return """# Subject: Standup 9/30 Mitch PartnerAdvisory -- Executive Strategic & Technical Brief | 09/30/2026
**Date:** 09/30/2026
**Time:** 09:30 AM - 10:00 AM ET
**Duration:** ~30 minutes
**Attendees:**
PartnerAdvisory:
- Mitch Garstka (Lead Software Engineer)
- Amit Ayer (Lead Client Partner)

# Meeting Objective
This tactical architecture alignment establishes the technical mechanism and validation contracts for stage synthesis. The catalyst is achieving first-pass synthesis qualification across all three outputs without false-positive deductions on contract-mandated headers or corporate attribution fallbacks.

# Key Discussion Points & Findings
- **Mitch Garstka reviews deployment topology and testing protocols:** Mitch reviewed the containerized deployment environment and verified that all test gates execute with mandatory timeouts and visual progress tracking to eliminate silent hanging states.
- **Amit Ayer explains contractual milestone boundaries and evaluation rubrics:** Amit clarified that SOW milestones govern commercial acceptance while pipeline stages govern technical execution, preventing conflation in telemetry reports.
- **Mitch Garstka demonstrates automated CI verification across all gates:** Demonstrating the pytest runner with progress hooks, Mitch confirmed 100% pass rates across deterministic pre-checks and exit validators.

# Action & Governance Ledger
| # | Type | Initiative & Deliverable Context | Accountable Owner | Target Timing | Transcript Locator | Governance Gate | Status |
|---|---|---|---|---|---|---|---|
| 1 | Action | Deploy Wave 1 forensic alignment fixes | Mitch Garstka | 09/30/2026 | [00:00:45] | Complete | Confirmed |
| 2 | Action | Formulate proposal for heuristic caching | Unassigned (Pending Owner Assignment) | TBD (Proposed) | [00:01:05] | In Review | Proposed |

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown
### Non-Speaking Session Participants
- None.

#### Mitch Garstka — PartnerAdvisory — Lead Software Engineer
- **Theme 1**: Technical execution and CI gate enforcement.
- **Theme 2**: Pipeline stage orchestration.
- **Theme 3**: Verification receipt generation.

#### Amit Ayer — PartnerAdvisory — Lead Client Partner
- **Theme 1**: Commercial alignment and governance.
- **Theme 2**: Architectural standards.
- **Theme 3**: Progress reporting.
"""


def test_unnumbered_headers_pass_x2(compliant_recap_text: str):
    """Verify that unnumbered '# Meeting Objective' headers pass X2 exit gate."""
    passed, errors = MeetingXExitContract.validate_x2_recap_structure(compliant_recap_text)
    assert passed is True, f"X2 failed with errors: {errors}"
    assert len(errors) == 0


def test_unassigned_pending_owner_passes_x2_and_recap_checks(compliant_recap_text: str):
    """Verify 'Unassigned (Pending Owner Assignment)' is accepted without anti-embarrassment error."""
    passed, errors = MeetingXExitContract.validate_x2_recap_structure(compliant_recap_text)
    assert passed is True
    assert not any("Anti-embarrassment" in e for e in errors)

    # Deterministic recap checks
    passed_rc, defects = check_action_ledger_integrity(
        compliant_recap_text,
        f1_text="Mitch Garstka and Amit Ayer spoke.",
        metadata={"attendees": ["Mitch Garstka", "Amit Ayer"]},
    )
    assert passed_rc is True, f"recap_checks failed: {defects}"
    assert not any("Forbidden generic/placeholder owner" in d for d in defects)


def test_actor_plus_action_anchors_pass_output2_contract(compliant_recap_text: str):
    """Verify that actor-plus-action anchors (e.g. '**Mitch Garstka reviews...**') pass decoupling check."""
    meta = {"attendees": ["Mitch Garstka", "Amit Ayer"]}
    passed, failures = validate_section3_thematic_decoupling(compliant_recap_text, meta=meta)
    assert passed is True, f"Section 3 anchor check failed: {failures}"
    assert len(failures) == 0


from dual_pipeline.prompts.contracts.output_contract import SectionRequirement


def test_output2_contract_end_to_end(compliant_recap_text: str):
    """Verify full declarative OUTPUT2_CONTRACT passes compliant recap text."""
    archetype = OUTPUT2_CONTRACT.detect_archetype("Standup 9/30 Mitch PartnerAdvisory", compliant_recap_text)
    for title, spec in OUTPUT2_CONTRACT.sections.items():
        if spec.get_requirement(archetype) == SectionRequirement.MANDATORY:
            assert (title in compliant_recap_text) or any(alias in compliant_recap_text for alias in spec.aliases)
