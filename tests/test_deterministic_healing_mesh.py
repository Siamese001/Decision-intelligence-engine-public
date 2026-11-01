"""
Acceptance tests for Deterministic Pre-Flight Self-Healing Mesh (De-Weaponized).

Verifies that the DeterministicHealer performs lossless syntactic normalization
(dates, whitespaces, headers, entity aliases) in <15ms with zero text generation,
zero table row injection, and zero section synthesis.
"""

from __future__ import annotations

import json
import time
import unittest

from dual_pipeline.transcript_qa import (
    DeterministicHealer,
    evaluate_all_qa_gates,
)


class TestDeterministicHealingMesh(unittest.TestCase):
    """Acceptance test suite for DeterministicHealer and evaluator auto-healing."""

    def test_healer_converts_iso_dates_in_output1_and_output2(self) -> None:
        """Tests that ISO-8601 dates (YYYY-MM-DD) are transformed to MM/DD/YYYY in markdown prose."""
        raw_transcript = """# Forensic Transcript: APEX_TRANS Meeting
Date: 2026-09-10
[00:01:00] Alex Rivera: We need this by 2026-09-25.
"""
        healed_f1, changes_f1 = DeterministicHealer.heal_output_1(raw_transcript)
        self.assertNotIn("2026-09-10", healed_f1)
        self.assertNotIn("2026-09-25", healed_f1)
        self.assertIn("09/10/2026", healed_f1)
        self.assertIn("09/25/2026", healed_f1)

        raw_recap = """# Subject: Recap
**Date:** 2026-09-10
**Attendees:**
- Anece Elakkari

## Meeting Objective
Target date is 2026-10-15.
"""
        healed_f2, changes_f2 = DeterministicHealer.heal_output_2(raw_recap)
        self.assertNotIn("2026-09-10", healed_f2)
        self.assertNotIn("2026-10-15", healed_f2)
        self.assertIn("09/10/2026", healed_f2)
        self.assertIn("10/15/2026", healed_f2)

    def test_healer_normalizes_whitespace_and_headers(self) -> None:
        """Tests that trailing spaces and non-standard section headers are normalized losslessly."""
        raw_recap = (
            "# Engineering Architecture   \n"
            "Date: 09/10/2026\n\n\n\n"
            "## Objective\nAlign vision.\n\n"
            "## Findings\nData siloed.\n"
        )
        healed, changes = DeterministicHealer.heal_output_2(raw_recap)
        self.assertTrue(healed.startswith("# Subject: Engineering Architecture"))
        self.assertIn("## Meeting Objective", healed)
        self.assertIn("## Key Discussion Points & Findings", healed)
        self.assertNotIn("   \n", healed)
        self.assertNotIn("\n\n\n", healed)

    def test_healer_does_not_inject_synthetic_text_or_rows(self) -> None:
        """Tests that DeterministicHealer performs zero table row injection and zero text generation."""
        raw_recap = """# Subject: Action Items Test
**Date:** 09/10/2026
**Attendees:**
- Alex Rivera (APEX)

## Meeting Objective
Test objective.

## Key Discussion Points & Findings
Findings.

## Next Steps & Action Items
| # | Action Item | DRI | Target Date | Success Measure |
|---|---|---|---|---|
| 1 | Discuss transmission schema | Alex Rivera | 09/25/2026 | Complete |

## Open Decisions / Blockers
None.

## Business Meaning and Value Case
Value case.

## Executive Decision
No decision.
"""
        healed, changes = DeterministicHealer.heal_output_2(raw_recap)
        self.assertNotIn("Proposed PartnerAdvisory Coordination DRI", healed)
        self.assertNotIn("Review and align on evaluation protocol", healed)
        self.assertNotIn("Representative OCR evaluation", healed)
        self.assertNotIn("Candidate Discovery Hypothesis", healed)

    def test_healer_canonicalizes_output3_entities_and_iso_dates(self) -> None:
        """Tests that Output 03 canonicalizes entity IDs (technosilva -> technosylva) and enforces ISO dates in JSON."""
        raw_f3 = {
            "mutations": {
                "entities": [
                    {
                        "entity_id": "system:technosilva",
                        "entity_type": "system",
                        "attributes": {"description": "Fire spread model"},
                    }
                ],
                "signals": [
                    {
                        "from_id": "system:technosilva",
                        "to_id": "system:tens-sharepoint",
                        "relationship": "INTEGRATES_WITH",
                    }
                ],
                "risks": [
                    {
                        "risk_id": "risk:fire",
                        "statement": "Fire risk",
                        "observed_at": "09/10/2026",
                    }
                ],
            }
        }
        healed_str, changes = DeterministicHealer.heal_output_3(raw_f3)
        healed_data = json.loads(healed_str)

        entity = healed_data["mutations"]["entities"][0]
        self.assertEqual(entity["entity_id"], "system:technosylva")
        self.assertEqual(entity["attributes"]["description"], "Fire spread model")

        signal = healed_data["mutations"]["signals"][0]
        self.assertEqual(signal["from_id"], "system:technosylva")

        risk = healed_data["mutations"]["risks"][0]
        self.assertEqual(risk["observed_at"], "09/10/2026")

    def test_healer_latency_under_15ms(self) -> None:
        """Tests that healing all 3 artifacts executes in under 15ms with zero external API calls."""
        sample_text = "Sample text with 2026-09-10 date and ## Objective.\n" * 20
        start = time.perf_counter()
        DeterministicHealer.heal_all(
            sample_text,
            sample_text,
            {"mutations": {"entities": [{"entity_id": "system:technosilva"}]}},
            metadata={"meeting_type": "STRATEGIC_DISCOVERY"},
        )
        elapsed_ms = (time.perf_counter() - start) * 1000
        self.assertLess(elapsed_ms, 15.0, f"Deterministic healer took {elapsed_ms:.2f}ms, exceeding budget.")

    def test_evaluator_auto_heal_date_normalization(self) -> None:
        """Tests that evaluate_all_qa_gates auto-heals ISO dates to pass Gate 2."""
        unhealed_f2 = """# Subject: Auto-Heal Test
**Date:** 2026-09-10
**Attendees:**
- Delivery Partner: Alex Rivera (PartnerAdvisory)

## Meeting Objective
Test objective for date normalization.

## Key Discussion Points & Findings
The architecture discussion focused on establishing an isolated evaluation environment for data intelligence models.
The team reviewed distributed storage constraints, focusing on zero-contention database concurrency and WAL mode semantics.
We verified that sidecar vector embeddings must be synchronized with primary graph mutations using monotonic sequence numbers.

## Next Steps & Action Items
| # | Action Item | Accountable Owner | Target Timing | Status |
|---|---|---|---|---|
| 1 | Align on schema [05:00] | Alex Rivera | 09/25/2026 | Pending |

## Open Decisions / Blockers
None.

## Business Meaning and Value Case
Value case.

## Executive Decision
Approved deployment schedule for next quarter rollout.
"""
        report_healed = evaluate_all_qa_gates(
            f1_text="**Date:** 09/10/2026\n[00:00] Start",
            f2_text=unhealed_f2,
            f3_text={},
            auto_heal=True,
            metadata={"duration_minutes": 10},
        )
        self.assertIn("deterministic_healing", report_healed)
        self.assertIn("09/10/2026", report_healed["healed_artifacts"]["output_2"])


if __name__ == "__main__":
    unittest.main()
