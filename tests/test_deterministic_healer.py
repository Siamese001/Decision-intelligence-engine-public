"""
Unit and acceptance tests for De-Weaponized DeterministicHealer (Wave 2).

Verifies:
1. Zero text generation, zero table row injection, and zero section synthesis.
2. Lossless syntactic normalization: ISO dates -> MM/DD/YYYY, whitespace, and markdown headings.
3. Deterministic execution latency < 5ms.
4. Explicit audit logging via normalization_audit_log.
"""

from __future__ import annotations

import time
import unittest

from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer


class TestDeterministicHealer(unittest.TestCase):
    def setUp(self) -> None:
        DeterministicHealer.clear_audit_log()

    def test_lossless_date_normalization(self) -> None:
        text = "Meeting on 2026-09-18 with next sync on 2026-09-25."
        healed, changes = DeterministicHealer.heal_output_2(text)
        self.assertIn("09/18/2026", healed)
        self.assertIn("09/25/2026", healed)
        self.assertNotIn("2026-09-18", healed)
        self.assertNotIn("2026-09-25", healed)
        audit_log = DeterministicHealer.get_audit_log()
        self.assertTrue(any(e["event"] == "date_normalized" for e in audit_log))

    def test_lossless_whitespace_normalization(self) -> None:
        text = "# Subject: Test   \n\n\n\n## Meeting Objective\nBody text with trailing spaces.   \n"
        healed, changes = DeterministicHealer.heal_output_2(text)
        self.assertNotIn("   \n", healed)
        self.assertNotIn("\n\n\n", healed)
        audit_log = DeterministicHealer.get_audit_log()
        self.assertTrue(any(e["event"] == "whitespace_normalized" for e in audit_log))

    def test_markdown_header_normalization(self) -> None:
        text = "# Architectural Strategy\n\n## Objective\nAlign on target architecture.\n\n## Findings\nEvaluated CDC."
        healed, changes = DeterministicHealer.heal_output_2(text)
        self.assertTrue(healed.startswith("# Subject: Architectural Strategy"))
        self.assertIn("## Meeting Objective", healed)
        self.assertIn("## Key Discussion Points & Findings", healed)
        audit_log = DeterministicHealer.get_audit_log()
        self.assertTrue(any(e["event"] == "subject_normalized" for e in audit_log))
        self.assertTrue(any(e["event"] == "section_normalized" for e in audit_log))

    def test_zero_text_generation_or_table_row_injection(self) -> None:
        """Healer must NEVER inject fake action rows, DRIs, or synthetic paragraphs."""
        raw_recap = (
            "# Subject: Clean Test\n"
            "**Date:** 09/18/2026\n\n"
            "## Meeting Objective\nScope.\n\n"
            "## Next Steps & Action Items\n"
            "| # | Action Item | DRI |\n"
            "|---|---|---|\n"
        )
        healed, _ = DeterministicHealer.heal_output_2(raw_recap)
        # Verify no synthetic default rows or fake DRIs were added
        self.assertNotIn("Proposed PartnerAdvisory Coordination DRI", healed)
        self.assertNotIn("Lead Enterprise Architect", healed)
        self.assertNotIn("Review and align on evaluation protocol", healed)
        self.assertNotIn("Candidate Discovery Hypothesis", healed)
        self.assertNotIn("Representative OCR evaluation", healed)

    def test_latency_under_5ms(self) -> None:
        sample = "# Strategy Sync\nDate: 2026-09-18\n\n## Objective\nTest.\n" * 15
        start = time.perf_counter()
        DeterministicHealer.heal_all(sample, sample, {"mutations": {"entities": []}})
        elapsed_ms = (time.perf_counter() - start) * 1000
        self.assertLess(elapsed_ms, 5.0, f"Healer exceeded 5ms budget: {elapsed_ms:.2f}ms")

    def test_audit_log_structure(self) -> None:
        text = "# Title\nDate: 2026-09-18\n\n## Objective\nPlan."
        DeterministicHealer.heal_output_2(text, metadata={"meeting_date": "2026-09-18"})
        log = DeterministicHealer.get_audit_log()
        self.assertGreater(len(log), 0)
        for entry in log:
            self.assertIn("event", entry)
            self.assertIn("rule", entry)
            self.assertIn("detail", entry)
            self.assertIn("timestamp", entry)


if __name__ == "__main__":
    unittest.main()
