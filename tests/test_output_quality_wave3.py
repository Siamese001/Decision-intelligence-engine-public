"""
tests/test_output_quality_wave3.py

Wave 3 Acceptance Suite: Claim-level recap grounding, standalone headings,
verified metadata time, unapproved proposal labeling, action status and deadline
hygiene, and anti-synthetic action quota removal.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Dict

import pytest

from dual_pipeline.transcript_qa_core.gate2 import run_qa_gate_2
from dual_pipeline.transcript_qa_core.gate2_grounding import (
    check_claim_level_grounding,
    check_standalone_headings,
    check_verified_time,
)
from dual_pipeline.transcript_qa_core.recap_grounding_healer import (
    heal_action_ledger_status,
    heal_header_time,
    heal_standalone_headings,
    heal_unapproved_proposals_and_baselines,
)
from dual_pipeline.transcript_qa_core.recap_healer import heal_output_2_recap
from dual_pipeline.transcript_qa_core.recap_healer_helpers import (
    _format_action_table,
    _parse_action_row,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_neet_platform_account_survives_and_architecture_labeled_unapproved():
    """APEX_TRANS platform account survives while proposed Azure/TLS/SQL architecture is labeled unapproved."""
    neet_recap_path = REPO_ROOT / "output" / "apex_trans" / "09-11-2026" / "02_NEET_Insights_Hub_Tech_Services_Group_Interview_09-11-2026_0336PM_0437PM_ET_executive_recap.md"
    assert neet_recap_path.exists(), f"Missing APEX_TRANS fixture: {neet_recap_path}"
    raw_neet = neet_recap_path.read_text(encoding="utf-8")

    # 1. Platform account survives: verify core observed platforms are present
    assert "Pano AI Smoke Detection Deployment" in raw_neet
    assert "TechnoSilva 5-Day Wildfire Risk Modeling" in raw_neet
    assert "Legacy Firecaster Obsolescence" in raw_neet
    assert "DTN Weather & Lightning Verification Workflow" in raw_neet
    assert "NERC CIP Isolation & Point Viewer Historical EMS Telemetry" in raw_neet
    assert "TENS Outage Reporting & Next-Action Decision Support" in raw_neet
    assert "Shift Handoff Logs & Planned Outage Power App" in raw_neet

    # 2. Fabricated or unapproved architecture appearing without advisory labeling must fail Gate 2.15
    unlabeled_arch = (
        "## Target Architecture & Cybersecurity Isolation (Ingress & Data Contract)\n\n"
        "- **Approved Ingress Pattern:** Outbound HTTPS polling and secure webhook ingestion to an Azure cloud tenant "
        "hosted on the Apex Global corporate business network; zero inbound listening ports; TLS 1.3 encryption with OAuth 2.0.\n"
        "- **NERC CIP Logical Isolation:** Historical EMS alarm context is ingested exclusively via read-only SQL queries "
        "against the internal Point Viewer database on the corporate business network.\n"
        "- **Data Freshness, Retention & Fail-Closed Resilience:** 30-day historical event cache for post-trip review.\n"
    )
    passed, fails = check_claim_level_grounding(unlabeled_arch)
    assert passed is False
    assert any("[GATE_2_UNAPPROVED_ARCHITECTURE_PROPOSAL]" in f for f in fails)

    # 3. Running heal_output_2_recap normalizes the architecture section to an explicit advisor proposal
    healed_arch = heal_unapproved_proposals_and_baselines(unlabeled_arch)
    assert "(Advisor Proposal — Unapproved Assumptions)" in healed_arch
    assert "advisor-proposed hypotheses pending formal engineering and cybersecurity review" in healed_arch

    passed_healed, fails_healed = check_claim_level_grounding(healed_arch)
    assert passed_healed is True, f"Expected healed proposal to pass, failed: {fails_healed}"


def test_neet_scored_decision_matrix_and_baselines_hypotheses():
    """Scored decision matrix and baselines must be labeled as unapproved hypotheses."""
    unlabeled_matrix = (
        "## Candidate Integration Scored Decision Matrix\n\n"
        "| Evaluation Dimension (Weight) | Alternative A | Alternative B | Alternative C |\n"
        "|---|---|---|---|\n"
        "| Security Approval Complexity (20%) | 75/100 | 85/100 | 95/100 |\n"
        "| **Weighted Composite Score** | **73.9 / 100** | **86.6 / 100** | **70.0 / 100** |\n"
    )
    passed, fails = check_claim_level_grounding(unlabeled_matrix)
    assert passed is False
    assert any("[GATE_2_UNAPPROVED_SCORED_MATRIX]" in f for f in fails)

    healed_matrix = heal_unapproved_proposals_and_baselines(unlabeled_matrix)
    assert "advisor-proposed hypotheses pending formal engineering and cybersecurity review" in healed_matrix

    passed_healed, _ = check_claim_level_grounding(healed_matrix)
    assert passed_healed is True


def test_uncommitted_dates_and_unverified_action_status():
    """Uncommitted dates are unscheduled and unverified actions cannot be marked 'In Progress'."""
    unverified_action = (
        "## Action & Governance Ledger\n\n"
        "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
        "|---|---|---|---|---|---|---|\n"
        "| 1 | Action | Locate Jenny's retrospective slides in the Teams channel and share with team | Mike Homorody | Unscheduled (Post-meeting follow-up; no deadline committed) | [00:14:44–00:15:04] | In Progress |\n"
    )
    passed, fails = check_claim_level_grounding(unverified_action)
    assert passed is False
    assert any("[GATE_2_UNVERIFIED_ACTION_STATUS]" in f for f in fails)

    healed_action = heal_action_ledger_status(unverified_action)
    assert "Proposed" in healed_action
    assert "In Progress" not in healed_action

    passed_healed, _ = check_claim_level_grounding(healed_action)
    assert passed_healed is True


def test_conduit_metadata_time_and_standalone_headings():
    """Conduit uses metadata time and requires standalone headings."""
    conduit_meta_path = REPO_ROOT / "inputs" / "transcripts" / "pd_conduit_831_metadata.json"
    assert conduit_meta_path.exists(), f"Missing metadata: {conduit_meta_path}"
    conduit_meta = json.loads(conduit_meta_path.read_text(encoding="utf-8"))
    assert conduit_meta["time"] == "01:00 PM - 01:47 PM ET"

    conduit_recap_path = REPO_ROOT / "output" / "conduit" / "08-31-2026" / "Conduit_Lessons_Learned_Michael_PartnerAdvisory" / "02_Conduit_Lessons_Learned_Michael_PartnerAdvisory_08-31-2026_0100PM_0147PM_ET_executive_recap.md"
    assert conduit_recap_path.exists(), f"Missing Conduit recap: {conduit_recap_path}"
    raw_conduit = conduit_recap_path.read_text(encoding="utf-8")

    # 1. Heading check: raw Conduit has non-standalone heading at line 19
    # '# Key Discussion Points & Findings — **Project Pause Determinants...**'
    headings_ok, heading_fails = check_standalone_headings(raw_conduit)
    assert headings_ok is False
    assert any("[GATE_2_NON_STANDALONE_HEADING]" in f for f in heading_fails)

    # 2. Time check: raw Conduit has '**Time:** Not stated (ET)' despite metadata time
    time_ok, time_fails = check_verified_time(raw_conduit, conduit_meta)
    assert time_ok is False
    assert any("[GATE_2_TIME_UNVERIFIED]" in f for f in time_fails)

    # 3. Heal Conduit recap
    healed_conduit = heal_output_2_recap(raw_conduit, metadata=conduit_meta)

    # Verified time must now be present
    assert "**Time:** 01:00 PM - 01:47 PM ET" in healed_conduit
    time_healed_ok, time_healed_fails = check_verified_time(healed_conduit, conduit_meta)
    assert time_healed_ok is True, f"Time healing failed: {time_healed_fails}"

    # Heading must now be standalone
    assert "# Key Discussion Points & Findings\n\n- **Project Pause Determinants" in healed_conduit
    headings_healed_ok, headings_healed_fails = check_standalone_headings(healed_conduit)
    assert headings_healed_ok is True, f"Headings healing failed: {headings_healed_fails}"


def test_mandy_heading_and_ungrounded_engagement_percentage():
    """Mandy heading stands alone and ungrounded completion percentages are rejected."""
    unsupported_recap = (
        "# Subject: Mandy LENS Catch-up -- Executive Brief\n\n"
        "**Date:** 09/24/2026\n"
        "**Time:** 10:29 AM - 10:45 AM ET\n"
        "**Attendees:**\n"
        "- Amit Ayer (PartnerAdvisory)\n"
        "- harper Hoffman (Apex Global Enterprises)\n\n"
        "# Key Discussion Points & Findings — **Operational Scope:** Field team achieved 45% completion on work orders.\n\n"
        "## Action & Governance Ledger\n\n"
        "The supplied entries establish no assigned actions, owner commitments, or deadlines.\n"
    )
    # Headings check
    h_ok, h_fails = check_standalone_headings(unsupported_recap)
    assert h_ok is False
    assert any("[GATE_2_NON_STANDALONE_HEADING]" in f for f in h_fails)

    # Ungrounded percentage check
    c_ok, c_fails = check_claim_level_grounding(unsupported_recap)
    assert c_ok is False
    assert any("[GATE_2_UNGROUNDED_ENGAGEMENT_PERCENTAGE]" in f for f in c_fails)

    # Healer decouples heading and grounds completion
    mandy_meta = {
        "date": "09/24/2026",
        "time": "10:29 AM - 10:45 AM ET",
        "meeting_name": "Mandy LENS Catch-up",
        "attendees": ["Amit Ayer", "harper Hoffman"],
    }
    healed = heal_output_2_recap(unsupported_recap, metadata=mandy_meta)
    assert "# Key Discussion Points & Findings\n\n- **Operational Scope:**" in healed
    h_healed_ok, _ = check_standalone_headings(healed)
    assert h_healed_ok is True


def test_removal_of_action_quotas_and_implied_owners():
    """Verify removal of hardcoded action quotas and implied attendee owners."""
    # When zero actions are found, do NOT fabricate synthetic Amit Ayer action
    table_text = _format_action_table([], "The meeting was an exploratory technical discussion.", None)
    assert "Technical Value-Driver Decomposition" not in table_text
    assert "No forward commitments or action items were assigned during this technical sync." in table_text

    # Implied owners are not guessed from names in bullet text without explicit assignment
    parsed = _parse_action_row(
        title="Alex Sanchez and Jake Stark evaluated dual-model extraction alignment",
        body="The team reviewed OCR models across the evaluation dataset.",
        sorted_candidates=["Alex Sanchez", "Jake Stark"],
    )
    # Since there was no explicit assignment verb (assigned to, will deliver, etc.), parsed must be None
    assert parsed is None


def test_appendix_healer_contracts_preserved():
    """Verify that appendix healing contracts (Rule C-21 Gold Standard) continue to function and pass."""
    sample_recap = (
        "# Subject: Architecture Sync -- Executive Recap\n\n"
        "**Date:** 09/30/2026\n"
        "**Time:** 02:13 PM - 03:07 PM ET\n"
        "**Attendees:**\n"
        "PartnerAdvisory:\n"
        "- Amit Ayer (Lead Enterprise Architect)\n"
        "- Jake Stark (UI Lead)\n\n"
        "# Meeting Objective\n\n"
        "The team met to review architecture and pipeline performance.\n\n"
        "# Key Discussion Points & Findings\n\n"
        "- **Pipeline Architecture:** Evaluated end-to-end extraction stages.\n\n"
        "# Action & Governance Ledger\n\n"
        "The supplied entries establish no assigned actions, owner commitments, or deadlines.\n"
    )
    meta = {
        "date": "09/30/2026",
        "time": "02:13 PM - 03:07 PM ET",
        "duration": "54m",
        "attendees": ["Amit Ayer", "Jake Stark"],
    }
    healed = heal_output_2_recap(sample_recap, metadata=meta)
    assert "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown" in healed
    assert "Amit Ayer" in healed
    assert "Jake Stark" in healed

    # Check that gate 2 runs cleanly
    gate_res = run_qa_gate_2(healed, meta=meta)
    assert gate_res["gates"]["Gate_2_8_Appendix_Completeness"]["passed"] is True
    assert gate_res["gates"]["Gate_2_15_Claim_Grounding_And_Headings"]["passed"] is True
