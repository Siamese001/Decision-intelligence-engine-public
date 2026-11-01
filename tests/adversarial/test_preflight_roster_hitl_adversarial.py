"""
tests/adversarial/test_preflight_roster_hitl_adversarial.py

Adversarial test suite for Wave 2: Pre-Flight Roster & Named Entity Grounding HITL Gate.
Tests boundary conditions, obfuscations, and evasion attempts.
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


class TestPreflightRosterAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.u0 = U0IngestionBoundary()
        self.raw_transcript = "00:01:00\nAlice: Hello\nBob: Hi Alice\n"

    def test_adversarial_whitespace_and_casing_variations(self) -> None:
        """Adversarial variations of placeholder titles must still trigger HITLRequiredError."""
        bad_roles = [
            "   ",
            "\t\n",
            "[ROLE NOT LISTED]",
            "role not listed",
            "(title not provided in source metadata)",
            "Role Not Listed",
            "Participant",
            "Consulting Team Member",
        ]
        for role in bad_roles:
            with self.subTest(role=role):
                meta: Dict[str, Any] = {
                    "meeting_name": "Adversarial Sync",
                    "date": "09/29/2026",
                    "time": "03:00 PM",
                    "classification": "confidential",
                    "attendees": [
                        {"name": "Tricky User", "org": "Google", "role": role},
                        {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead Client Partner"},
                    ],
                    "diarization_status": "NATIVE",
                }
                with self.assertRaises(HITLRequiredError) as ctx:
                    self.u0.execute(self.raw_transcript, meta)
                self.assertEqual(ctx.exception.hitl_type, "role_clarification")
                self.assertIn("Tricky User", ctx.exception.prompt)

    def test_single_bad_apple_in_large_roster_triggers_atomic_hitl(self) -> None:
        """In a roster of 10 attendees, exactly one placeholder triggers an atomic 1-of-1 HITL prompt."""
        attendees = [
            {"name": f"Engineer {i}", "org": "Google", "role": "Senior Cloud Engineer"}
            for i in range(1, 10)
        ]
        attendees.append({"name": "Unspecified Person", "org": "Google", "role": "Role not listed"})
        meta: Dict[str, Any] = {
            "meeting_name": "Large Sync",
            "date": "09/29/2026",
            "time": "03:00 PM",
            "classification": "confidential",
            "attendees": attendees,
            "diarization_status": "NATIVE",
        }
        with self.assertRaises(HITLRequiredError) as ctx:
            self.u0.execute(self.raw_transcript, meta)

        self.assertIn("Unspecified Person", ctx.exception.prompt)
        self.assertIn("1 of 1", ctx.exception.prompt)
        self.assertTrue(meta.get("invalidate_prior_outputs"))

    def test_f1_dialogue_fallback_catches_stripped_metadata_evasion(self) -> None:
        """If caller scrubs pre_enrichment_roles, Gate 1.7 must inspect f1 text and fail closed."""
        f1_text = """# Forensic Transcript: Test
**Date:** 09/29/2026

## Ambiguity Lock Register
| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |
|---|---|---|---|---|---|
| 1 | Speaker Attribution & Acoustic Grounding: Chad Brockman | [00:00:00 – Session End] | stmt-spk-01 | Attributed to Chad Brockman. | Multi-Tier Acoustic Isolation Protocol |
| 2 | Speaker Attribution & Acoustic Grounding: Amit Ayer | [00:00:00 – Session End] | stmt-spk-02 | Attributed to Amit Ayer. | Multi-Tier Acoustic Isolation Protocol |
| 3 | Enterprise Domain Lexicon | [00:00:00 – Session End] | stmt-lex-03 | Enterprise taxonomy. | Standard Enterprise Taxonomy Verification |

# Transcript Dialogue Turns
Chad Brockman -- Google (Role not listed): Good morning everyone.
Amit Ayer -- PartnerAdvisory (Lead Client Partner): Good morning Chad.
"""
        # meta completely lacks pre_enrichment_roles
        meta: Dict[str, Any] = {
            "attendees": [
                {"name": "Chad Brockman", "org": "Google"},
                {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead Client Partner"},
            ]
        }
        passed, _, failures = check_ambiguity_lock_register_and_speaker_hitl(f1_text, meta)
        self.assertFalse(passed)
        self.assertTrue(any("AMBIGUITY_LOCK_REGISTER_NOT_HITL_BACKED" in f for f in failures))

    def test_gate2_12_catches_all_placeholder_title_variants(self) -> None:
        """Gate 2.12 catches bracketed, parenthesized, and raw Role Not Listed phrases."""
        variants = [
            "(role not listed)",
            "[role not listed]",
            "(title not provided in source metadata)",
            "[title not provided in source metadata]",
            "Role Not Listed",
        ]
        for v in variants:
            with self.subTest(variant=v):
                text = f"""# Subject: Recap | 09/29/2026
**Date:** 09/29/2026
**Attendees:**
Google:
- Test Attendee {v}
PartnerAdvisory:
- Amit Ayer (Lead Client Partner)
"""
                passed, failures, _ = check_gate_2_12_attendee_titles(text)
                self.assertFalse(passed)
                self.assertTrue(any("placeholder" in f.lower() for f in failures))


if __name__ == "__main__":
    unittest.main()
