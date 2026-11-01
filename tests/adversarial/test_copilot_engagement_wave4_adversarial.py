"""
tests/adversarial/test_copilot_engagement_wave4_adversarial.py

Wave 4 adversarial and edge-case acceptance tests:
Covers mandatory categories per .agents/rules/e2e-and-edge-case-coverage.md:
1. Exact production regression: Pin the exact October 7 incident claims (two-week SecOps, LENS reuse, 3-5 day timebox).
2. Boundary payload: Empty strings, 0-byte fields, null/empty bullet lists, empty assertion collections.
3. Malformed input: Corrupted candidate dicts, non-string scripts, non-list bullets, malformed assertions.
4. Invariant bypass attempt: Valid assertion IDs attached to fabricated durations, invented reuse, or cross-tenant claims.
5. Rollback & recovery: Empty evidence and timeouts must fail closed and never restore rejected claims.
"""
from __future__ import annotations

import unittest
from typing import Any, Dict, List

from dual_pipeline.live_meeting_recorder_core.copilot_clause_verifier import (
    GROUNDING_QUESTION_FALLBACK,
    check_clause_grounding,
)
from dual_pipeline.live_meeting_recorder_core.copilot_validator import (
    enforce_sentence_citations,
    validate_copilot_feedback,
)


class TestCopilotEngagementWave4Adversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.secops_asrt: Dict[str, Any] = {
            "assertion_id": "asrt_secops_01",
            "predicate": "TRACKS",
            "subject_node_id": "team:secops",
            "object_literal": "service requests",
            "claim_text": "SecOps tracks all security service requests.",
            "engagement_id": "project:jarvis",
        }
        self.jarvis_sso_asrt: Dict[str, Any] = {
            "assertion_id": "asrt_jarvis_sso_02",
            "predicate": "DEPENDS_ON",
            "subject_node_id": "project:jarvis",
            "object_literal": "Single Sign-On",
            "claim_text": "Jarvis depends on Single Sign-On for authentication.",
            "engagement_id": "project:jarvis",
        }
        self.assertions = [self.secops_asrt, self.jarvis_sso_asrt]
        self.by_id = {
            "asrt_secops_01": self.secops_asrt,
            "asrt_jarvis_sso_02": self.jarvis_sso_asrt,
        }

    def test_adversarial_exact_production_regression_three_issues(self) -> None:
        """
        CATEGORY: Exact production regression.
        Pins the 3 unevidenced incident claims from the October 7 RCA:
        1. Two-week turnaround for SecOps (when source only says SecOps tracks requests)
        2. LENS/Nexus reuse relationship (when no reuse relationship exists)
        3. 3-5 day timebox for GCP hosting (when 3-5 days was an unverified proposal)
        All 3 must be rejected and stripped from the copilot output.
        """
        candidate = {
            "has_guidance": True,
            "category": "TECHNICAL",
            "suggested_script": (
                "SecOps security service requests take two weeks. "
                "Jarvis reuses LENS spatial upload modules. "
                "Alternative GCP hosting requires a 3-5 days timebox."
            ),
            "supporting_details_bullets": [
                "SecOps reviews take two weeks.",
                "Jarvis reuses LENS spatial upload modules.",
                "3-5 days timebox required for GCP hosting.",
            ],
            "speaker_summary": (
                "SecOps requires two weeks for reviews. "
                "Jarvis reuses LENS components."
            ),
        }

        validated = validate_copilot_feedback(
            candidate,
            verified_assertions=self.assertions,
            rigor_tier="VERIFIED",
            engagement_id="project:jarvis",
        )

        self.assertIsNotNone(validated)
        # All 3 unevidenced claims must be stripped
        script = validated["suggested_script"]
        self.assertNotIn("two weeks", script)
        self.assertNotIn("reuses LENS", script)
        self.assertNotIn("3-5 days", script)

        # Bullets must be empty because all 3 bullets were ungrounded
        self.assertEqual(validated["supporting_details_bullets"], [])

        # Fallback question must be triggered since no factual clauses survived
        self.assertEqual(validated["suggested_script"], GROUNDING_QUESTION_FALLBACK)
        self.assertEqual(validated["cited_assertion_ids"], [])
        self.assertEqual(validated["rigor_tier"], "UNVERIFIED")
        self.assertEqual(validated["label"], "Clarification")
        self.assertTrue(validated["is_suggestion"])

    def test_adversarial_boundary_payloads(self) -> None:
        """
        CATEGORY: Boundary payload.
        Handles empty strings, zero-length fields, empty lists, and empty assertion sets.
        """
        empty_candidate = {
            "has_guidance": True,
            "category": "TECHNICAL",
            "suggested_script": "",
            "supporting_details_bullets": [],
            "speaker_summary": "",
        }

        # Null/empty assertion set
        res_empty = validate_copilot_feedback(
            empty_candidate,
            verified_assertions=[],
            rigor_tier="VERIFIED",
            engagement_id="project:jarvis",
        )
        self.assertIsNotNone(res_empty)
        self.assertEqual(res_empty["cited_assertion_ids"], [])
        self.assertEqual(res_empty["supporting_details_bullets"], [])
        self.assertEqual(res_empty["label"], "Clarification")

        # Single word candidate
        single_word = {
            "has_guidance": True,
            "category": "TECHNICAL",
            "suggested_script": "Architecture.",
            "supporting_details_bullets": ["Architecture."],
        }
        res_single = validate_copilot_feedback(
            single_word,
            verified_assertions=self.assertions,
            rigor_tier="VERIFIED",
            engagement_id="project:jarvis",
        )
        self.assertIsNotNone(res_single)
        # Single word without evidence is dropped
        self.assertEqual(res_single["cited_assertion_ids"], [])
        self.assertEqual(res_single["suggested_script"], GROUNDING_QUESTION_FALLBACK)

    def test_adversarial_malformed_input_structures(self) -> None:
        """
        CATEGORY: Malformed input.
        Verifies resilience against corrupted structures, wrong types, and missing keys.
        """
        malformed_candidate = {
            "has_guidance": True,
            "suggested_script": 12345,  # Non-string
            "supporting_details_bullets": "Not a list",  # Non-list
            "speaker_summary": None,
        }

        # Should not crash and should fail closed safely
        res = validate_copilot_feedback(
            malformed_candidate,
            verified_assertions=self.assertions,
            rigor_tier="VERIFIED",
            engagement_id="project:jarvis",
        )
        self.assertIsNotNone(res)
        self.assertEqual(res["cited_assertion_ids"], [])
        self.assertEqual(res["label"], "Clarification")

        # Corrupted assertions missing claim_text, predicate, or id
        corrupted_assertions = [
            {"invalid_key": True},
            {"assertion_id": None, "claim_text": None},
        ]
        match = check_clause_grounding(
            "SecOps tracks requests.",
            corrupted_assertions,  # type: ignore[arg-type]
            {},
        )
        self.assertIsNone(match)

    def test_adversarial_invariant_bypass_attempt(self) -> None:
        """
        CATEGORY: Invariant bypass attempt.
        Attempts to bypass verification by embedding valid assertion IDs next to
        completely fabricated durations and unevidenced reuse relationships.
        """
        # Fabricated duration with valid assertion ID
        bypass_duration = "SecOps security requests take 6 months asrt_secops_01."
        match_dur = check_clause_grounding(bypass_duration, self.assertions, self.by_id)
        self.assertIsNone(match_dur, "Fabricated duration must fail despite valid assertion ID")

        # Fabricated reuse with valid assertion ID
        bypass_reuse = "Jarvis reuses project Nexus spatial upload pipeline asrt_jarvis_sso_02."
        match_reuse = check_clause_grounding(bypass_reuse, self.assertions, self.by_id)
        self.assertIsNone(match_reuse, "Fabricated reuse must fail despite valid assertion ID")

        # Cross-tenant claim tagging unrelated assertion ID
        bypass_cross = "LENS Spatial File Upload is blocked asrt_secops_01."
        match_cross = check_clause_grounding(bypass_cross, self.assertions, self.by_id)
        self.assertIsNone(match_cross, "Cross-tenant claim must fail despite valid assertion ID")

    def test_adversarial_rollback_and_recovery_fail_closed(self) -> None:
        """
        CATEGORY: Rollback & recovery.
        Verifies that when verification fails or empty evidence is encountered,
        rejected claims cannot be restored or repopulated from fallback fields.
        """
        candidate = {
            "has_guidance": True,
            "category": "TECHNICAL",
            "suggested_script": "Jarvis requires a two-week turnaround for SecOps reviews.",
            "supporting_details_bullets": ["SecOps turnaround is two weeks."],
            "snap_cue": "SecOps takes two weeks",
            "opportunity": "Speed up the two-week turnaround",
            "risk_trap": "Two-week turnaround blocks sprint 1",
        }

        # Validate with assertions that don't support two weeks
        validated = validate_copilot_feedback(
            candidate,
            verified_assertions=self.assertions,
            rigor_tier="VERIFIED",
            engagement_id="project:jarvis",
        )

        self.assertIsNotNone(validated)
        self.assertEqual(validated["cited_assertion_ids"], [])
        self.assertEqual(validated["supporting_details_bullets"], [])
        self.assertNotIn("two-week", validated["suggested_script"])
        self.assertNotIn("two weeks", validated["suggested_script"])

        # Crucially: fallback logic must NOT repopulate bullets from snap_cue/opportunity/risk_trap!
        for b in validated.get("supporting_details_bullets", []):
            self.assertNotIn("two-week", b)
            self.assertNotIn("two weeks", b)


if __name__ == "__main__":
    unittest.main()
