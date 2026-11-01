"""
tests/test_stage02_realtime_healing.py

Unit test suite for Wave 3:
1. Real-time deterministic healing in L2ExecutiveRecapExecutor (subject, ISO dates, attendees, headers).
2. Rule C-20 parsimony: stripping of dead-weight unratified decisions and redundant contracts.
3. Forbidden section topology stripping for specific archetypes.
4. Stage 02 Checkpoint Gatekeeper: fast deterministic Gate 2 evaluation and artifact freezing.
5. Fail-closed behavior on empty or fatal recap defects.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from dual_pipeline.remediation_core import SelectiveRemediationStateMachine
from dual_pipeline.transcript_pipeline_core.stage_checkpoint import (
    evaluate_stage_02_checkpoint,
    is_fatal_stage_02_defect,
)
from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
from dual_pipeline.transcript_stages_core.recap_executor import (
    _post_process_recap,
)


def test_is_fatal_stage_02_defect():
    assert is_fatal_stage_02_defect(["empty executive recap"])
    assert is_fatal_stage_02_defect(["0 words detected in output"])
    assert not is_fatal_stage_02_defect(["Gate 2.1: missing section"])


def test_post_process_recap_heals_syntax_and_headers():
    raw_recap = (
        "# LENS Architecture Sync\n\n"
        "**Date:** 2026-09-18\n"
        "**Time:** 12:43 PM - 01:39 PM ET\n\n"
        "## Objective\n"
        "Review data ingestion mechanisms for Project LENS.\n\n"
        "## Findings\n"
        "Observed ev-001 pipeline requirements.\n\n"
        "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n\n"
        "### Amit Ayer - PartnerAdvisory, Lead Enterprise Architect\n"
        "- **Technical Analysis & Methodology**: Outlined system patterns.\n"
    )
    metadata = {
        "meeting_date": "09/18/2026",
        "time": "12:43 PM - 01:39 PM ET",
        "attendees": {
            "PartnerAdvisory": ["Amit Ayer (Lead Enterprise Architect)", "Jake Stark (LENS Lead)"]
        },
    }
    healed = _post_process_recap(
        raw_recap,
        metadata=metadata,
        forbid_sections=[],
        forensic_transcript="Amit Ayer and Jake Stark met.",
    )

    # 1. Subject header injected
    assert healed.startswith("# Subject: LENS Architecture Sync")
    # 2. ISO date normalized
    assert "09/18/2026" in healed
    assert "2026-09-18" not in healed
    # 3. Duration injected
    assert "**Duration:** ~56 minutes" in healed
    # 4. Attendees roster injected
    assert "# Attendees" in healed
    assert "Amit Ayer" in healed
    assert "Jake Stark" in healed
    # 5. Section titles normalized
    assert "Meeting Objective" in healed
    assert "Key Discussion Points & Findings" in healed


def test_c20_parsimony_deadweight_decisions_stripped():
    raw_recap = (
        "# Subject: Architecture Sync\n\n"
        "**Date:** 09/18/2026\n"
        "**Time:** 12:43 PM - 01:39 PM ET\n\n"
        "# Attendees\n"
        "- Amit Ayer (PartnerAdvisory)\n\n"
        "# Meeting Objective\n"
        "Review architecture status.\n\n"
        "# Recommendations & Decisions Required\n\n"
        "No formal decision was ratified during this session; proposed directions remain open.\n\n"
        "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n\n"
        "### Amit Ayer - PartnerAdvisory\n"
        "- **Core Discussion Context**: Reviewed architecture.\n"
    )
    healed, changes = DeterministicHealer.heal_output_2(raw_recap)
    assert "No formal decision was ratified" not in healed
    assert "# Recommendations & Decisions Required" not in healed
    assert any("c20" in c.lower() or "parsimony" in c.lower() or "decisions" in c.lower() for c in changes)


def test_c20_parsimony_redundant_contracts_stripped():
    raw_recap = (
        "# Subject: LENS Pipeline\n\n"
        "**Date:** 09/18/2026\n\n"
        "# Meeting Objective\n"
        "Review pipeline.\n\n"
        "```text\n"
        "LENS Autonomous Asset Intelligence Pipeline Architecture\n"
        "```\n\n"
        "# Appendix: Zone Parameter Contracts\n\n"
        "| Zone | Buffer | Metric |\n"
        "| 1 | 100m | high |\n\n"
        "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n\n"
        "Details here.\n"
    )
    healed, changes = DeterministicHealer.heal_output_2(raw_recap)
    assert "Zone Parameter Contracts" not in healed
    assert any("contracts" in c.lower() for c in changes)


def test_post_process_recap_strips_forbidden_sections():
    raw_recap = (
        "# Subject: Tech Sync\n\n"
        "**Date:** 09/18/2026\n\n"
        "# Meeting Objective\n"
        "Technical status.\n\n"
        "# Business Meaning and Value Case\n\n"
        "This is business value narrative that is forbidden for technical sync.\n\n"
        "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n\n"
        "Appendix details.\n"
    )
    healed = _post_process_recap(
        raw_recap,
        metadata={"meeting_date": "09/18/2026"},
        forbid_sections=["Business Meaning and Value Case"],
    )
    assert "Business Meaning and Value Case" not in healed
    assert "business value narrative" not in healed


def test_stage02_checkpoint_fail_closed_on_empty():
    sm = SelectiveRemediationStateMachine()
    rec = MagicMock()
    notify = MagicMock()
    artifacts = {}

    with pytest.raises(RuntimeError, match="Stage 02 Checkpoint Gate 2 FAIL-CLOSED"):
        evaluate_stage_02_checkpoint(
            pipeline=MagicMock(),
            executive_recap="",
            source_text="source text",
            metadata={},
            state_machine=sm,
            artifacts=artifacts,
            recorder=rec,
            notify_step=notify,
            attempt=1,
            is_notes_mode=False,
        )
    assert not sm.is_frozen("output_2")


def test_stage02_checkpoint_freezes_valid_recap():
    sm = SelectiveRemediationStateMachine()
    rec = MagicMock()
    notify = MagicMock()
    artifacts = {}

    valid_recap = (
        "# Subject: LENS Architecture Status & Technical Sync\n\n"
        "**Date:** 09/18/2026\n"
        "**Time:** 09:00 AM - 09:10 AM ET\n"
        "**Duration:** ~10 minutes\n\n"
        "# Attendees\n"
        "- Amit Ayer (Lead Enterprise Architect, PartnerAdvisory)\n"
        "- Jake Stark (LENS Lead, PartnerAdvisory)\n\n"
        "# Meeting Objective\n\n"
        "Review the technical design and architectural boundaries for the LENS platform, "
        "establishing delivery priorities and evaluation metrics across engineering streams.\n\n"
        "# Key Discussion Points & Findings\n\n"
        "1. **Core Data Ingress**: The team evaluated input stream processing under stmt-001, verifying that incoming telemetry buffers maintain lossless event sequencing across high-throughput data streams.\n"
        "2. **State Machine Persistence**: Evaluated WAL mode and serialized writes under stmt-002, confirming that zero database lock contention occurs under high concurrency with 30-second busy handlers.\n"
        "3. **Signal Projection Quality**: Assessed W2 canonical predicate alignment under stmt-003, establishing uniform entity namespace prefixes across all projected relationship assertions.\n\n"
        "# Action & Governance Ledger\n\n"
        "| # | Type | Item | Status | Owner | Due Date | Rationale |\n"
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
        "| 1 | Action | Concurrency Stress 16-Worker Harness | In Progress | Amit Ayer | 09/25/2026 | Run 100 concurrent write transactions under WAL |\n\n"
        "# Recommendations & Decisions Required\n\n"
        "The engineering team ratified Option A (Governed SQLite WAL Context) as the primary storage layer, balancing operational simplicity with high-throughput multi-worker concurrency.\n\n"
        "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n\n"
        "### Amit Ayer - PartnerAdvisory, Lead Enterprise Architect\n"
        "- **Technical Analysis & Methodology**: Outlined system mechanisms for streaming resilience, stating: \"We must maintain zero-contention SQLite storage with 30s busy timeouts.\"\n"
        "- **Delivery Safeguards & Operational Focus**: Emphasized that all pipeline stages must run under class A pytest timeouts.\n"
        "- **Alignment, Tradeoffs & Next Steps**: Proposed deterministic healing mesh to eliminate downstream retry loops.\n\n"
        "### Jake Stark - PartnerAdvisory, Participant / LENS Lead\n"
        "- **Core Discussion Context & Initial Focus**: Outlined current LENS delivery progress, noting: \"The ingestion engine is progressing well.\"\n"
        "- **Technical Analysis & Methodology**: Reviewed autonomous asset classification boundaries.\n"
        "- **Alignment, Tradeoffs & Next Steps**: Affirmed alignment on the target deliverables.\n"
    )

    metadata = {
        "meeting_date": "09/18/2026",
        "time": "09:00 AM - 09:10 AM ET",
        "duration_minutes": 10,
        "archetype": "INTERNAL_TECHNICAL_SYNC",
        "meeting_type": "INTERNAL_TECHNICAL_SYNC",
        "attendees": {
            "PartnerAdvisory": ["Amit Ayer (Lead Enterprise Architect)", "Jake Stark (LENS Lead)"]
        },
    }

    res = evaluate_stage_02_checkpoint(
        pipeline=MagicMock(),
        executive_recap=valid_recap,
        source_text="Amit and Jake met.",
        metadata=metadata,
        state_machine=sm,
        artifacts=artifacts,
        recorder=rec,
        notify_step=notify,
        attempt=1,
        is_notes_mode=False,
    )

    assert sm.is_frozen("output_2")
    assert artifacts["02_executive_recap.md"] == res
    assert "stage02_qa_gate2.json" in artifacts
    assert artifacts["stage02_qa_gate2.json"]["passed"] is True

    # Calling again returns frozen cached content
    res2 = evaluate_stage_02_checkpoint(
        pipeline=MagicMock(),
        executive_recap="corrupted_content",
        source_text="source text",
        metadata={},
        state_machine=sm,
        artifacts=artifacts,
        recorder=rec,
        notify_step=notify,
        attempt=2,
        is_notes_mode=False,
    )
    assert res2 == res
