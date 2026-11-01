"""
tests/test_copilot_anti_hallucination.py

Tests for Rule C-20 Anti-Hallucination Invariant and Rule C-19 Career Firewall in Live Copilot:
1. Intercepts unauthorized commercial commitments ("for free", "at no charge", "without extra cost").
2. Intercepts unauthorized rapid delivery commitments ("deliver in 2 days", "ship tomorrow").
3. Detects and sanitizes conflicts with verified GraphDB ownership (OWNS / LEADS).
4. Verifies Agree-and-Bound and RACI clarification fallbacks.
5. Verifies canonical prompt registration of Rule C-20.
"""
import unittest
from typing import Any, Dict
from unittest.mock import MagicMock

from dual_pipeline.live_meeting_recorder_core.copilot import (
    COPILOT_SYSTEM_PROMPT,
)
from dual_pipeline.live_meeting_recorder_core.copilot_validator import (
    AGREE_AND_BOUND_FALLBACK_SCRIPT,
    AGREE_AND_BOUND_FALLBACK_SNAP,
    VERIFY_OWNERSHIP_FALLBACK_SCRIPT,
    VERIFY_OWNERSHIP_FALLBACK_SNAP,
    validate_copilot_feedback,
)


class TestCopilotAntiHallucination(unittest.TestCase):
    def test_prompt_contains_c20_and_exact_constraints(self):
        """Verifies that COPILOT_SYSTEM_PROMPT enforces Rule C-20 and preserves Quick Snap constraints."""
        self.assertIn("Rule C-20 Anti-Hallucination Invariant", COPILOT_SYSTEM_PROMPT)
        self.assertIn("Rule C-19 Career Firewall", COPILOT_SYSTEM_PROMPT)
        self.assertIn("snap_cue <= 8 words", COPILOT_SYSTEM_PROMPT)
        self.assertIn("suggested_script <= 12 words", COPILOT_SYSTEM_PROMPT)
        self.assertIn("risk_trap <= 8 words", COPILOT_SYSTEM_PROMPT)

    def test_unauthorized_free_commercial_promise_intercepted(self):
        """Verify that promising free work is intercepted and converted to Agree-and-Bound."""
        raw_feedback: Dict[str, Any] = {
            "has_guidance": True,
            "category": "TECHNICAL",
            "snap_cue": "Promise free build",
            "suggested_script": "We can build the custom reporting gateway for free without extra cost.",
            "risk_trap": "Scope creep",
            "confidence": 0.9,
        }
        validated = validate_copilot_feedback(raw_feedback)
        self.assertIsNotNone(validated)
        self.assertEqual(validated["suggested_script"], AGREE_AND_BOUND_FALLBACK_SCRIPT)
        self.assertEqual(validated["snap_cue"], AGREE_AND_BOUND_FALLBACK_SNAP)
        self.assertEqual(validated["category"], "COMMERCIAL")
        self.assertEqual(validated["risk_trap"], "Unbudgeted commitment")

    def test_unauthorized_rapid_timeline_intercepted(self):
        """Verify that promising delivery in 2 days or tomorrow is intercepted."""
        raw_feedback: Dict[str, Any] = {
            "has_guidance": True,
            "category": "STRATEGIC",
            "snap_cue": "Fast delivery",
            "suggested_script": "We can deliver in 2 days without issues.",
            "risk_trap": "Tight timeline",
            "confidence": 0.9,
        }
        validated = validate_copilot_feedback(raw_feedback)
        self.assertIsNotNone(validated)
        self.assertEqual(validated["suggested_script"], AGREE_AND_BOUND_FALLBACK_SCRIPT)
        self.assertEqual(validated["snap_cue"], AGREE_AND_BOUND_FALLBACK_SNAP)
        self.assertEqual(validated["category"], "COMMERCIAL")

    def test_c19_career_firewall_intercepted(self):
        """Verify that internal career leverage markers trigger executive RACI fallback."""
        raw_feedback: Dict[str, Any] = {
            "has_guidance": True,
            "category": "STRATEGIC",
            "snap_cue": "Use leverage",
            "suggested_script": "Use this as a networking_wedge for compensation_trajectory.",
            "risk_trap": "Exposure",
            "confidence": 0.9,
        }
        validated = validate_copilot_feedback(raw_feedback)
        self.assertIsNotNone(validated)
        self.assertEqual(validated["suggested_script"], VERIFY_OWNERSHIP_FALLBACK_SCRIPT)
        self.assertEqual(validated["snap_cue"], VERIFY_OWNERSHIP_FALLBACK_SNAP)
        self.assertEqual(validated["category"], "ALIGNMENT")

    def test_graphdb_ownership_conflict_intercepted(self):
        """Verify that claiming an unverified owner contrary to GraphDB triggers RACI clarification."""
        mock_graph = MagicMock()
        mock_graph.assertions = [
            {
                "subject_node_id": "person:amit_ayer",
                "predicate": "OWNS",
                "object_node_id": "system:power_bi_gateway",
            }
        ]
        mock_graph.nodes = {
            "person:amit_ayer": {"canonical_name": "Amit Ayer"},
            "system:power_bi_gateway": {"canonical_name": "Power BI Gateway"},
        }

        raw_feedback: Dict[str, Any] = {
            "has_guidance": True,
            "category": "ALIGNMENT",
            "snap_cue": "Assign ownership",
            "suggested_script": "Dave is leading the system:power_bi_gateway and owns delivery.",
            "risk_trap": "Misaligned RACI",
            "confidence": 0.9,
        }
        validated = validate_copilot_feedback(raw_feedback, in_memory_graph=mock_graph)
        self.assertIsNotNone(validated)
        self.assertEqual(validated["suggested_script"], VERIFY_OWNERSHIP_FALLBACK_SCRIPT)
        self.assertEqual(validated["snap_cue"], VERIFY_OWNERSHIP_FALLBACK_SNAP)
        self.assertEqual(validated["risk_trap"], "Unverified ownership")

    def test_valid_executive_script_passes_untouched(self):
        """Verify that compliant executive guidance is preserved without alteration."""
        raw_feedback: Dict[str, Any] = {
            "has_guidance": True,
            "category": "STRATEGIC",
            "snap_cue": "Anchor on two-week spike",
            "suggested_script": "Should we phase the charter into an initial grounded-contract spike before locking commitments?",
            "risk_trap": "Unscoped milestone extension without funding",
            "confidence": 0.95,
        }
        validated = validate_copilot_feedback(raw_feedback)
        self.assertIsNotNone(validated)
        self.assertEqual(
            validated["suggested_script"],
            "Should we phase the charter into an initial grounded-contract spike before locking commitments?",
        )
        self.assertEqual(validated["snap_cue"], "Anchor on two-week spike")
        self.assertEqual(validated["category"], "STRATEGIC")

    def test_sentence_level_citation_enforcement_in_feedback(self):
        """Verify that validate_copilot_feedback with verified_assertions drops ungrounded statements and retains cited ones."""
        verified = [
            {
                "assertion_id": "asrt_valid_01",
                "predicate": "DELIVERS",
                "subject_node_id": "team_core",
                "object_literal": "phase1",
                "claim_text": "Team core delivers phase1 by end of quarter.",
            }
        ]
        raw_feedback: Dict[str, Any] = {
            "has_guidance": True,
            "category": "STRATEGIC",
            "snap_cue": "Phase 1 delivery",
            "suggested_script": "Team core delivers phase1 by end of quarter asrt_valid_01. Random hallucination that has no backing.",
            "supporting_details_bullets": [
                "Team core delivers phase1 by end of quarter.",
                "Hallucinated bullet point.",
            ],
            "confidence": 0.95,
        }
        validated = validate_copilot_feedback(
            raw_feedback,
            verified_assertions=verified,
            rigor_tier="VERIFIED",
        )
        self.assertIsNotNone(validated)
        self.assertIn("Team core delivers phase1", validated["suggested_script"])
        self.assertNotIn("Random hallucination", validated["suggested_script"])
        self.assertEqual(len(validated["supporting_details_bullets"]), 1)
        self.assertEqual(validated["cited_assertion_ids"], ["asrt_valid_01"])
        self.assertEqual(validated["rigor_tier"], "VERIFIED")


if __name__ == "__main__":
    unittest.main()
