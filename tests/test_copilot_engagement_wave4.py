"""
tests/test_copilot_engagement_wave4.py

Unit tests for Wave 4: Verify the delivered words.
Covers:
- Invented durations fail despite shared words.
- Invented durations fail despite valid citation IDs.
- Invented reuse/dependencies fail despite shared words.
- Invented reuse/dependencies fail despite valid citation IDs.
- Supported durations and reuse succeed with authentic evidence.
- Successful factual answers require nonempty supporting evidence.
- Summaries receive equal checks.
- Suggestions remain labeled.
"""
from __future__ import annotations

import unittest
from typing import Any, Dict, List

from dual_pipeline.live_meeting_recorder_core.copilot_clause_verifier import (
    check_clause_grounding,
    enforce_deep_clause_verification,
    extract_durations,
)
from dual_pipeline.live_meeting_recorder_core.copilot_validator import (
    enforce_sentence_citations,
    validate_copilot_feedback,
)


class TestCopilotEngagementWave4Unit(unittest.TestCase):
    def setUp(self) -> None:
        self.secops_asrt: Dict[str, Any] = {
            "assertion_id": "asrt_secops_01",
            "predicate": "TRACKS",
            "subject_node_id": "team:secops",
            "object_literal": "service requests",
            "claim_text": "SecOps tracks all security service requests.",
        }
        self.sso_asrt: Dict[str, Any] = {
            "assertion_id": "asrt_sso_02",
            "predicate": "DEPENDS_ON",
            "subject_node_id": "project:jarvis",
            "object_literal": "Single Sign-On",
            "claim_text": "Jarvis depends on Single Sign-On for authentication.",
        }
        self.by_id: Dict[str, Dict[str, Any]] = {
            "asrt_secops_01": self.secops_asrt,
            "asrt_sso_02": self.sso_asrt,
        }
        self.assertions: List[Dict[str, Any]] = [self.secops_asrt, self.sso_asrt]

    def test_duration_extraction_normalization(self) -> None:
        """Verifies normalization of words and numbers in duration strings."""
        self.assertIn("2 weeks", extract_durations("We need two weeks for this."))
        self.assertIn("2 weeks", extract_durations("We need 2 weeks for this."))
        self.assertIn("3-5 days", extract_durations("The 3-5 days timebox."))
        self.assertIn("sprint 1", extract_durations("Blocked by Sprint 1."))

    def test_invented_duration_rejected_despite_shared_words(self) -> None:
        """Shared words like 'SecOps' and 'requests' cannot validate an invented two-week duration."""
        clause = "SecOps security service requests take two weeks."
        match = check_clause_grounding(clause, self.assertions, self.by_id)
        self.assertIsNone(match, "Invented duration must fail despite high token overlap")

    def test_invented_duration_rejected_despite_valid_citation_id(self) -> None:
        """Explicit citation ID cannot validate a clause with an invented duration."""
        clause = "SecOps security service requests take two weeks asrt_secops_01."
        match = check_clause_grounding(clause, self.assertions, self.by_id)
        self.assertIsNone(match, "Invented duration must fail even with an explicit valid citation ID")

    def test_invented_reuse_rejected_despite_shared_words(self) -> None:
        """Shared words like 'SecOps' cannot validate an unevidenced reuse claim."""
        clause = "Jarvis reuses SecOps security service request modules."
        match = check_clause_grounding(clause, self.assertions, self.by_id)
        self.assertIsNone(match, "Unsupported reuse claim must fail despite shared words")

    def test_invented_reuse_rejected_despite_valid_citation_id(self) -> None:
        """Explicit citation ID cannot validate an unevidenced reuse claim."""
        clause = "Jarvis reuses SecOps modules asrt_secops_01."
        match = check_clause_grounding(clause, self.assertions, self.by_id)
        self.assertIsNone(match, "Unsupported reuse claim must fail even with a valid citation ID")

    def test_supported_duration_and_reuse_accepted(self) -> None:
        """Assertions that genuinely specify durations and dependencies must pass validation."""
        asrt_timed: Dict[str, Any] = {
            "assertion_id": "asrt_timed_03",
            "predicate": "TRACKS",
            "subject_node_id": "team:secops",
            "object_literal": "two-week turnaround",
            "claim_text": "SecOps tracks security reviews within a two-week turnaround.",
        }
        by_id = {**self.by_id, "asrt_timed_03": asrt_timed}
        assertions = [*self.assertions, asrt_timed]

        # Valid duration
        hit_duration = check_clause_grounding("SecOps tracks reviews with two weeks turnaround.", assertions, by_id)
        self.assertEqual(hit_duration, "asrt_timed_03")

        # Valid reuse / dependency
        hit_dep = check_clause_grounding("Jarvis depends on Single Sign-On for authentication.", assertions, by_id)
        self.assertEqual(hit_dep, "asrt_sso_02")

    def test_successful_factual_answers_require_nonempty_evidence(self) -> None:
        """Factual answers without supporting evidence cannot succeed as verified facts."""
        parsed = {
            "has_guidance": True,
            "category": "TECHNICAL",
            "suggested_script": "Jarvis depends on Single Sign-On.",
            "supporting_details_bullets": ["Authentication requires SSO."],
        }
        # Empty evidence
        res = enforce_sentence_citations(parsed, verified_assertions=[], rigor_tier="VERIFIED")
        self.assertEqual(res["cited_assertion_ids"], [])
        self.assertEqual(res["supporting_details_bullets"], [])
        self.assertEqual(res["rigor_tier"], "UNVERIFIED")
        self.assertTrue(res["suggested_script"].startswith("Can we verify"))
        self.assertEqual(res["label"], "Clarification")
        self.assertTrue(res["is_suggestion"])

    def test_summaries_receive_equal_checks(self) -> None:
        """Speaker summaries receive sentence-level verification; ungrounded sentences are dropped."""
        parsed = {
            "has_guidance": True,
            "category": "TECHNICAL",
            "speaker_summary": "Jarvis depends on Single Sign-On. SecOps requires two weeks for reviews.",
            "suggested_script": "Jarvis depends on Single Sign-On.",
            "supporting_details_bullets": ["Jarvis depends on Single Sign-On."],
        }
        res = validate_copilot_feedback(
            parsed,
            verified_assertions=self.assertions,
            rigor_tier="VERIFIED",
        )
        self.assertIsNotNone(res)
        # Grounded sentence is kept, ungrounded two-week sentence is dropped
        self.assertIn("Jarvis depends on Single Sign-On", res["speaker_summary"])
        self.assertNotIn("two weeks", res["speaker_summary"])
        self.assertEqual(res["cited_assertion_ids"], ["asrt_sso_02"])

    def test_suggestions_remain_labeled(self) -> None:
        """Verified factual statements and suggestions are explicitly and correctly labeled."""
        factual_parsed = {
            "has_guidance": True,
            "category": "TECHNICAL",
            "suggested_script": "Jarvis depends on Single Sign-On.",
            "supporting_details_bullets": ["Jarvis depends on Single Sign-On."],
        }
        res_factual = validate_copilot_feedback(
            factual_parsed,
            verified_assertions=self.assertions,
            rigor_tier="VERIFIED",
        )
        self.assertEqual(res_factual["label"], "Verified Answer")
        self.assertFalse(res_factual["is_suggestion"])

        suggestion_parsed = {
            "has_guidance": True,
            "category": "STRATEGIC",
            "suggested_script": "Should we phase the charter into an initial 30-day spike?",
        }
        res_sugg = validate_copilot_feedback(suggestion_parsed)
        self.assertEqual(res_sugg["label"], "Suggestion")
        self.assertTrue(res_sugg["is_suggestion"])


if __name__ == "__main__":
    unittest.main()
