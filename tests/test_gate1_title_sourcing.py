"""
tests/test_gate1_title_sourcing.py

Wave 2 F-03: Speaker title sourcing advisory tests.
Validates that _check_speaker_title_grounding (via check_attribution_hygiene)
flags titles that appear in Output 1 with no manifest source or timestamp anchor.
"""

import unittest

from dual_pipeline.transcript_qa_core.gate1_attribution import check_attribution_hygiene

TRANSCRIPT_WITH_GROUNDED_TITLE = """
**Date:** 09/18/2026

**Participants:**
- Jake Stark (PartnerAdvisory, Solution Architect)
- Amit Ayer (PartnerAdvisory, Lead Enterprise Architect)
- Miles Erickson (NEA, Staff Engineer)

## Transcript

**[00:00]** Jake Stark: Let's get started.
**[00:30]** Amit Ayer: I'll review the architecture.
"""

TRANSCRIPT_WITH_FABRICATED_TITLE = """
**Date:** 09/18/2026

**Participants:**
- Jake Stark (PartnerAdvisory)
- Miles Erickson (NEA)

## Transcript

**[00:00]** Jake Stark: Let's proceed.
**[00:30]** Miles Erickson: The pipeline is solid.

## Appendix: Detailed Stakeholder Perspectives

### Miles Erickson - NEA, Software Engineer / Technical Contributor

**Pipeline Contribution**: Miles validated the architectural approach from the NEA perspective.
"""


class TestSpeakerTitleGrounding(unittest.TestCase):

    def test_title_with_manifest_source_passes_cleanly(self):
        meta = {
            "roster": [
                {"name": "Jake Stark", "role": "Solution Architect", "title_source": "manifest"},
                {"name": "Amit Ayer", "role": "Lead Enterprise Architect", "title_source": "manifest"},
            ],
        }
        passed, unresolved, attributed, failures = check_attribution_hygiene(
            TRANSCRIPT_WITH_GROUNDED_TITLE, meta
        )
        # No UNGROUNDED_TITLE_ADVISORY should appear when title_source is manifest
        advisory_failures = [f for f in failures if "UNGROUNDED_TITLE_ADVISORY" in f]
        self.assertEqual(advisory_failures, [], f"Unexpected advisory: {advisory_failures}")

    def test_ungrounded_title_generates_advisory(self):
        meta = {
            "roster": [
                # Miles has a title but no title_source and no transcript timestamp near the title
                {"name": "Miles Erickson", "role": "Software Engineer / Technical Contributor"},
            ],
        }
        passed, unresolved, attributed, failures = check_attribution_hygiene(
            TRANSCRIPT_WITH_FABRICATED_TITLE, meta
        )
        advisory_failures = [f for f in failures if "UNGROUNDED_TITLE_ADVISORY" in f]
        # Advisory should fire because "Software Engineer" appears in output with no manifest source
        # Note: gate may still pass (advisory doesn't hard-fail)
        # But if title IS in output AND no source, we expect the advisory
        # (the advisory fires only if title appears in output)
        if "software engineer" in TRANSCRIPT_WITH_FABRICATED_TITLE.lower():
            self.assertTrue(
                len(advisory_failures) > 0,
                f"Expected UNGROUNDED_TITLE_ADVISORY for Miles' fabricated title. Got: {failures}"
            )

    def test_no_roster_no_advisory(self):
        """When no roster is provided, no title advisory should fire."""
        meta = {}
        passed, unresolved, attributed, failures = check_attribution_hygiene(
            TRANSCRIPT_WITH_GROUNDED_TITLE, meta
        )
        advisory = [f for f in failures if "UNGROUNDED_TITLE_ADVISORY" in f]
        self.assertEqual(advisory, [])

    def test_attendee_without_title_no_advisory(self):
        """Attendees with no role/title should not trigger the advisory."""
        meta = {
            "roster": [
                {"name": "Jake Stark"},  # no role field
            ],
        }
        passed, unresolved, attributed, failures = check_attribution_hygiene(
            TRANSCRIPT_WITH_GROUNDED_TITLE, meta
        )
        advisory = [f for f in failures if "UNGROUNDED_TITLE_ADVISORY" in f]
        self.assertEqual(advisory, [])


if __name__ == "__main__":
    unittest.main()
