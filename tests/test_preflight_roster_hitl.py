"""
tests/test_preflight_roster_hitl.py

Unit test suite for Wave 2: Pre-Flight Roster & Named Entity Grounding HITL Gate.
Validates that Stage U0 halts and raises an atomic HITLRequiredError whenever attendee
roles are missing or contain placeholder values, unless backed by operator confirmation
or authentic cryptographic receipts.
"""

from __future__ import annotations

import unittest
from typing import Any, Dict

from dual_pipeline.transcript_qa_core.gate1_alr import (
    check_ambiguity_lock_register_and_speaker_hitl,
)
from dual_pipeline.transcript_qa_core.gate2_titles import check_gate_2_12_attendee_titles
from dual_pipeline.transcript_stages_core.models import HITLRequiredError
from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary


class TestPreflightRosterHITL(unittest.TestCase):
    def setUp(self) -> None:
        self.u0 = U0IngestionBoundary()
        self.sample_transcript = """00:01:00
Alex Brannan: Hello everyone, welcome to the sync.
Amit Ayer: Thanks Alex, glad to be here.
"""

    def test_u0_raises_hitl_on_missing_role(self) -> None:
        """U0 must raise HITLRequiredError when attendee has missing/empty role."""
        metadata: Dict[str, Any] = {
            "meeting_name": "Test Sync",
            "date": "09/29/2026",
            "time": "03:00 PM",
            "classification": "confidential",
            "attendees": [
                {"name": "Unknown Person", "org": "Google"},  # No role provided
                {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead Client Partner"},
            ],
            "diarization_status": "NATIVE",
        }
        with self.assertRaises(HITLRequiredError) as ctx:
            self.u0.execute(self.sample_transcript, metadata)

        err = ctx.exception
        self.assertEqual(err.hitl_type, "role_clarification")
        self.assertTrue(len(err.options) >= 2)
        self.assertIn("Unknown Person", err.prompt)

    def test_u0_raises_hitl_on_placeholder_role(self) -> None:
        """U0 must raise HITLRequiredError when attendee has 'Role not listed' or 'title not provided'."""
        metadata: Dict[str, Any] = {
            "meeting_name": "Test Sync",
            "date": "09/29/2026",
            "time": "03:00 PM",
            "classification": "confidential",
            "attendees": [
                {"name": "External Guest", "org": "Google", "role": "Role not listed"},
                {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead Client Partner"},
            ],
            "diarization_status": "NATIVE",
        }
        with self.assertRaises(HITLRequiredError) as ctx:
            self.u0.execute(self.sample_transcript, metadata)

        err = ctx.exception
        self.assertEqual(err.hitl_type, "role_clarification")
        self.assertIn("External Guest", err.prompt)

    def test_u0_passes_with_operator_confirmation_override(self) -> None:
        """U0 passes if operator explicitly confirmed attendee roles."""
        metadata: Dict[str, Any] = {
            "meeting_name": "Test Sync",
            "date": "09/29/2026",
            "time": "03:00 PM",
            "classification": "confidential",
            "attendees": [
                {"name": "External Guest", "org": "Google", "role": "Role not listed"},
                {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead Client Partner"},
            ],
            "attendee_roles_confirmed_by_operator": True,
            "diarization_status": "NATIVE",
        }
        res = self.u0.execute(self.sample_transcript, metadata)
        self.assertIn("metadata", res)

    def test_u0_passes_when_all_roles_verified(self) -> None:
        """U0 passes cleanly when all attendees have verified, non-placeholder roles."""
        metadata: Dict[str, Any] = {
            "meeting_name": "Test Sync",
            "date": "09/29/2026",
            "time": "03:00 PM",
            "classification": "confidential",
            "attendees": [
                {"name": "Alex Brannan", "org": "Google", "role": "Partner Engineering Lead"},
                {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead Client Partner"},
            ],
            "diarization_status": "NATIVE",
        }
        res = self.u0.execute(self.sample_transcript, metadata)
        self.assertIn("metadata", res)

    def test_gate1_fails_closed_on_unbacked_placeholder_role(self) -> None:
        """Gate 1.7 fails closed if Output 01 has placeholder roles without HITL receipt."""
        f1_text = """# Forensic Transcript: Test Sync
**Date:** 09/29/2026

## Ambiguity Lock Register
| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |
|---|---|---|---|---|---|
| 1 | Speaker Attribution & Acoustic Grounding: Alex Brannan | [00:00:00 – Session End] | stmt-spk-01 | Dialogue turns attributed to verified participant Alex Brannan. | Multi-Tier Acoustic Isolation Protocol |
| 2 | Speaker Attribution & Acoustic Grounding: Amit Ayer | [00:00:00 – Session End] | stmt-spk-02 | Dialogue turns attributed to verified participant Amit Ayer. | Multi-Tier Acoustic Isolation Protocol |
| 3 | Enterprise Domain Lexicon | [00:00:00 – Session End] | stmt-lex-03 | Enterprise taxonomy. | Standard Enterprise Taxonomy Verification |

# Transcript Dialogue Turns
Alex Brannan -- Google (Role not listed): Hello everyone.
Amit Ayer -- PartnerAdvisory (Lead Client Partner): Hello Alex.
"""
        meta: Dict[str, Any] = {
            "attendees": [
                {"name": "Alex Brannan", "org": "Google", "role": "Role not listed"},
                {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead Client Partner"},
            ],
            "pre_enrichment_roles": {
                "Alex Brannan": "Role not listed",
                "Amit Ayer": "Lead Client Partner",
            },
        }
        passed, details, failures = check_ambiguity_lock_register_and_speaker_hitl(f1_text, meta)
        self.assertFalse(passed)
        self.assertTrue(any("AMBIGUITY_LOCK_REGISTER_NOT_HITL_BACKED" in f for f in failures))

    def test_gate2_12_detects_role_not_listed(self) -> None:
        """Gate 2.12 fails closed on '(Role not listed)' or '(title not provided in source metadata)'."""
        f2_text = """# Subject: Executive Recap | 09/29/2026
**Date:** 09/29/2026
**Attendees:**
Google:
- Dianne Eldridge (Role not listed)
PartnerAdvisory:
- Amit Ayer (Lead Client Partner)
"""
        passed, failures, details = check_gate_2_12_attendee_titles(f2_text)
        self.assertFalse(passed)
        self.assertTrue(any("placeholder" in f.lower() for f in failures))


if __name__ == "__main__":
    unittest.main()
