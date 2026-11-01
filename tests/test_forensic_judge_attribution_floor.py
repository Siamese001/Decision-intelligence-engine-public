"""
tests/test_forensic_judge_attribution_floor.py

Verifies the hardened Forensic Transcript Judge Dimension 2 attribution floor:
- Transcripts exceeding 15% generic un-attributed speaker turns (Speaker 1/2) must fail Dimension 2 (capped at <= 5/25)
  and receive an automatic composite FAIL (<70/100).
- Transcripts with multi-tier attribution (PartnerAdvisory vs. NEA and named participants) pass cleanly.
"""
import unittest

from dual_pipeline.llm_judge_core.forensic import (
    _assign_forensic_verdict,
    _check_generic_speaker_ratio,
)

GENERIC_TRANSCRIPT = """# Meeting -- Forensic Transcript

**Date:** 08/27/2026
**Participants:** Cameron Mercer, Chakra Sankaraiah

---
**Speaker 1** [00:00:00]: Good morning everyone.
**Speaker 2** [00:00:05]: Good morning Stefan.
**Speaker 1** [00:00:10]: Let us discuss the operating model.
**Speaker 2** [00:00:15]: The pod model is not working out.
**Speaker 1** [00:00:20]: We need multifaceted engineers.
**Speaker 2** [00:00:25]: Under vertical tech leads.

---
## Ambiguity Lock Register
| Lock ID | Item |
"""

ATTRIBUTED_TRANSCRIPT = """# Meeting -- Forensic Transcript

**Date:** 08/27/2026
**Participants:** Cameron Mercer, Chakra Sankaraiah

---
**Cameron Mercer (PartnerAdvisory)** [00:00:00]: Good morning everyone.
**Chakra Sankaraiah (NEA)** [00:00:05]: Good morning Stefan.
**Cameron Mercer (PartnerAdvisory)** [00:00:10]: Let us discuss the operating model.
**Chakra Sankaraiah (NEA)** [00:00:15]: The pod model is not working out.
**Cameron Mercer (PartnerAdvisory)** [00:00:20]: We need multifaceted engineers.
**Chakra Sankaraiah (NEA)** [00:00:25]: Under vertical tech leads.

---
## Ambiguity Lock Register
| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |
|---|---|---|---|---|---|
| 1 | Speaker Attribution & Acoustic Grounding | [00:00:00–00:00:25] | stmt-spk-01 | Cameron Mercer (PartnerAdvisory) and Chakra Sankaraiah (NEA) verified via acoustic matching and roster alignment. | Multi-Tier Acoustic Isolation Protocol |
"""


class TestForensicJudgeAttributionFloor(unittest.TestCase):
    def test_generic_speaker_ratio_detection(self):
        """Verify that un-attributed generic speaker ratio triggers when >15%."""
        is_excessive, ratio, generic_cnt, total_cnt = _check_generic_speaker_ratio(GENERIC_TRANSCRIPT)
        self.assertTrue(is_excessive)
        self.assertEqual(total_cnt, 6)
        self.assertEqual(generic_cnt, 6)
        self.assertEqual(ratio, 1.0)

    def test_attributed_speaker_ratio_detection(self):
        """Verify that attributed transcript has 0% generic speaker ratio."""
        is_excessive, ratio, generic_cnt, total_cnt = _check_generic_speaker_ratio(ATTRIBUTED_TRANSCRIPT)
        self.assertFalse(is_excessive)
        self.assertEqual(total_cnt, 6)
        self.assertEqual(generic_cnt, 0)
        self.assertEqual(ratio, 0.0)

    def test_attribution_floor_caps_dimension_2_and_composite(self):
        """Verify that an overly lenient judge score is capped to FAIL when generic ratio >15%."""
        lenient_judge_response = {
            "dimension_1_forensic_grounding_and_fidelity": 25,
            "dimension_2_attribution_and_room_mic_hygiene": 25,
            "dimension_3_chronological_and_timestamp_integrity": 25,
            "dimension_4_asr_cleanup_and_domain_preservation": 25,
            "composite_score": 100,
            "verdict": "PASS",
            "forensic_rationale": "Verbatim transcript matched reference perfectly.",
            "actionable_remediation_diff": "",
            "grounding_citations": [],
        }

        result = _assign_forensic_verdict(lenient_judge_response, f1_text=GENERIC_TRANSCRIPT)
        self.assertLessEqual(result["dimension_2_attribution_and_room_mic_hygiene"], 5)
        self.assertLessEqual(result["composite_score"], 69)
        self.assertEqual(result["verdict"], "FAIL")
        self.assertIn("ATTRIBUTION FLOOR HARD FAILURE", result["forensic_rationale"])
        self.assertIn("Post-process dialogue turns with multi-tier attribution", result["actionable_remediation_diff"])

    def test_attribution_floor_preserves_attributed_transcript(self):
        """Verify that an attributed transcript passes without being capped."""
        judge_response = {
            "dimension_1_forensic_grounding_and_fidelity": 25,
            "dimension_2_attribution_and_room_mic_hygiene": 24,
            "dimension_3_chronological_and_timestamp_integrity": 25,
            "dimension_4_asr_cleanup_and_domain_preservation": 25,
            "composite_score": 99,
            "verdict": "PASS",
            "forensic_rationale": "Attribution and fidelity verified.",
            "actionable_remediation_diff": "",
            "grounding_citations": [],
        }

        result = _assign_forensic_verdict(judge_response, f1_text=ATTRIBUTED_TRANSCRIPT)
        self.assertEqual(result["dimension_2_attribution_and_room_mic_hygiene"], 24)
        self.assertEqual(result["composite_score"], 99)
        self.assertEqual(result["verdict"], "PASS")


if __name__ == "__main__":
    unittest.main()

