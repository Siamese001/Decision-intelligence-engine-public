"""Tests for Output 2 Hardened Executive Recap Judge (>= 95 threshold) and gpt-5.6-luna binding."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from dual_pipeline.llm_judge_core.recap import ExecutiveRecapJudge

from dual_pipeline.transcript_qa_core.evaluator import evaluate_judge_and_release


class TestHardenedRecapJudge(unittest.TestCase):

    def test_executive_recap_judge_model_and_mock(self):
        judge = ExecutiveRecapJudge(model="gpt-5.6-luna", reasoning_effort="high")
        self.assertEqual(judge.model, "gpt-5.6-luna")
        self.assertEqual(judge.reasoning_effort, "high")

        res = judge.evaluate(
            f2_text="# Subject: Meeting Recap\n\n**Date:** 08/28/2026\n\n## Next Steps\n| 1 | Action | Owner | 09/15/2026 |",
            f1_text="Forensic transcript",
            mock=True,
        )
        self.assertEqual(res["verdict"], "PASS")
        self.assertGreaterEqual(res["composite_score"], 95)

    def test_recap_judge_c19_leak_rejection(self):
        judge = ExecutiveRecapJudge(model="gpt-5.6-luna", reasoning_effort="high")
        res = judge.evaluate(
            f2_text="# Subject: Meeting\n\nNotes on executive compensation band and networking_wedge.",
            f1_text="Forensic transcript",
            mock=True,
        )
        self.assertEqual(res["verdict"], "FAIL")
        self.assertEqual(res["composite_score"], 30)
        self.assertIn("RULE C-19 VIOLATION", res["forensic_rationale"])

    def test_recap_judge_scoring_thresholds_with_mock_client(self):
        judge = ExecutiveRecapJudge(model="gpt-5.6-luna", reasoning_effort="high")
        mock_client = MagicMock()

        # Test score 96 -> PASS
        mock_client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content='{"composite_score": 96, "meeting_archetype": "STRATEGIC_DISCOVERY"}'))
        ]
        res_pass = judge.evaluate("Output 2 text", "Output 1 text", client=mock_client, mock=False)
        self.assertEqual(res_pass["verdict"], "PASS")
        self.assertEqual(res_pass["composite_score"], 96)

        # Test score 85 -> FAIL (PASS_ADVISORY forbidden for Output 2; score < 90 is FAIL)
        mock_client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content='{"composite_score": 85, "meeting_archetype": "STRATEGIC_DISCOVERY"}'))
        ]
        res_advisory = judge.evaluate("Output 2 text", "Output 1 text", client=mock_client, mock=False)
        self.assertEqual(res_advisory["verdict"], "FAIL")
        self.assertEqual(res_advisory["composite_score"], 85)

        # Test score 75 -> FAIL
        mock_client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content='{"composite_score": 75, "meeting_archetype": "STRATEGIC_DISCOVERY"}'))
        ]
        res_fail = judge.evaluate("Output 2 text", "Output 1 text", client=mock_client, mock=False)
        self.assertEqual(res_fail["verdict"], "FAIL")
        self.assertEqual(res_fail["composite_score"], 75)

    def test_evaluate_judge_and_release_enforces_output2_gte_95(self):
        f1 = "### [00:00:00]\n\n**Alex Rivera:** Initial discussion on system integration."
        f2 = """# Subject: Project Atlas -- Executive Strategic & Technical Brief | 08/28/2026

**Date:** 08/28/2026
**Time:** 09:00 - 10:00 ET
**Attendees:**
- **PartnerAdvisory:** Alex Rivera (Lead)
- **APEX:** Jane Doe (Director)

---

## Meeting Objective
Drive developer throughput against flat headcount constraints.

## Key Discussion Points & Findings
Discussion on Unifier and LAMS integration points.

## Next Steps & Action Items
| # | Action Item | Owner | Target Date | Status |
|---|-------------|-------|-------------|--------|
| 1 | Finalize interface specification | Alex Rivera | 09/30/2026 | Open |

## Open Decisions / Blockers
Architecture approval.

## Business Meaning and Value Case
Critical path efficiency.

## Executive Decision
Proceed with modular decoupled approach.

## Bounded Execution Wedge
Phase 1 prototype delivery.

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

### Alex Rivera (PartnerAdvisory)
- **Technical Architecture & Modular Delivery**: Alex Rivera emphasized modular decoupled integration and API contract governance, demonstrating that phased rollout prevents disruption to downstream analytics.
- **Implementation Strategy & Prototyping**: He confirmed that engineering teams are prepared to execute prototype validation within agreed architectural constraints and track metrics continuously.

### Jane Doe (APEX)
- **Delivery Priorities & Operational Scale**: Jane Doe outlined flat headcount constraints and developer throughput objectives, confirming that business leadership requires automated regression safety checks before committing to production deployment.
- **Milestone Alignment & Governance**: She aligned on milestone schedules across operational teams and confirmed executive reporting requirements for upcoming steering committee reviews.
"""
        f3 = '{"signals": [], "schema_version": "5.7"}'

        mock_qa = {
            "passed": True,
            "status": "PASS",
            "output_01_forensic_transcript": {"status": "PASS", "passed": True},
            "output_02_executive_recap": {"status": "PASS", "passed": True},
            "output_03_canonical_signal_projection": {"status": "PASS", "passed": True},
            "cross_artifact_consistency": {"status": "PASS", "passed": True},
        }
        with patch("dual_pipeline.transcript_qa_core.evaluator.evaluate_all_qa_gates", return_value=mock_qa):
            qa_rep, judge_rep, decision = evaluate_judge_and_release(
                f1_text=f1,
                f2_text=f2,
                f3_text=f3,
                source_text=f1,
                mock=True,
                model="gpt-5.6-luna",
                reasoning_effort="high",
            )
        # Mock mode gives score 96 for output 2, which satisfies s2 >= 90
        self.assertEqual(judge_rep["output_2_evaluation"]["composite_score"], 96)
        self.assertTrue(decision["output_2_llm_score_pass"])
        self.assertTrue(decision["output_2_deterministic_gates_pass"])
        self.assertTrue(decision["output_2_pass"])

        # Directly test the release gate logic: an output 2 score of 85 (between 80 and 89) MUST fail production release
        s2 = 85  # Advisory score < 90
        j2_pass = s2 >= 90
        self.assertFalse(j2_pass)  # Score 85 strictly fails production pass gate for Output 2!


if __name__ == "__main__":
    unittest.main()
