"""Adversarial QA Gate 1 test suite verifying closure of all single-speaker collapse,
un-attributed partner organization, and autonomous silence bypass loopholes.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.speaker_attribution_core.collapse_detector import (
    check_speaker_skew_and_collapse,
)
from dual_pipeline.transcript_qa_core.gate1 import _check_duration_and_turn_density
from dual_pipeline.transcript_qa_core.gate1_attribution import check_attribution_hygiene
from dual_pipeline.transcript_stages_core.models import HITLRequiredError
from dual_pipeline.transcript_stages_core.silence_gap_classifier import (
    HITLDecisionStore,
    classify_silence_gaps,
)


class AdversarialGate1CollapseTests(unittest.TestCase):
    """Test suite for hardened QA Gate 1 controls."""

    def setUp(self) -> None:
        self.roster = [
            {"name": "Alex Brannan", "org": "Google"},
            {"name": "Amit Ayer", "org": "PartnerAdvisory"},
            {"name": "Drew Oakley", "org": "PartnerAdvisory"},
            {"name": "Dianne Eldridge", "org": "Google"},
            {"name": "Vani Eswarappa", "org": "Google"},
        ]
        self.meta = {
            "meeting_name": "PartnerAdvisory / Google - apex_global Alignment",
            "date": "09/29/2026",
            "project": "partner_alliances",
            "attendees": self.roster,
            "roster": self.roster,
            "meeting_duration_minutes": 36,
        }

    def test_single_speaker_collapse_under_10_turns_fails_closed(self) -> None:
        """Broken transcript with only 7 turns all attributed to Amit Ayer must fail Gate 1."""
        broken_transcript = (
            "# PartnerAdvisory / Google - apex_global Alignment\n\n"
            "**Participants:** Alex Brannan (Google); Amit Ayer (PartnerAdvisory); Drew Oakley (PartnerAdvisory); Dianne Eldridge (Google)\n\n"
            "### [00:01:12]\n\n**Amit Ayer:** Hello everyone, welcome to the partner sync.\n\n"
            "### [00:02:24]\n\n**Amit Ayer:** We have an agenda prepared today to review our alignment.\n\n"
            "### [00:03:36]\n\n**Amit Ayer:** Let's discuss HR for you and Scott McNeil.\n\n"
            "### [00:05:34]\n\n**Amit Ayer:** There are also Gemini Enterprise licenses to coordinate.\n\n"
            "### [00:07:46]\n\n**Amit Ayer:** PartnerAdvisory can deliver this in two weeks.\n\n"
            "### [00:08:40]\n\n**Amit Ayer:** Let's follow up on the nuclear project setup.\n\n"
            "### [00:10:19]\n\n**Amit Ayer:** Thank you everyone for joining.\n"
        )

        passed, unresolved, speakers_attr, failures = check_attribution_hygiene(
            broken_transcript, self.meta
        )

        self.assertFalse(passed, "Gate 1 should have failed closed on single-speaker collapse!")

        # 1. Single speaker collapse failure
        has_collapse_failure = any("SINGLE_SPEAKER_COLLAPSE_DETECTED" in f for f in failures)
        self.assertTrue(has_collapse_failure, f"Missing collapse failure. Failures: {failures}")

        # 2. Partner organization missing failure (Google has 0 turns)
        has_partner_failure = any("PARTNER_ORGANIZATION_MISSING" in f and "google" in f.lower() for f in failures)
        self.assertTrue(has_partner_failure, f"Missing partner org failure. Failures: {failures}")

        # 3. Speakers attributed should be False for Alex Brannan, Dianne Eldridge, etc.
        self.assertTrue(speakers_attr.get("Amit Ayer"))
        self.assertFalse(speakers_attr.get("Alex Brannan"))
        self.assertFalse(speakers_attr.get("Dianne Eldridge"))

    def test_collapse_detector_under_10_turns(self) -> None:
        """check_speaker_skew_and_collapse must flag 7-turn monologue with min_turns=2."""
        monologue = (
            "**[00:01:00]** **Amit Ayer:** Point 1.\n\n"
            "**[00:02:00]** **Amit Ayer:** Point 2.\n\n"
            "**[00:03:00]** **Amit Ayer:** Point 3.\n\n"
            "**[00:04:00]** **Amit Ayer:** Point 4.\n\n"
            "**[00:05:00]** **Amit Ayer:** Point 5.\n\n"
            "**[00:06:00]** **Amit Ayer:** Point 6.\n\n"
            "**[00:07:00]** **Amit Ayer:** Point 7.\n"
        )
        with self.assertRaises(HITLRequiredError) as ctx:
            check_speaker_skew_and_collapse(
                transcript=monologue,
                roster=self.roster,
                metadata=self.meta,
                min_turns=2,
            )
        self.assertIn("Single-speaker collapse detected", str(ctx.exception))

    def test_density_check_enforced_despite_gap_annotations(self) -> None:
        """check_turn_density_and_truncation must fail if 36-minute meeting has only 7 turns, even with pause notation."""
        sparse_transcript = (
            "**[00:01:00]** **Amit Ayer:** Start.\n\n"
            "[Session pause: 00:05:00 – 00:30:00]\n\n"
            "**[00:31:00]** **Amit Ayer:** End.\n"
        )
        passed, dur, turns, words, failures = _check_duration_and_turn_density(
            sparse_transcript, sparse_transcript, self.meta
        )
        self.assertFalse(passed, "Density check should fail for sparse transcript despite gap annotations")
        self.assertTrue(any("turn density failure" in f.lower() for f in failures))

    def test_silence_gap_classifier_prohibits_autonomous_resolution(self) -> None:
        """classify_silence_gaps must raise HITLRequiredError when interactive_resolver is None."""
        gap_transcript = (
            "### [00:05:00]\n\n**Amit Ayer:** Before pause.\n\n"
            "[Recording gap: 00:05:00 – 00:08:20]\n\n"
            "### [00:08:20]\n\n**Amit Ayer:** After pause.\n"
        )
        with tempfile.TemporaryDirectory() as td:
            isolated_store = HITLDecisionStore(ledger_path=Path(td) / "test_ledger.json")
            with self.assertRaises(HITLRequiredError):
                classify_silence_gaps(gap_transcript, self.meta, store=isolated_store, interactive_resolver=None)

    def test_legitimate_multi_speaker_transcript_passes(self) -> None:
        """Properly balanced multi-speaker transcript across organizations passes Gate 1."""
        legit_transcript = (
            "# PartnerAdvisory / Google - apex_global Alignment\n\n"
            "**Participants:** Alex Brannan (Google); Amit Ayer (PartnerAdvisory); Drew Oakley (PartnerAdvisory); Dianne Eldridge (Google)\n\n"
            "### [00:01:12]\n\n**Dianne Eldridge:** Thanks for introducing us.\n\n"
            "### [00:01:25]\n\n**Alex Brannan:** Yeah, Vani is joining going forward.\n\n"
            "### [00:02:10]\n\n**Amit Ayer:** Hey everyone, how are you doing?\n\n"
            "### [00:02:30]\n\n**Drew Oakley:** We had a good meeting this morning with the Google team.\n\n"
            "### [00:05:30]\n\n**Alex Brannan:** I will pull up the agenda.\n\n"
            "### [00:06:00]\n\n**Amit Ayer:** Sounds good, let's start with the HR initiative.\n"
        )
        passed, unresolved, speakers_attr, failures = check_attribution_hygiene(
            legit_transcript, self.meta
        )
        self.assertTrue(passed, f"Legitimate transcript failed: {failures}")
        self.assertEqual(len(failures), 0)
        self.assertTrue(speakers_attr["Amit Ayer"])
        self.assertTrue(speakers_attr["Alex Brannan"])
        self.assertTrue(speakers_attr["Dianne Eldridge"])
        self.assertTrue(speakers_attr["Drew Oakley"])


if __name__ == "__main__":
    unittest.main()
