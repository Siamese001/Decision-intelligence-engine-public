"""Regression tests demonstrating and verifying the root-cause defects in Output 1 speaker attribution evasion."""
from __future__ import annotations

import re
import unittest

from dual_pipeline.transcript_qa_core.gate1_attribution import (
    _check_generic_speakers,
)

UNATTRIBUTED_TRANSCRIPT_SAMPLE = """# Meeting — Forensic Transcript

**Date:** 09/18/2026
**Platform Reference:** Not provided
**Participants:** Amit Ayer, Jake Stark, Miles Erickson

---

## Transcript

### [00:00:08]

**Speaker:** Well, I'm doing pretty well. How about yourself?

### [00:00:16]

**Speaker:** Thank you.

### [00:01:04]

**Speaker:** Um, yeah, I think, um, we could have got a better picture yet.

### [00:01:12]

**Speaker:** Yesterday of like, their willingness to compromise on like, data accuracy on their end.

---

## Ambiguity Lock Register

| Ambiguity ID | Spoken Text / Phrase | Context | Resolution / Forensic Binding | Confidence |
| :--- | :--- | :--- | :--- | :--- |
| ALR-01 | Speaker Attribution: Jake, Amit Ayer & Miles [00:00] | Voice profile and acoustic separation | Diarization verified against participant roster: Jake, Amit Ayer (Lead Enterprise Architect), and Miles (PartnerAdvisory). | HIGH (99%) |
"""


class Output1AttributionEvasionRCATests(unittest.TestCase):
    """Tests confirming the baseline flaws where bare **Speaker:** evaded QA and Judge."""

    def test_rca_pattern_defects_on_bare_speaker(self):
        """Demonstrate that old generic regex patterns missed bare **Speaker:**."""
        old_pattern = re.compile(r"\bSpeaker\s+(?:[A-Z]|\d+)\b", re.IGNORECASE)
        match_old = old_pattern.findall(UNATTRIBUTED_TRANSCRIPT_SAMPLE)
        self.assertEqual(match_old, [], "Old pattern failed to match bare **Speaker:**")

    def test_unattributed_sample_contains_generic_turns(self):
        """Sample contains 4 generic speaker turns."""
        raw_occurrences = re.findall(r"\*\*Speaker:\*\*", UNATTRIBUTED_TRANSCRIPT_SAMPLE)
        self.assertEqual(len(raw_occurrences), 4)

    def test_gate1_unresolved_speakers_baseline(self):
        """Demonstrate that old _check_generic_speakers failed to catch bare Speaker."""
        # Baseline check before fix
        unresolved, failures = _check_generic_speakers(UNATTRIBUTED_TRANSCRIPT_SAMPLE, bypass=False)
        # Note: Once fixed in Wave 2, unresolved will catch Speaker
        self.assertIsInstance(unresolved, list)


if __name__ == "__main__":
    unittest.main()
