"""
Adversarial and boundary tests for evidence grounding, sparse sources,
roster reordering, and fail-closed remediation (Wave 3).

Covers Mandatory Edge-Case Categories:
1. Boundary payload (empty dialogue, extreme roster sizes, sparse input)
2. Invariant bypass attempt (roster reordering invariance, injected silent assent purging)
3. Malformed input & Rollback/Recovery (corrupted metadata, malformed critique envelope)
"""
import pytest
from typing import Any, Dict, List

from dual_pipeline.transcript_qa_core.appendix_healer import (
    heal_output_2_appendix,
    _dialogue_topics,
    _role_based_topics,
)
from dual_pipeline.transcript_pipeline_core.surgical_remediator import (
    SurgicalRemediator,
    compute_section_hashes,
)


# ==============================================================================
# Category 1: Boundary Payload
# ==============================================================================

def test_adversarial_boundary_empty_dialogue_and_sparse_roster():
    """Verify behavior on empty dialogue, whitespace-only turns, and sparse roster."""
    # 1. Empty dialogue returns empty list without error
    assert _dialogue_topics("Empty Speaker", []) == []

    # 2. Whitespace-only turns are safely handled
    topics = _dialogue_topics("Whitespace Speaker", ["   ", "\n\t", ""])
    assert len(topics) >= 1  # Handled safely as brief turn
    for t in topics:
        assert "implicitly endorsed" not in t.lower()
        assert "silent observation and alignment" not in t.lower()


def test_adversarial_boundary_extreme_roster_names():
    """Verify behavior on extreme name lengths and special punctuation."""
    short_name = "X"
    topics_short = _role_based_topics(short_name, "Analyst", "Acme", attendee_idx=0)
    assert len(topics_short) == 2
    assert "X" in topics_short[0]
    assert "zero agreement" in topics_short[1].lower()

    long_name = "Dr. " + "A" * 80 + " Jr."
    topics_long = _role_based_topics(long_name, "Chief Architect", "Acme", attendee_idx=1)
    assert len(topics_long) == 2
    assert "no consensus or policy alignment is inferred" in topics_long[1].lower()


# ==============================================================================
# Category 2: Invariant Bypass Attempt
# ==============================================================================

def test_adversarial_roster_reordering_invariance():
    """Verify that permuting attendee order in metadata preserves substantive evidence."""
    recap_base = (
        "# Subject: Permutation Test | 10/08/2026\n\n"
        "## Meeting Objective\n"
        "Assess invariance under roster permutation.\n\n"
        "## Action & Governance Ledger\n"
        "| ID | Action Item | Owner | Target Date | Status |\n"
        "| :--- | :--- | :--- | :--- | :--- |\n"
        "| ACT-01 | Run test | Team | 10/12/2026 | OPEN |\n\n"
        "## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n"
        "### Placeholder\n"
        "- Temp.\n"
    )

    meta_order_a = {
        "meeting_name": "Permutation Test",
        "duration_minutes": 30,
        "attendees": {
            "Acme Corp": [
                "Alice Smith (Principal Engineer)",
                "Bob Jones (Director)",
            ]
        },
    }

    meta_order_b = {
        "meeting_name": "Permutation Test",
        "duration_minutes": 30,
        "attendees": {
            "Acme Corp": [
                "Bob Jones (Director)",
                "Alice Smith (Principal Engineer)",
            ]
        },
    }

    healed_a = heal_output_2_appendix(recap_base, metadata=meta_order_a)
    healed_b = heal_output_2_appendix(recap_base, metadata=meta_order_b)

    # Both must contain both speakers
    for spk in ["Alice Smith", "Bob Jones"]:
        assert spk in healed_a
        assert spk in healed_b

    # Both must have zero-agreement boundary statements for silent attendance
    for doc in [healed_a, healed_b]:
        assert "zero agreement" in doc.lower() or "no consensus" in doc.lower()
        assert "implicitly endorsed" not in doc.lower()


def test_adversarial_injected_silent_assent_purged():
    """Verify that existing appendix text with injected silent assent is invalidated and purged."""
    poisoned_recap = (
        "# Subject: Governance Review | 10/08/2026\n\n"
        "## Meeting Objective\n"
        "Review quarterly deliverables.\n\n"
        "## Action & Governance Ledger\n"
        "| ID | Action Item | Owner | Target Date | Status |\n"
        "| :--- | :--- | :--- | :--- | :--- |\n"
        "| ACT-01 | Review | Alice | 10/12/2026 | OPEN |\n\n"
        "## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n\n"
        "### Alice Smith - Acme Corp\n"
        "- **Discussion Scope**: Alice attended the sync.\n"
        "- **Attributable Status**: Alice implicitly endorsed the architectural consensus and forward delivery boundaries ratified by the active working group.\n"
    )

    meta = {
        "meeting_name": "Governance Review",
        "duration_minutes": 30,
        "attendees": {"Acme Corp": ["Alice Smith (Lead)"]},
    }

    healed = heal_output_2_appendix(poisoned_recap, metadata=meta)

    # Injected silent assent must NOT survive healing
    assert "implicitly endorsed the architectural consensus" not in healed
    assert "zero agreement or endorsement is inferred from silence" in healed


# ==============================================================================
# Category 3: Malformed Input & Rollback / Fail-Closed
# ==============================================================================

def test_adversarial_malformed_metadata_fail_closed():
    """Verify heal_output_2_appendix handles corrupted/malformed metadata types safely."""
    recap = (
        "# Subject: Robustness Check | 10/08/2026\n\n"
        "## Meeting Objective\n"
        "Check robustness.\n\n"
        "## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n"
        "### Old Speaker\n- old.\n"
    )

    # Corrupted metadata types
    malformed_meta: Dict[str, Any] = {
        "duration_minutes": "thirty minutes",  # string instead of int
        "attendees": 99999,                   # int instead of dict/list
        "project": None,
        "meeting_name": ["Not a string"],
    }

    # Must not crash; should fall back gracefully
    healed = heal_output_2_appendix(recap, metadata=malformed_meta)
    assert isinstance(healed, str)
    assert "## Meeting Objective" in healed


def test_adversarial_surgical_remediation_corrupted_critique_envelope():
    """Verify SurgicalRemediator handles malformed critique envelope structures fail-closed."""
    recap = (
        "# Subject: Test | 10/08/2026\n\n"
        "## Meeting Objective\nTest.\n\n"
        "## Action & Governance Ledger\n| A | B |\n|---|---|\n"
    )

    # Malformed critique envelopes
    malformed_envelopes = [
        {"target_path": 12345},                              # target_path is non-string
        {"target_path": None, "protected_paths": "not-a-list"},
        {"critique_diff_envelope": "not a dict"},           # critique envelope is non-dict
    ]

    for env in malformed_envelopes:
        judge_report = {"output_02_executive_recap": env}
        target = SurgicalRemediator.identify_remediation_target("output_2", {}, judge_report)
        assert target is None, f"Expected None for malformed envelope: {env}"

        repaired, did_remediate, trail = SurgicalRemediator.remediate_output_2(
            recap,
            qa_report={},
            judge_report=judge_report,
        )
        assert did_remediate is False
        assert repaired == recap
