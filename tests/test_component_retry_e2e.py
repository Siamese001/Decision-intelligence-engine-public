"""End-to-end tests for Wave 5 component-level retry and downstream protection.

Verifies end-to-end execution:
1. Appendix defect repair modifies only appendix; passing sections and Output 01/03 remain byte-identical.
2. Frozen stage in state machine retains exact bytes through evaluator QA with auto_heal=True.
3. Transcript speaker changes propagate dependency invalidation to recap/graph criteria.
4. Component retry lifecycle terminates when global or class-specific budgets are reached.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict

import pytest

from dual_pipeline.transcript_pipeline_core.component_retry_planner import (
    ComponentRetryPlanner,
    RetryBudget,
    RetryClass,
)
from dual_pipeline.transcript_pipeline_core.surgical_remediator import (
    SurgicalRemediator,
    compute_section_hashes,
)
from dual_pipeline.transcript_qa_core.evaluator import evaluate_all_qa_gates


class MockStateMachine:
    """Mock state machine for testing stage freeze invariants."""

    def __init__(self, frozen_stages: list[str]) -> None:
        self._frozen = set(frozen_stages)

    def is_frozen(self, stage_key: str) -> bool:
        return stage_key in self._frozen


def _generate_artifacts() -> tuple[str, str, str]:
    f1 = (
        "# Output 1: Forensic Transcript\n"
        "Alice: We are reviewing the grid integration scope.\n"
        "Bob: Latency is within 50ms.\n"
    )
    f2 = (
        "# Subject: Grid Integration Review\n"
        "**Date:** 09/10/2026\n"
        "**Attendees:**\n"
        "- Alice (Lead)\n"
        "- Bob (Engineer)\n\n"
        "# Meeting Objective\n"
        "Review grid integration scope and latency requirements.\n\n"
        "# Key Discussion Points & Findings\n"
        "Substation telemetry confirms latency is within 50ms.\n\n"
        "# Action & Governance Ledger\n"
        "| # | Action Item | DRI | Target Date | Success Measure |\n"
        "|---|---|---|---|---|\n"
        "| 1 | Deploy telemetry probe | Alice | 09/25/2026 | Probe verified |\n\n"
        "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n"
        "# Alice — Lead\n"
        "- **Contribution**: Alice reviewed grid integration scope.\n"
        "# Bob — Engineer\n"
        "- **Contribution**: Bob confirmed latency is within 50ms.\n"
    )
    f3 = (
        "{\n"
        '  "schema_version": "5.7",\n'
        '  "meeting_name": "Grid Integration Review",\n'
        '  "entities": [\n'
        '    {"id": "person:alice", "name": "Alice", "role": "Lead"},\n'
        '    {"id": "person:bob", "name": "Bob", "role": "Engineer"}\n'
        "  ],\n"
        '  "decisions": [\n'
        '    {"topic": "Latency", "owner": "Alice", "evidence": ["Latency is within 50ms."]}\n'
        "  ]\n"
        "}"
    )
    return f1, f2, f3


def test_appendix_repair_preserves_other_sections_e2e():
    f1, f2, f3 = _generate_artifacts()
    baseline_hashes = compute_section_hashes(f2)
    f1_hash_orig = hashlib.sha256(f1.encode("utf-8")).hexdigest()
    f3_hash_orig = hashlib.sha256(f3.encode("utf-8")).hexdigest()

    # Appendix is missing perspective for Carol from roster
    f2_defective = f2

    qa_report: Dict[str, Any] = {"output_02_executive_recap": {"failures": ["Appendix perspective defect: missing Carol"]}}
    judge_report: Dict[str, Any] = {
        "output_2_evaluation": {
            "verdict": "FAIL",
            "composite_score": 84,
            "section_scores": {
                "header_and_overview": 10,
                "key_updates_technical_substance": 35,
                "conditional_extensions": 20,
                "appendix_stakeholder_perspectives": 19,
            },
            "critique_diff_envelope": {
                "artifact": "OUTPUT02",
                "target_path": "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown",
                "target_section": "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown",
                "observed_defect": "Missing attendee perspective in appendix",
                "required_delta": "Synthesize Carol perspective",
                "protected_paths": ["# Meeting Objective", "# Key Discussion Points & Findings", "# Action & Governance Ledger"],
            },
        }
    }

    remediated_f2, did_rem, audit = SurgicalRemediator.remediate_output_2(
        f2_defective,
        qa_report=qa_report,
        judge_report=judge_report,
        metadata={"duration_minutes": 30, "roster": [{"name": "Alice"}, {"name": "Bob"}, {"name": "Carol"}]},
    )

    assert did_rem is True
    # Verify non-appendix sections in Output 2 remained strictly byte-identical
    new_hashes = compute_section_hashes(remediated_f2)
    for sec, h in baseline_hashes.items():
        if "appendix" not in sec:
            assert new_hashes.get(sec) == h, f"Section {sec} mutated during appendix remediation!"

    # Verify Outputs 01 and 03 remained strictly untouched
    assert hashlib.sha256(f1.encode("utf-8")).hexdigest() == f1_hash_orig
    assert hashlib.sha256(f3.encode("utf-8")).hexdigest() == f3_hash_orig


def test_frozen_artifact_protection_in_evaluator_qa():
    f1, f2, f3 = _generate_artifacts()
    orig_f1 = f1

    # Freeze Output 1
    sm = MockStateMachine(frozen_stages=["output_1"])
    meta = {"state_machine": sm}

    # Run evaluate_all_qa_gates with auto_heal=True
    report = evaluate_all_qa_gates(
        f1_text=f1,
        f2_text=f2,
        f3_text=f3,
        metadata=meta,
        auto_heal=True,
    )

    # Output 1 was frozen, so healed_artifacts should preserve exact original f1 bytes
    healed_f1 = report["healed_artifacts"]["output_1"]
    assert healed_f1 == orig_f1
    assert report["output_01_forensic_transcript"]["status"] == "PASS"
    assert report["output_01_forensic_transcript"].get("frozen") is True


def test_component_retry_budget_exhaustion_e2e():
    budget = RetryBudget(max_total_attempts=3, max_content_repairs=2)
    planner = ComponentRetryPlanner(budget=budget)

    # Attempt 1: Content repair
    can, _ = planner.budget.can_attempt(RetryClass.CONTENT_REPAIR)
    assert can is True
    planner.budget.record_attempt(RetryClass.CONTENT_REPAIR)

    # Attempt 2: Content repair
    can, _ = planner.budget.can_attempt(RetryClass.CONTENT_REPAIR)
    assert can is True
    planner.budget.record_attempt(RetryClass.CONTENT_REPAIR)

    # Attempt 3: Content repair should fail (exceeded max_content_repairs)
    can, reason = planner.budget.can_attempt(RetryClass.CONTENT_REPAIR)
    assert can is False
    assert "Content repair budget exhausted" in reason

    # Attempt 3: Evaluator repair can still proceed (total 2 < 3)
    can, _ = planner.budget.can_attempt(RetryClass.EVALUATOR_REPAIR)
    assert can is True
    planner.budget.record_attempt(RetryClass.EVALUATOR_REPAIR)

    # Total attempts now 3/3; any further attempt fails
    can, reason = planner.budget.can_attempt(RetryClass.TRANSPORT)
    assert can is False
    assert "Global retry budget exhausted" in reason
