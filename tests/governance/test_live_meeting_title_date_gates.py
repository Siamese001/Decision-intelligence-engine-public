"""
tests/governance/test_live_meeting_title_date_gates.py

Unit and adversarial governance test suite verifying Gate 1.1b, Gate 2.1,
and output hierarchy validation for live meeting title and date enforcement.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from dual_pipeline.transcript_qa_core.gate1 import run_qa_gate_1
from dual_pipeline.transcript_qa_core.gate2 import run_qa_gate_2
from tools.validate_output_hierarchy_core.container import _classify_container_item


def test_gate_1_1b_compliant_live_transcript_passes():
    """Gate 1.1b passes when live meeting title contains both meeting name and date."""
    transcript = """# Live Meeting Transcript: Apex Global Strategy Alignment | 10/12/2026

**Date:** 10/12/2026
**Time:** 10:00 AM - 10:30 AM ET
**Attendees:**
- PartnerAdvisory: Reid Schenk

---

Reid Schenk: [00:01] We are reviewing the architecture strategy and ensuring all deliverables are aligned.
Reid Schenk: [00:10] The unified pipeline and execution verification gates are in place across all workstreams.
Reid Schenk: [00:20] Every artifact will strictly conform to the governed schema contracts.

---

## Ambiguity Lock Register
| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |
|---|---|---|---|---|---|
| 1 | Reid Schenk Title Grounding | [00:01] | stmt-01 | Architect verified | Multi-Tier Acoustic Isolation Protocol (Operator Verified) |
"""
    result = run_qa_gate_1(
        f1_text=transcript,
        metadata={
            "is_live": True,
            "meeting_name": "Apex Global Strategy Alignment",
            "duration_minutes": 5,
            "roster": [{"name": "Reid Schenk"}],
        },
    )
    assert result["gates"]["Gate_1_1b_Title_And_Date"]["passed"] is True
    assert not any("Gate 1.1b" in f for f in result["failures"])


def test_gate_1_1b_missing_date_fails():
    """Gate 1.1b fails with descriptive diagnostic when live heading omits date."""
    transcript = """# Live Meeting Transcript: Apex Global Strategy Alignment

**Date:** 10/12/2026
**Attendees:**
- PartnerAdvisory: Reid Schenk

---

Reid Schenk: [00:01] Discussing strategy alignment without date in primary header.
"""
    result = run_qa_gate_1(
        f1_text=transcript,
        metadata={
            "is_live": True,
            "meeting_name": "Apex Global Strategy Alignment",
            "duration_minutes": 5,
            "roster": [{"name": "Reid Schenk"}],
        },
    )
    assert result["gates"]["Gate_1_1b_Title_And_Date"]["passed"] is False
    assert any("must include meeting date in format" in f for f in result["failures"])


def test_gate_1_1b_missing_name_fails():
    """Gate 1.1b fails with descriptive diagnostic when live heading omits meeting name."""
    transcript = """# Live Meeting Transcript: | 10/12/2026

**Date:** 10/12/2026
**Attendees:**
- PartnerAdvisory: Reid Schenk

---

Reid Schenk: [00:01] Discussing strategy alignment with missing meeting name in title.
"""
    result = run_qa_gate_1(
        f1_text=transcript,
        metadata={
            "is_live": True,
            "meeting_name": "Apex Global Strategy Alignment",
            "duration_minutes": 5,
            "roster": [{"name": "Reid Schenk"}],
        },
    )
    assert result["gates"]["Gate_1_1b_Title_And_Date"]["passed"] is False
    assert any("must include meeting name 'Apex Global Strategy Alignment'" in f for f in result["failures"])


def test_gate_1_1b_historical_non_live_passes():
    """Historical transcripts without is_live flag do not trigger Gate 1.1b title failures."""
    transcript = """# Subject: Architecture Review -- HITL-Resolved Transcript

**Date:** 09/14/2026
**Attendees:**
- PartnerAdvisory: Reid Schenk

---

Reid Schenk: [00:01] Historical review dialogue preserving existing formatting without live date requirement.
"""
    result = run_qa_gate_1(
        f1_text=transcript,
        metadata={
            "is_live": False,
            "duration_minutes": 5,
            "roster": [{"name": "Reid Schenk"}],
        },
    )
    assert result["gates"]["Gate_1_1b_Title_And_Date"]["passed"] is True
    assert not any("Gate 1.1b" in f for f in result["failures"])


def test_gate_2_1_compliant_live_subject_passes():
    """Gate 2.1 passes when live meeting recap # Subject: contains name and date."""
    recap = """# Subject: Apex Global Strategy Alignment -- Executive Strategic & Technical Brief | 10/12/2026

**Date:** 10/12/2026
**Attendees:**
- Reid Schenk (Enterprise Architect)

## Meeting Objective
Align architecture roadmaps and formalize deliverables for enterprise rollout.

## Key Discussion Points & Findings
- **ev-01**: Verified all pipeline stages emit deterministic receipts under execution.

## Action & Governance Ledger
- Reid Schenk: Publish migration playbook by 10/20/2026.

## Immediate Delivery Scope & Release Boundaries
- Scope bounded to core repository services.

## Recommendations & Decisions Required
- Ratified decision on unified test coverage.

## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown
- Reid Schenk: Architecture lead.
"""
    from dual_pipeline.transcript_qa_core.gate2 import _check_structural_completeness
    passed, sections, iso_dates, failures = _check_structural_completeness(
        recap,
        meta={
            "is_live": True,
            "meeting_name": "Apex Global Strategy Alignment",
        },
    )
    assert passed is True
    assert not any("Gate 2.1 Subject Audit" in f for f in failures)

    result = run_qa_gate_2(
        f2_text=recap,
        metadata={
            "is_live": True,
            "meeting_name": "Apex Global Strategy Alignment",
            "duration_minutes": 10,
        },
    )
    assert not any("Gate 2.1 Subject Audit" in f for f in result["failures"])


