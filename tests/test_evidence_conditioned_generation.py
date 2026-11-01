"""
Unit tests for evidence-conditioned generation, zero/one-turn attendees,
and evidence grounding (Wave 3).
"""
import pytest
from dual_pipeline.transcript_qa_core.appendix_healer import (
    _role_based_topics,
    _dialogue_topics,
    heal_output_2_appendix,
)
from dual_pipeline.prompts.contracts.output2_contract import (
    build_output2_contract,
    FORBIDDEN_APPENDIX_DISCLAIMERS,
)


def test_zero_turn_attendees_never_imply_agreement():
    """Verify that zero-turn attendees across all variations never state or imply agreement."""
    forbidden_assent_tokens = [
        "implicitly endorsed",
        "accepted the operational alignment",
        "silent observation and alignment",
        "endorsed the architectural consensus",
        "ratified by the active working group",
    ]

    for idx in range(6):
        topics = _role_based_topics("Test Speaker", "Director", "Acme Corp", attendee_idx=idx)
        assert len(topics) >= 2, f"Expected at least 2 topics for idx {idx}"
        full_text = " ".join(topics).lower()

        for forbidden in forbidden_assent_tokens:
            assert forbidden not in full_text, (
                f"Forbidden silent assent token '{forbidden}' found in idx {idx}: {full_text}"
            )

        # Assert explicit presence of evidence boundary clarifying zero agreement/endorsement inferred
        assert (
            "zero agreement or endorsement is inferred" in full_text
            or "no consensus or policy alignment is inferred" in full_text
            or "zero agreement or decision endorsement is inferred" in full_text
        ), f"Missing required zero-agreement boundary statement in idx {idx}: {full_text}"


def test_one_turn_attendee_evidence_boundary():
    """Verify that single-turn attendees receive an exact quote and evidence boundary without fake motives."""
    speaker = "Alex Chen"
    single_turn = "We need to verify the telemetry pipeline latency before enabling the feature flag."
    topics = _dialogue_topics(speaker, [single_turn])

    assert len(topics) >= 2, f"Expected 2 topics for single turn, got {len(topics)}"
    full_text = " ".join(topics)

    # Must contain the actual spoken statement
    assert "telemetry pipeline latency" in full_text

    # Must contain explicit evidence boundary
    assert "evidentiary standards require that no broader consensus, agreement, or policy endorsement" in full_text

    # Must NOT contain fabricated motives
    forbidden_motives = [
        "Delivery Priorities",
        "Strategic Governance",
        "Workstream Tracking",
        "Technical Follow-up",
        "Operational Assurance",
        "Architecture Alignment",
    ]
    for motive in forbidden_motives:
        assert motive not in full_text, f"Fabricated motive '{motive}' found in single-turn topics"


def test_missing_clock_time_admissible_state():
    """Verify that the Output 02 contract explicitly allows unavailable clock time without penalty."""
    contract = build_output2_contract()
    meeting_obj_spec = contract.sections["Meeting Objective"]
    assert "Unavailable from source evidence" in meeting_obj_spec.guidance
    assert "without penalty or fabrication" in meeting_obj_spec.guidance


def test_grounded_short_sections_pass_without_word_salad():
    """Verify that appendix speaker section with short, grounded turns produces clean non-word-salad output."""
    speaker = "Dana White"
    turns = ["I can confirm the database connection pool size was set to 16."]
    topics = _dialogue_topics(speaker, turns)

    assert len(topics) == 2
    for t in topics:
        assert not any(d in t.lower() for d in FORBIDDEN_APPENDIX_DISCLAIMERS)
        assert "aspirational operating concept" not in t.lower()
        assert "bounded execution wedge" not in t.lower()


def test_appendix_healer_preserves_action_ledger_text():
    """Verify that heal_output_2_appendix preserves non-appendix text exactly."""
    recap = (
        "# Subject: Operational Architecture Review | 10/08/2026\n\n"
        "## Meeting Objective\n"
        "Evaluate database connection pooling and failover latency.\n\n"
        "## Action & Governance Ledger\n"
        "| ID | Action Item | Owner | Target Date | Status |\n"
        "| :--- | :--- | :--- | :--- | :--- |\n"
        "| ACT-01 | Scale pool to 32 | Alex Chen | 10/12/2026 | OPEN |\n\n"
        "## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n"
        "### Alex Chen - Acme Corp\n"
        "- Old thin bullet.\n"
    )

    metadata = {
        "attendees": {"Alex Chen": "Lead Architect"},
        "meeting_name": "Architecture Review",
        "duration_minutes": 30,
    }

    healed = heal_output_2_appendix(recap, metadata=metadata)
    assert "## Action & Governance Ledger" in healed
    assert "| ACT-01 | Scale pool to 32 | Alex Chen | 10/12/2026 | OPEN |" in healed
    import re
    ledger_original = re.split(r"#+\s+Appendix", recap)[0].split("## Action & Governance Ledger")[1]
    ledger_healed = re.split(r"#+\s+Appendix", healed)[0].split("## Action & Governance Ledger")[1]
    assert ledger_original.strip() == ledger_healed.strip()
