"""
tests/test_duration_ssot_and_gate_durability.py

Unit tests for Single Source of Truth (SSOT) meeting duration resolution
and QA Gate 2 durability against silent gate evasion.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from dual_pipeline.transcript_duration_helper import (
    parse_time_duration_minutes,
    parse_transcript_timestamp_duration_minutes,
    resolve_meeting_duration,
)
from dual_pipeline.transcript_qa_core.gate2 import run_qa_gate_2


class TestDurationSSOTAndGateDurability(unittest.TestCase):
    def test_parse_time_duration_minutes(self) -> None:
        self.assertEqual(parse_time_duration_minutes("04:32 PM - 04:59 PM ET"), 27)
        self.assertEqual(parse_time_duration_minutes("05:02 PM - 05:39 PM ET (Duration: ~37m)"), 37)
        self.assertEqual(parse_time_duration_minutes("10:00 AM - 11:15 AM"), 75)
        self.assertEqual(parse_time_duration_minutes("09:00 AM - 10:00 AM ET"), 60)
        self.assertIsNone(parse_time_duration_minutes("10:00 AM ET"))
        self.assertIsNone(parse_time_duration_minutes(None))

    def test_parse_transcript_timestamp_duration(self) -> None:
        text = """
        **Speaker A:** [00:00] Welcome everyone.
        **Speaker B:** [15:30] Midway point.
        **Speaker A:** [26:56] Wrapping up now.
        """
        self.assertEqual(parse_transcript_timestamp_duration_minutes(text), 27)

        hour_text = """
        **A:** [01:00:00] Start.
        **B:** [01:45:00] End.
        """
        self.assertEqual(parse_transcript_timestamp_duration_minutes(hour_text), 45)

        self.assertIsNone(parse_transcript_timestamp_duration_minutes("[00:00] Only one timestamp."))
        self.assertIsNone(parse_transcript_timestamp_duration_minutes(""))

    def test_resolve_meeting_duration_priority(self) -> None:
        # 1. Explicit metadata takes precedence
        self.assertEqual(resolve_meeting_duration({"duration_minutes": 45}), 45)
        self.assertEqual(resolve_meeting_duration({"meeting_duration_minutes": 30}), 30)

        # 2. Pattern in text
        text_with_dur = "**Time:** 10:00 AM - 10:30 AM (Duration: ~30m)"
        self.assertEqual(resolve_meeting_duration({}, text_with_dur), 30)

        # 3. Time range in metadata['time']
        self.assertEqual(resolve_meeting_duration({"time": "04:32 PM - 04:59 PM ET"}), 27)

        # 4. Time range in text
        text_range = "**Time:** 04:32 PM - 04:59 PM ET (Start: 04:32 PM | End: 04:59 PM)"
        self.assertEqual(resolve_meeting_duration({}, text_range), 27)

    def test_gate_2_fails_defective_0911_recap_without_metadata(self) -> None:
        """
        Crucial RCA regression test:
        The defective 09-11 recap must NOT pass Gate 2 merely because metadata is omitted.
        Gate 2 must extract 27m duration from the text and fail due to missing Strategic Synthesis.
        """
        f2_path = (
            Path(__file__).resolve().parent.parent
            / "output"
            / "project_lens"
            / "09-11-2026"
            / "02_Project_LENS_Phase_II_Catchup_09-11-2026_0432PM_0459PM_ET_executive_recap.md"
        )
        if f2_path.exists():
            f2_text = f2_path.read_text(encoding="utf-8")
            # Strip Strategic Synthesis if present to test defective scenario
            defective_text = re.sub(
                r"(?is)#+\s+Strategic Synthesis.*?(?=\n#|\Z)", "", f2_text
            )
            res = run_qa_gate_2(defective_text)  # NO metadata passed
            self.assertEqual(res["status"], "FAIL")
            self.assertEqual(res["gates"]["Gate_2_5_Word_Count_Bounds"]["duration_minutes"], 27)
            self.assertFalse(res["gates"]["Gate_2_6_Strategic_Synthesis_Matrix"]["passed"])
            self.assertTrue(any("Strategic Synthesis" in f for f in res["failures"]))

    def test_gate_2_passes_complete_0914_recap_without_metadata(self) -> None:
        """The complete 09-14 recap must resolve 37m duration and pass Gate 2 cleanly."""
        f2_path = (
            Path(__file__).resolve().parent.parent
            / "output"
            / "project_lens"
            / "10-02-2026"
            / "02_Project_Lens_SOW1_Observability_Catchup_10-02-2026_0502PM_0539PM_ET_executive_recap.md"
        )
        if f2_path.exists():
            f2_text = f2_path.read_text(encoding="utf-8")
            res = run_qa_gate_2(f2_text)  # NO metadata passed
            self.assertEqual(res["status"], "PASS")
            self.assertEqual(res["gates"]["Gate_2_5_Word_Count_Bounds"]["duration_minutes"], 37)
            self.assertTrue(res["gates"]["Gate_2_6_Strategic_Synthesis_Matrix"]["passed"])
            self.assertEqual(res["failures"], [])


if __name__ == "__main__":
    unittest.main()
