"""
tests/test_c27_entity_resolution_adversarial.py

Adversarial and edge-case tests for Constraint C-27 entity resolution scoping,
ASR product homophone protection ("Sarah" -> "Sherpa"), and anti-fabrication
gating on unauthenticated off-roster entity resolutions.

Covers mandatory edge-case categories:
1. Boundary payload (empty transcript, zero-length roster, empty clarifications)
2. Malformed input (corrupt entity_clarifications entries, non-string fields)
3. Invariant bypass attempt (unauthenticated off-roster mapping to 'Sarah Duffy' without HITL receipt)
4. Exact production regression (Sprint Demo line 407 'Sarah would know to start an architecture review... like how SAA does')
"""

from __future__ import annotations

import unittest
from typing import Any, Dict, List

from dual_pipeline.ambiguity_core.detector_d6_entity import EntityClarificationDetector
from dual_pipeline.identity_resolver_core.resolver import IdentityResolver
from dual_pipeline.transcript_qa_core.gate1_entity_context import (
    evaluate_referenced_entity_context_gate,
    is_product_homophone_context,
)


SPRINT_DEMO_ROSTER: List[str] = [
    "David Childers",
    "Speaker_Alpha Muschett",
    "Marcus Balk",
    "Brandon Que",
    "Catalina Orostegui",
    "Jeffrey Glade",
    "Andy Kilinskis",
    "Lillian Le",
    "Egle Tamasevice",
    "Amit Ayer",
    "Jennifer Pan",
    "Alex Sanchez",
    "Jake Stark",
    "Morgan Vance Krutilla",
]

LINE_407_HOMOPHONE_TURN = (
    "**Andy Kilinskis:** But based on the requirements and what it takes in, "
    "Sarah would know to start an architecture review by today. And she could start now. "
    "She'll run through the questions, ask the user for clarification, like how SAA does currently."
)


class TestC27EntityResolutionAdversarial(unittest.TestCase):
    """Adversarial verification for C-27 project scoping and ASR product homophone guard."""

    def test_global_directory_off_roster_names_excluded_from_c27_grounding_set(self) -> None:
        """Category: Cross-tenant / off-roster leakage prevention."""
        resolver = IdentityResolver()
        grounding_names = resolver.get_first_names_requiring_grounding(
            attendees=SPRINT_DEMO_ROSTER,
            project="project_beacon",
        )
        # Global directory-only persona 'Sarah Duffy' must NOT be injected into C-27 scan set
        self.assertNotIn("sarah", grounding_names)
        self.assertNotIn("rolf", grounding_names)
        # Confirmed roster members must be present
        self.assertIn("brandon", grounding_names)
        self.assertIn("brien", grounding_names)
        self.assertIn("david", grounding_names)

    def test_exact_production_regression_line_407_homophone_detected(self) -> None:
        """Category: Exact production regression (Sprint Demo line 407 'Sarah' -> 'Sherpa')."""
        product = is_product_homophone_context(LINE_407_HOMOPHONE_TURN, "Sarah")
        self.assertEqual(product, "Sherpa")

        resolver = IdentityResolver()
        normalized, repairs, _ = resolver.normalize_phonetic_dialogue(
            [(407, "Brandon Que", LINE_407_HOMOPHONE_TURN)]
        )
        self.assertIn("Sherpa would know to start an architecture review", normalized[0][2])
        self.assertTrue(any(r.category == "product_homophone" and r.canonical_text == "Sherpa" for r in repairs))

    def test_gate1_10_flags_unrepaired_product_homophone_as_asr_homophone_not_sarah_duffy(self) -> None:
        """Category: Exact production regression & Gate 1.10 diagnostic accuracy."""
        resolver = IdentityResolver()
        passed, failures = evaluate_referenced_entity_context_gate(
            f1_text=f"# Transcript\n\n### [00:28:29]\n\n{LINE_407_HOMOPHONE_TURN}\n",
            f2_text="## Executive Recap\n\nSherpa routes SAA reviews.\n",
            roster_names=SPRINT_DEMO_ROSTER,
            entity_clarifications=[],
            identity_resolver=resolver,
            project="project_beacon",
        )
        self.assertFalse(passed)
        self.assertTrue(any("C27_ASR_PRODUCT_HOMOPHONE_IN_TRANSCRIPT" in f for f in failures))
        self.assertFalse(any("C27_UNGROUNDED_STAKEHOLDER_IN_TRANSCRIPT" in f for f in failures))

    def test_invariant_bypass_unauthenticated_off_roster_entity_resolution_rejected(self) -> None:
        """Category: Invariant bypass attempt (forging entity_resolutions.json for 'Sarah Duffy')."""
        resolver = IdentityResolver()
        forged_clarifications: List[Dict[str, Any]] = [
            {
                "token": "sarah",
                "canonical_name": "Sarah Duffy",
                "org": "PartnerAdvisory",
                "role": "Enterprise Practice Executive",
                "status": "RESOLVED_HITL",
                "rationale": "Explicit operator entity resolution",
            }
        ]
        passed, failures = evaluate_referenced_entity_context_gate(
            f1_text="# Transcript\n\n**Brandon Que:** Sherpa would know to start an architecture review.\n",
            f2_text="## Executive Recap\n\nAll good.\n",
            roster_names=SPRINT_DEMO_ROSTER,
            entity_clarifications=forged_clarifications,
            identity_resolver=resolver,
            project="project_beacon",
        )
        self.assertFalse(passed)
        self.assertTrue(
            any("C27_UNAUTHENTICATED_OFF_ROSTER_ENTITY_MAPPING" in f for f in failures),
            f"Expected unauthenticated off-roster mapping failure, got: {failures}",
        )

    def test_d6_detector_does_not_map_sarah_homophone_to_sarah_duffy(self) -> None:
        """Category: Cross-tenant directory pollution guard in Domain 6 detector."""
        detector = EntityClarificationDetector()
        records = detector.detect(
            turns=[{"speaker": "Brandon Que", "text": LINE_407_HOMOPHONE_TURN}],
            known_attendees=SPRINT_DEMO_ROSTER,
        )
        sarah_records = [r for r in records if "sarah" in r.ambiguity_id]
        self.assertEqual(sarah_records, [])

    def test_boundary_and_malformed_inputs_handled_safely(self) -> None:
        """Category: Boundary payload & Malformed input."""
        resolver = IdentityResolver()
        passed, failures = evaluate_referenced_entity_context_gate(
            f1_text="",
            f2_text="",
            roster_names=[],
            entity_clarifications=[{}, {"token": None, "canonical_name": 123}],  # type: ignore[list-item]
            identity_resolver=resolver,
            project="",
        )
        self.assertTrue(passed)
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
