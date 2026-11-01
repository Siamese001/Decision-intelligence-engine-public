"""
tests/test_unmocked_qa_pipeline_integration.py

Un-mocked integration smoke tests for QA gate evaluation and pipeline coordination.
Executes real deterministic gates without mocking out validation logic.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from dual_pipeline.transcript_pipeline_core.qa_runner import _handle_remediation
from dual_pipeline.transcript_qa_core.evaluator import (
    evaluate_all_qa_gates,
)

SAMPLE_F1 = """# Subject: Storage Engine Architecture Evaluation | 09/18/2026

**Date:** 09/18/2026
**Attendees:**
- Delivery Partner: Amit Ayer, Alex Sanchez

---

[00:00:10] Amit Ayer: Welcome everyone. Today we are evaluating storage engine architecture.
[00:00:35] Alex Sanchez: I agree. We should explore SQLite WAL mode for local zero-contention transactions.
[00:01:00] Amit Ayer: Agreed. Let's make sure the busy timeout is set to 30 seconds and tests run hermetically.
[00:01:25] Alex Sanchez: Understood. I will verify the connection pooling pragma and run concurrency benchmarks.

## Attribution Summary
| Speaker | Attributed Turns | Confidence | Anchor Cue |
| :--- | :--- | :--- | :--- |
| Amit Ayer | 2 | HIGH | Welcome everyone |
| Alex Sanchez | 2 | HIGH | I agree |

## Ambiguity Lock Register
| Term / Ambiguity | Resolution | Affected Turns |
| :--- | :--- | :--- |
| Amit Ayer | Roster match | [00:10] |
| Alex Sanchez | Roster match | [00:35] |
| WAL | Write-Ahead Logging | [00:35] |
"""

SAMPLE_F2 = """# Subject: Storage Engine Architecture Evaluation | 09/18/2026

**Date:** 09/18/2026
**Attendees:**
- Delivery Partner: Amit Ayer, Alex Sanchez

---

## Meeting Objective
Evaluate storage engine options and establish zero-contention concurrency primitives.

## Key Discussion Points & Findings
The team analyzed SQLite WAL mode [00:35] to prevent database deadlocks in concurrent evaluation runs. We confirmed that a 30-second busy timeout [01:00] eliminates lockup cascades under load while preserving fast local access.

## Architectural Trade-Off Matrix
| Proposed Option | Architectural Mechanism | Blast Radius | Team Consensus & Rationale |
| :--- | :--- | :--- | :--- |
| Option A: SQLite WAL with pool | Thread-local connections and WAL | Low: storage package only | Selected: lightweight zero-contention hermetic operation |
| Option B: Client-server DBMS | PostgreSQL external service | High: external daemon | Deferred: excess runtime dependency overhead |

## Action & Governance Ledger
| # | Type | Item | Status | Owner | Due Date | Rationale |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | Action | Verify connection pooling pragma | In Progress | Alex Sanchez | 09/25/2026 | Prevent multi-worker lockups |

## Recommendations & Decisions Required
The engineering team ratifies SQLite WAL mode as the standard local storage foundation. This decision balances simplicity, speed, and thread safety for multi-worker gate evaluations.

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

### Alex Sanchez (Solution Architect)
- **Theme 1: SQLite WAL Concurrency Primitives**: Proposed thread-local connections with WAL pragmas to eliminate deadlocks.
- **Theme 2: Concurrency Stress Verification**: Committed to running 16-worker concurrency benchmarks to validate zero-contention.
- **Theme 3: Hermetic Dependency Minimization**: Advocated against external database daemons to preserve lightweight workstation test execution.

