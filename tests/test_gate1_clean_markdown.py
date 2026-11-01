"""
tests/test_gate1_clean_markdown.py

Tests verifying Gate 1 deterministic QA gates on clean markdown transcripts
(no double-star ** speaker wrappers, single-hash # Transcript headers, etc.).
"""

from __future__ import annotations

from dual_pipeline.transcript_qa_core.gate1 import run_qa_gate_1


def test_gate_1_parses_plain_speaker_turns_without_double_stars():
    """Verify that Gate 1 turn density check parses speaker turns formatted as 'Speaker Name: [MM:SS]'."""
    turns = [
        f"Jennifer Pan: [{i:02d}:00] Spoken dialogue point {i} detailing the system architecture and explaining why comprehensive pre-commit validation and end-to-end observability are essential to preventing institutional software abandonment across Apex Global Enterprises engineering workstreams. We must ensure every stage of the pipeline produces verifiable runtime receipts and enforces rigorous data contracts across all operational boundaries."
        for i in range(25)
    ]
    transcript_text = f"""# Subject: Architecture Sync -- HITL-Resolved Transcript

Date: 09/14/2026
Time: 10:00 AM - 10:30 AM ET
Duration: ~30m
Platform: Microsoft Teams
Classification: internal

Attendees:
- PartnerAdvisory: Jennifer Pan

---

# Transcript

{"\n\n".join(turns)}

---

# Ambiguity Lock Register
No ambiguity met the HITL threshold.
"""
    result = run_qa_gate_1(
        f1_text=transcript_text,
        metadata={"duration_minutes": 30, "roster": [{"name": "Jennifer Pan"}]},
    )
    assert result["gates"]["Gate_1_6_Duration_Turn_Density"]["passed"] is True
    assert result["gates"]["Gate_1_6_Duration_Turn_Density"]["total_turns"] >= 20
    assert result["status"] == "PASS"


def test_gate_1_splits_body_on_single_hash_transcript_header():
    """Verify that Gate 1 properly isolates the transcript body when formatted with '# Transcript'."""
    transcript_text = """# Subject: Executive Review -- HITL-Resolved Transcript

Date: 09/14/2026
Attendees:
- PartnerAdvisory: Alex Sanchez

---

# Transcript

Alex Sanchez: [00:01] We have verified the architecture boundary and validated all core integration components.
Alex Sanchez: [00:05] All components are validated across the system architecture and ready for release verification.
Alex Sanchez: [00:10] The pipeline execution and test suite verify clean markdown formatting across each stage and meets all governance gates.

---

# Ambiguity Lock Register
ALR-01: Confirmed.
"""
    result = run_qa_gate_1(
        f1_text=transcript_text,
        metadata={"duration_minutes": 5, "roster": [{"name": "Alex Sanchez"}]},
    )
    assert result["gates"]["Gate_1_3_Temporal_Strictness"]["passed"] is True
    assert result["gates"]["Gate_1_5_Timestamp_Continuity"]["passed"] is True
    assert result["status"] == "PASS"


def test_gate_1_supports_parenthetical_speaker_roles():
    """Verify that speaker turns with role annotations like 'Brett Gavagni (NEA): [00:00]' are detected."""
    turns = [
        f"Brett Gavagni (NEA): [{i:02d}:00] Observability is paramount for long-term adoption across enterprise engineering workflows and operational tools. We need clear telemetry signals that provide full visibility into pipeline performance, model execution metrics, and latency budgets without any hidden runtime failures or silent fallbacks."
        for i in range(20)
    ]
    transcript_text = f"""# Subject: Observability Review -- HITL-Resolved Transcript

Date: 09/14/2026
Duration: ~15m
Attendees:
- NEA: Brett Gavagni

---

## Transcript

{"\n\n".join(turns)}
"""
    result = run_qa_gate_1(
        f1_text=transcript_text,
        metadata={"duration_minutes": 15, "roster": [{"name": "Brett Gavagni"}]},
    )
    assert result["gates"]["Gate_1_6_Duration_Turn_Density"]["total_turns"] >= 12
    assert result["status"] == "PASS"