def test_gate_2_1_missing_date_fails():
    """Gate 2.1 fails when live meeting recap # Subject: omits date."""
    recap = """# Subject: Apex Global Strategy Alignment -- Executive Strategic & Technical Brief

**Date:** 10/12/2026
**Attendees:**
- Reid Schenk (Enterprise Architect)

## Meeting Objective
Align architecture roadmaps and formalize deliverables for enterprise rollout.
"""
    from dual_pipeline.transcript_qa_core.gate2 import _check_structural_completeness
    passed, sections, iso_dates, failures = _check_structural_completeness(
        recap,
        meta={
            "is_live": True,
            "meeting_name": "Apex Global Strategy Alignment",
        },
    )
    assert passed is False
    assert any("must include meeting date" in f for f in failures)


def test_gate_2_1_missing_name_fails():
    """Gate 2.1 fails when live meeting recap # Subject: omits meeting name."""
    recap = """# Subject: Executive Strategic & Technical Brief | 10/12/2026

**Date:** 10/12/2026
**Attendees:**
- Reid Schenk (Enterprise Architect)

## Meeting Objective
Align architecture roadmaps and formalize deliverables for enterprise rollout.
"""
    from dual_pipeline.transcript_qa_core.gate2 import _check_structural_completeness
    passed, sections, iso_dates, failures = _check_structural_completeness(
        recap,
        meta={
            "is_live": True,
            "meeting_name": "Apex Global Strategy Alignment",
        },
    )
    assert passed is False
    assert any("must include meeting name 'Apex Global Strategy Alignment'" in f for f in failures)


def test_gate_2_1_historical_recap_passes():
    """Historical recaps without is_live flag do not enforce live date pipe in # Subject:."""
    recap = """# Subject: Engineering Architecture Sync

**Date:** 09/14/2026
**Attendees:**
- Reid Schenk (PartnerAdvisory)

## Meeting Objective
Historical review and architecture alignment.

## Key Discussion Points & Findings
- **ev-01**: Historical finding.

## Action & Governance Ledger
- Reid Schenk: Historical action by 09/20/2026.
"""
    result = run_qa_gate_2(
        f2_text=recap,
        metadata={
            "is_live": False,
            "duration_minutes": 10,
        },
    )
    assert not any("Gate 2.1 Subject Audit" in f for f in result["failures"])


def test_output_hierarchy_live_transcript_validation():
    """Output hierarchy container item classification validates live transcript line 1."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        # 1. Compliant file
        compliant_file = tmp_path / "01_test_live_transcript.md"
        compliant_file.write_text("# Live Meeting Transcript: Test Meeting | 10/12/2026\n\nContent", encoding="utf-8")
        violations = _classify_container_item(
            compliant_file,
            proj_name="project_nexus",
            meeting_date="10-12-2026",
            container_label="project_nexus/10-12-2026",
            art01_files=[],
            art02_files=[],
            art03_files=[],
            receipts_found={},
        )
        assert len(violations) == 0

        # 2. Invalid header prefix
        bad_header_file = tmp_path / "02_test_live_transcript.md"
        bad_header_file.write_text("# Random Meeting Title\n\nContent", encoding="utf-8")
        violations = _classify_container_item(
            bad_header_file,
            proj_name="project_nexus",
            meeting_date="10-12-2026",
            container_label="project_nexus/10-12-2026",
            art01_files=[],
            art02_files=[],
            art03_files=[],
            receipts_found={},
        )
        assert any("[INVALID_LIVE_TRANSCRIPT_HEADER]" in v for v in violations)

        # 3. Date mismatch
        mismatch_file = tmp_path / "03_test_live_transcript.md"
        mismatch_file.write_text("# Live Meeting Transcript: Test Meeting | 10/11/2026\n\nContent", encoding="utf-8")
        violations = _classify_container_item(
            mismatch_file,
            proj_name="project_nexus",
            meeting_date="10-12-2026",
            container_label="project_nexus/10-12-2026",
            art01_files=[],
            art02_files=[],
            art03_files=[],
            receipts_found={},
        )
        assert any("[LIVE_TRANSCRIPT_DATE_MISMATCH]" in v for v in violations)