### Amit Ayer (Lead Architect)
- **Theme 1: Governed Storage Lifecycle**: Emphasized the necessity of 30-second busy timeouts and automated connection cleanup.
- **Theme 2: Production Architectural Decoupling**: Highlighted unidirectional layer boundaries and adherence to module budget invariants.
- **Theme 3: Multi-Worker Release Gate Assurance**: Endorsed hermetic execution standards across all pipeline stages.
"""

SAMPLE_F3 = json.dumps({
    "schema_version": "5.7",
    "meeting_id": "Storage_Engine_Architecture_09_18_2026",
    "classification": "internal",
    "audiences": ["engineering"],
    "evidence_index": {
        "ev_001": {
            "speaker": "Amit Ayer",
            "source_classification": "FACT",
            "text": "Evaluating storage engine architecture with SQLite WAL.",
            "locator": {"kind": "section", "value": "[00:10]"},
        },
        "ev_002": {
            "speaker": "Alex Sanchez",
            "source_classification": "FACT",
            "text": "SQLite WAL mode provides local zero-contention transactions.",
            "locator": {"kind": "section", "value": "[00:35]"},
        },
    },
    "graph_mutations": {
        "entity_upserts": [
            {
                "id": "person:amit_ayer",
                "type": "person",
                "name": "Amit Ayer",
                "mutation": "UPDATE",
                "evidence_ids": ["ev_001"],
            },
            {
                "id": "system:sqlite_storage",
                "type": "system",
                "name": "SQLite WAL Storage",
                "mutation": "CREATE",
                "evidence_ids": ["ev_001"],
            },
        ],
        "relationship_signals": [
            {
                "signal_id": "sig_001",
                "from_id": "person:amit_ayer",
                "to_id": "system:sqlite_storage",
                "relationship": "owns",
                "evidence_ids": ["ev_001"],
            }
        ],
        "decision_updates": [
            {
                "decision_id": "decision:adopt_sqlite_wal",
                "status": "APPROVED",
                "rationale": "Adopted SQLite WAL mode as primary storage layer",
                "evidence_ids": ["ev_002"],
            }
        ],
        "proposal_updates": [],
        "action_updates": [],
        "risk_updates": [],
    },
    "evidence_only_statement_ids": [],
})


def test_real_qa_gates_pass_unmocked():
    """Verify that real evaluate_all_qa_gates executes cleanly with zero mocks."""
    meta = {
        "archetype": "INTERNAL_TECHNICAL_SYNC",
        "meeting_type": "INTERNAL_TECHNICAL_SYNC",
        "meeting_date": "09/18/2026",
        "duration_minutes": 2,
        "roster": [
            {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead Architect"},
            {"name": "Alex Sanchez", "org": "PartnerAdvisory", "role": "Solution Architect"},
        ],
        "primary_speakers": ["Amit Ayer", "Alex Sanchez"],
    }
    qa_report = evaluate_all_qa_gates(
        SAMPLE_F1,
        SAMPLE_F2,
        SAMPLE_F3,
        source_text="Source transcript",
        metadata=meta,
        auto_heal=False,
    )
    assert qa_report["passed"] is True, (
        f"QA failed with: {qa_report.get('failures')} | "
        f"q1: {qa_report.get('output_01_forensic_transcript', {}).get('failures')} | "
        f"q2: {qa_report.get('output_02_executive_recap', {}).get('failures')} | "
        f"q3: {qa_report.get('output_03_canonical_signal_projection', {}).get('failures')}"
    )
    assert qa_report["output_01_forensic_transcript"]["passed"] is True
    assert qa_report["output_02_executive_recap"]["passed"] is True
    assert qa_report["output_03_canonical_signal_projection"]["passed"] is True


def test_remediation_handling_on_skipped_judge():
    """Verify that _handle_remediation gracefully extracts directives when judges were skipped."""
    mock_tracker = MagicMock()
    meta = {"remediation_attempts": 0}
    qa_report = {
        "passed": False,
        "output_01_forensic_transcript": {"failures": ["Speaker turn missing"]},
        "output_02_executive_recap": {"failures": ["Objective missing"]},
        "output_03_canonical_signal_projection": {"failures": ["Invalid JSON"]},
    }
    judge_report = {
        "status": "FAIL",
        "evaluation_mode": "SKIPPED_DETERMINISTIC_QA_FAILURE",
        "output_1_evaluation": {"status": "SKIPPED"},
        "output_2_evaluation": {"status": "SKIPPED"},
        "output_3_evaluation": {"status": "SKIPPED"},
        "cross_artifact_reconciliation": {"failures": []},
    }
    final_decision = {
        "status": "FAIL",
        "output_1_pass": False,
        "output_2_pass": False,
        "output_3_pass": False,
    }

    result = _handle_remediation(
        attempt=1,
        tracker=mock_tracker,
        metadata=meta,
        is_notes_mode=False,
        qa_report=qa_report,
        judge_report=judge_report,
        final_decision=final_decision,
        forensic_transcript=SAMPLE_F1,
        executive_recap=SAMPLE_F2,
        graph_json=SAMPLE_F3,
    )

    assert result[0] is False
    assert "Speaker turn missing" in meta.get("remediation_directive_forensic", "")
    assert "Objective missing" in meta.get("remediation_directive_recap", "")
    assert "Invalid JSON" in meta.get("remediation_directive_graph", "")
