"""
End-to-end tests for evidence-conditioned generation, appendix healing,
and surgical remediation protection (Wave 3).
"""
import re
from typing import Any, Dict
import pytest

from dual_pipeline.transcript_qa_core.appendix_healer import heal_output_2_appendix
from dual_pipeline.transcript_pipeline_core.surgical_remediator import (
    SurgicalRemediator,
    compute_section_hashes,
)


@pytest.fixture
def sample_executive_recap() -> str:
    return (
        "# Subject: Operational Architecture Review | 10/08/2026\n\n"
        "**Date:** 10/08/2026\n"
        "**Time:** 02:00 PM – 03:00 PM ET\n"
        "**Duration:** ~45m\n\n"
        "## Meeting Objective\n"
        "The objective was to align on platform database scaling and failover tolerances.\n\n"
        "## Key Discussion Points & Findings\n"
        "- Database connection saturation observed under peak load [00:12:15].\n"
        "- Read replica lag averaged 450ms during stress events [00:18:30].\n\n"
        "## Action & Governance Ledger\n"
        "| ID | Action Item | Accountable Owner | Target Timing | Status |\n"
        "| :--- | :--- | :--- | :--- | :--- |\n"
        "| ACT-01 | Scale primary connection pool from 16 to 32 | Daniel Brankin | 10/15/2026 | OPEN |\n"
        "| ACT-02 | Add replica read timeout alerts | Alex Chen | 10/18/2026 | OPEN |\n\n"
        "## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n"
        "### Daniel Brankin - Apex Global Enterprises\n"
        "- Old brief placeholder bullet that requires remediation.\n"
    )


def test_e2e_recap_appendix_healing_with_sparse_and_silent_attendees(sample_executive_recap):
    """E2E test verifying appendix healing on mixed active, single-turn, and silent attendees."""
    meta: Dict[str, Any] = {
        "meeting_name": "Operational Architecture Review",
        "duration_minutes": 45,
        "attendees": {
            "Apex Global Enterprises": [
                "Daniel Brankin (Project Development Lead)",
                "Sharon Diaram (Director, Technology)",
                "Jill Cizewski (Business Lead)",
            ]
        },
    }

    # Add mock transcript turns to output_1 text so extractor can find dialogue
    output_1_text = (
        "Daniel Brankin: We need to ensure pool size is 32 before next release cycle.\n"
        "Daniel Brankin: The replica lag directly impacts query response times.\n"
        "Daniel Brankin: Let's also verify failover parameters with platform engineering.\n"
        "Sharon Diaram: From an infrastructure budget perspective, 32 connections is well within limits.\n"
        # Jill Cizewski has 0 spoken turns
    )

    baseline_hashes = compute_section_hashes(sample_executive_recap)

    healed = heal_output_2_appendix(
        sample_executive_recap,
        metadata=meta,
        output_1=output_1_text,
    )

    # 1. Non-appendix sections must remain byte-identical
    new_hashes = compute_section_hashes(healed)
    for sec in baseline_hashes:
        if "appendix" not in sec:
            assert new_hashes.get(sec) == baseline_hashes.get(sec), (
                f"Section '{sec}' was unexpectedly mutated during appendix healing!"
            )

    # 2. Action Ledger must be strictly intact
    assert "| ACT-01 | Scale primary connection pool from 16 to 32 | Daniel Brankin | 10/15/2026 | OPEN |" in healed
    assert "| ACT-02 | Add replica read timeout alerts | Alex Chen | 10/18/2026 | OPEN |" in healed

    # 3. All three attendees must be present in appendix
    assert "Daniel Brankin" in healed
    assert "Sharon Diaram" in healed
    assert "Jill Cizewski" in healed

    # 4. Single-turn attendee (Sharon Diaram) must have quote and evidence boundary
    assert "infrastructure budget perspective" in healed
    assert "evidentiary standards require that no broader consensus" in healed

    # 5. Zero-turn attendee (Jill Cizewski) must have factual attendance and zero agreement inferred
    assert "zero agreement" in healed or "no consensus" in healed
    assert "implicitly endorsed" not in healed
    assert "silent observation and alignment" not in healed


def test_e2e_surgical_remediator_appendix_target_success(sample_executive_recap):
    """E2E test verifying surgical remediation successfully repairs appendix while preserving non-appendix sections."""
    qa_report = {
        "output_02_executive_recap": {
            "failures": ["[GATE_2_8_THIN_SPEAKER_SECTION] Appendix is thin"],
        }
    }
    judge_report = {
        "output_02_executive_recap": {
            "failures": [],
            "critique_diff_envelope": {
                "target_path": "appendix",
                "observed_defect": "Thin speaker sections in appendix",
            },
        }
    }
    meta = {
        "meeting_name": "Operational Architecture Review",
        "duration_minutes": 45,
        "attendees": {"Apex Global Enterprises": ["Daniel Brankin (Lead)"]},
    }

    remediated, did_remediate, audit_trail = SurgicalRemediator.remediate_output_2(
        sample_executive_recap,
        qa_report=qa_report,
        judge_report=judge_report,
        metadata=meta,
    )

    assert did_remediate is True
    assert any("Surgically remediated appendix (frozen non-appendix sections intact: True)" in a for a in audit_trail)
    assert "## Action & Governance Ledger" in remediated


def test_e2e_surgical_remediator_rejects_mutation_of_frozen_sections(monkeypatch, sample_executive_recap):
    """E2E test verifying surgical remediation rejects patches if frozen non-appendix sections are mutated."""
    qa_report = {
        "output_02_executive_recap": {
            "failures": ["appendix thin"],
        }
    }
    judge_report = {
        "output_02_executive_recap": {
            "failures": [],
            "critique_diff_envelope": {"target_path": "appendix"},
        }
    }

    # Simulate a buggy healer that inadvertently touches the Action Ledger
    def buggy_healer(text, **kwargs):
        return text.replace("ACT-01", "ACT-MUTATED") + "\n# Appendix: New\n- test"

    monkeypatch.setattr(
        "dual_pipeline.transcript_qa_core.appendix_healer.heal_output_2_appendix",
        buggy_healer,
    )

    remediated, did_remediate, audit_trail = SurgicalRemediator.remediate_output_2(
        sample_executive_recap,
        qa_report=qa_report,
        judge_report=judge_report,
    )

    assert did_remediate is False
    assert remediated == sample_executive_recap
    assert any("Remediation rejected: non-appendix sections were mutated" in a for a in audit_trail)


def test_e2e_invalid_critique_target_returns_evaluator_repair():
    """E2E test verifying missing or invalid critique targets return None (evaluator repair)."""
    # Case 1: Invalid target path
    judge_report_invalid = {
        "output_02_executive_recap": {
            "critique_diff_envelope": {
                "target_path": "invalid_fabricated_section",
                "observed_defect": "Unknown issue",
            }
        }
    }
    target = SurgicalRemediator.identify_remediation_target("output_2", {}, judge_report_invalid)
    assert target is None, "Expected None (evaluator repair) for invalid target path"

    # Case 2: Target targets action ledger while action is protected
    judge_report_protected_action = {
        "output_02_executive_recap": {
            "critique_diff_envelope": {
                "target_path": "action_ledger",
                "protected_paths": ["action_ledger"],
            }
        }
    }
    target = SurgicalRemediator.identify_remediation_target("output_2", {}, judge_report_protected_action)
    assert target is None, "Expected None (evaluator repair) when action ledger is protected"

    # Case 3: Empty reports
    target = SurgicalRemediator.identify_remediation_target("output_2", {}, {})
    assert target is None, "Expected None (evaluator repair) for empty reports"
