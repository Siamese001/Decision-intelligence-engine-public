"""
Unit tests for Structured Critique-Diff Contract & Surgical AST Patcher (Wave 3).
"""

from __future__ import annotations

import pytest
from dual_pipeline.llm_judge_core.judges.recap import ExecutiveRecapJudge

from dual_pipeline.transcript_qa_core.surgical_patcher import (
    ProtectedPathMutationError,
    SurgicalPatcher,
)


def test_recap_judge_emits_valid_critique_diff_envelope() -> None:
    """Tests that ExecutiveRecapJudge includes a well-formed critique_diff_envelope."""
    judge = ExecutiveRecapJudge(model="gpt-5.6-luna", reasoning_effort="high")
    res = judge.evaluate(
        f2_text="# Subject: Test\n**Date:** 09/10/2026",
        f1_text="Forensic transcript reference",
        mock=True,
    )
    assert "critique_diff_envelope" in res
    env = res["critique_diff_envelope"]
    assert env["artifact"] == "OUTPUT02"
    assert "target_path" in env
    assert "observed_defect" in env
    assert "required_delta" in env
    assert isinstance(env["protected_paths"], list)
    assert isinstance(env["forbidden_mutations"], list)


def test_surgical_patcher_modifies_only_target_section() -> None:
    """Tests that SurgicalPatcher patches ONLY the target section while keeping others byte-identical."""
    original_recap = """# Subject: APEX_TRANS Discovery
**Date:** 09/10/2026
**Attendees:**
- Alex Rivera (APEX)

## Meeting Objective
Align on vision for Insights Hub across wind, solar, and transmission.

## Key Discussion Points & Findings
Data is siloed across T&D domains with legacy SharePoint files.

## Next Steps & Action Items
| # | Action Item | DRI | Target Date | Success Measure |
|---|---|---|---|---|
| 1 | Align on integration scope | Alex Rivera | 09/25/2026 | Document approved |

## Open Decisions / Blockers
None recorded.

## Business Meaning and Value Case
Operational value across asset fleet.

## Executive Decision
No architectural decision made during discovery.

## Bounded Execution Wedge (Candidate Discovery Hypothesis — Advisor-Proposed)
Initial hypothesis is weak and unvalidated.
"""

    new_wedge_content = """The advisor-proposed architecture hypothesis evaluates a centralized knowledge graph bridging SharePoint and TechnoSilva.
This hypothesis requires validation in Sprint 1."""

    patched_doc, audit = SurgicalPatcher.apply_surgical_patch(
        original_text=original_recap,
        target_section="Bounded Execution Wedge",
        new_section_content=new_wedge_content,
        protected_sections=[
            "Meeting Objective",
            "Key Discussion Points & Findings",
            "Next Steps & Action Items",
            "Open Decisions / Blockers",
            "Business Meaning and Value Case",
            "Executive Decision",
        ],
    )

    # Invariance assertions on protected sections
    assert "Align on vision for Insights Hub across wind, solar, and transmission." in patched_doc
    assert "Data is siloed across T&D domains with legacy SharePoint files." in patched_doc
    assert "| 1 | Align on integration scope | Alex Rivera | 09/25/2026 | Document approved |" in patched_doc
    assert "No architectural decision made during discovery." in patched_doc

    # Mutation assertion on target section
    assert "The advisor-proposed architecture hypothesis evaluates a centralized knowledge graph" in patched_doc
    assert "Initial hypothesis is weak and unvalidated." not in patched_doc
    assert audit["target_found"] is True


def test_surgical_patcher_rejects_patching_protected_path() -> None:
    """Tests that attempting to patch a protected path directly raises ProtectedPathMutationError."""
    original_recap = """# Subject: Test
## Meeting Objective
Original objective.

## Executive Decision
Original decision.
"""
    with pytest.raises(ProtectedPathMutationError, match="declared as a protected path"):
        SurgicalPatcher.apply_surgical_patch(
            original_text=original_recap,
            target_section="Meeting Objective",
            new_section_content="Attempted mutation",
            protected_sections=["Meeting Objective", "Executive Decision"],
        )


def test_surgical_patcher_applies_critique_envelope() -> None:
    """Tests applying a patch using a structured CritiqueDiffEnvelope."""
    original_recap = """# Subject: Test Recap
**Date:** 09/10/2026
**Attendees:**
- Peter Ashley

## Meeting Objective
Objective.

## Next Steps & Action Items
| # | Action Item | DRI | Target Date | Success Measure |
|---|---|---|---|---|
| 1 | Old action | Peter Ashley | 09/25/2026 | Done |

## Bounded Execution Wedge (Candidate Discovery Hypothesis — Advisor-Proposed)
Old hypothesis.
"""
    critique_env = {
        "artifact": "OUTPUT02",
        "target_path": "sections['Bounded Execution Wedge']",
        "observed_defect": "Lacks specific validation steps",
        "required_delta": "Recast with concrete discovery validation milestones",
        "protected_paths": ["Meeting Objective", "Next Steps & Action Items"],
        "forbidden_mutations": ["Do not alter DRIs"],
    }
    replacement = "Refined hypothesis establishing concrete discovery validation milestones."

    patched, audit = SurgicalPatcher.apply_critique_envelope(
        original_text=original_recap,
        critique_envelope=critique_env,
        replacement_content=replacement,
    )

    assert replacement in patched
    assert "Old hypothesis." not in patched
    assert "| 1 | Old action | Peter Ashley | 09/25/2026 | Done |" in patched
