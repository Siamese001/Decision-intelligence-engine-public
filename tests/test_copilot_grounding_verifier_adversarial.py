"""
tests/test_copilot_grounding_verifier_adversarial.py

Adversarial stress and edge-case testing for CopilotGroundingVerifier (Wave 4):
1. Graph activated mid-meeting (hash mismatch between session packet and graph topology).
2. Extreme budget exhaustion (0.0001 ms budget -> safe degradation to rigor: FAST without crash).
3. Provider output with an uncited sentence (sentence-level citation enforcement drops ungrounded statements).
4. Rule C-19 Career Firewall assertion injection.
5. Empty candidate set and malformed payloads.
6. Extreme candidate fanout (500+ candidates) within 50ms budget.
"""
import time
import unittest
from typing import Any, Dict
from unittest.mock import MagicMock

from dual_pipeline.live_meeting_recorder_core.copilot_grounding_verifier import (
    CopilotGroundingVerifier,
)
from dual_pipeline.live_meeting_recorder_core.copilot_validator import (
    enforce_sentence_citations,
)


class TestCopilotGroundingVerifierAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.verifier = CopilotGroundingVerifier(budget_ms=250.0, min_provenance_score=0.3)

    def test_graph_activated_mid_meeting_hash_mismatch(self) -> None:
        """When a graph release activates mid-meeting, verifier flags hash_mismatch and protects against stale state."""
        mock_topology = MagicMock()
        mock_topology.graph_sha256 = "new_active_hash_bbbb2222"
        mock_topology.assertions = {
            "asrt_1": {"assertion_id": "asrt_1", "is_active": True, "claim_text": "Phase 2 scope"}
        }

        candidates = [
            {
                "assertion_id": "asrt_1",
                "is_active": True,
                "claim_text": "Phase 2 scope",
                "evidence_ids": ["ev_1", "ev_2"],
                "source_ids": ["meeting:01"],
            },
        ]

        # Session packet had prior hash
        result = self.verifier.verify_and_ground(
            candidates=candidates,
            dialogue="Phase 2 scope",
            topology=mock_topology,
            packet_graph_sha="old_packet_hash_aaaa1111",
        )

        self.assertTrue(result.hash_mismatch)
        # Even with hash mismatch, valid assertions in active graph still verify
        self.assertEqual(len(result.verified_assertions), 1)

    def test_budget_exhaustion_safe_degradation(self) -> None:
        """Under severe budget constraint (e.g. 0.0001ms), verifier immediately degrades to FAST without crashing."""
        exhausted_verifier = CopilotGroundingVerifier(budget_ms=0.00001)
        candidates = [
            {
                "assertion_id": f"asrt_{i}",
                "is_active": True,
                "claim_text": f"Candidate fact number {i}",
                "evidence_ids": ["ev_1"],
            }
            for i in range(20)
        ]

        result = exhausted_verifier.verify_and_ground(
            candidates=candidates,
            dialogue="Candidate fact number 5",
        )

        self.assertEqual(result.rigor_tier, "FAST")
        self.assertTrue(result.is_budget_breached)
        # Returns candidate fallback safely
        self.assertEqual(len(result.verified_assertions), 20)

    def test_provider_output_uncited_sentence_dropped(self) -> None:
        """In VERIFIED mode, sentences that fail to map to verified assertions are dropped."""
        verified = [
            {
                "assertion_id": "asrt_nexus_01",
                "predicate": "OWNS",
                "subject_node_id": "person:amit_ayer",
                "object_literal": "Power BI Gateway",
                "claim_text": "Amit Ayer owns the Power BI Gateway architecture.",
            }
        ]

        parsed: Dict[str, Any] = {
            "has_guidance": True,
            "suggested_script": "Amit Ayer owns the Power BI Gateway architecture. We should also rewrite the entire billing backend in Rust next week.",
            "supporting_details_bullets": [
                "Amit Ayer owns the Power BI Gateway architecture asrt_nexus_01.",
                "Unrelated ungrounded statement about arbitrary marketing budgets.",
            ],
            "snap_cue": "Verify ownership",
        }

        enforced = enforce_sentence_citations(parsed, verified, rigor_tier="VERIFIED")

        # Ungrounded bullet dropped
        self.assertEqual(len(enforced["supporting_details_bullets"]), 1)
        self.assertIn("Power BI Gateway", enforced["supporting_details_bullets"][0])

        # Ungrounded sentence in script dropped
        self.assertIn("Amit Ayer owns the Power BI Gateway", enforced["suggested_script"])
        self.assertNotIn("Rust next week", enforced["suggested_script"])
        self.assertEqual(enforced["cited_assertion_ids"], ["asrt_nexus_01"])

    def test_c19_career_firewall_assertion_injection(self) -> None:
        """Adversarial assertion carrying C-19 internal advisory markers is quarantined."""
        toxic_candidates = [
            {
                "assertion_id": "asrt_toxic_01",
                "is_active": True,
                "namespace": "INTERNAL_ADVISORY",
                "claim_text": "Leverage this meeting for networking_wedge and career promotion.",
            },
            {
                "assertion_id": "asrt_toxic_02",
                "is_active": True,
                "is_quarantined": 1,
                "claim_text": "Secret executive compensation trajectory data.",
            },
            {
                "assertion_id": "asrt_clean_03",
                "is_active": True,
                "claim_text": "Substation telemetry requires redundant CT/PT circuits.",
                "evidence_ids": ["ev_1", "ev_2"],
            },
        ]

        result = self.verifier.verify_and_ground(
            candidates=toxic_candidates,
            dialogue="Substation telemetry requirements",
        )

        verified_ids = [a["assertion_id"] for a in result.verified_assertions]
        self.assertNotIn("asrt_toxic_01", verified_ids)
        self.assertNotIn("asrt_toxic_02", verified_ids)
        self.assertIn("asrt_clean_03", verified_ids)

    def test_empty_and_malformed_candidates(self) -> None:
        """Empty list or malformed candidate payloads handled without exception."""
        # Empty candidates
        res_empty = self.verifier.verify_and_ground([], "dialogue")
        self.assertEqual(res_empty.rigor_tier, "VERIFIED")
        self.assertEqual(len(res_empty.verified_assertions), 0)

        # Malformed entries
        malformed = [None, {}, {"is_active": True}, "not_a_dict"]  # type: ignore
        res_malformed = self.verifier.verify_and_ground(malformed, "dialogue")  # type: ignore
        self.assertEqual(len(res_malformed.verified_assertions), 0)

    def test_large_candidate_fanout_stress(self) -> None:
        """500 candidate assertions verified and reranked comfortably within budget."""
        candidates = [
            {
                "assertion_id": f"asrt_stress_{i}",
                "is_active": True,
                "predicate": "DEPENDS_ON" if i % 3 == 0 else "CONTAINS",
                "claim_text": f"Telemetry subsystem {i} connects to control node {i % 10}.",
                "evidence_ids": ["ev_1"],
                "source_ids": ["meeting:01"],
            }
            for i in range(500)
        ]

        t0 = time.perf_counter()
        res = self.verifier.verify_and_ground(
            candidates=candidates,
            dialogue="Telemetry subsystem connects to control node",
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        self.assertEqual(res.rigor_tier, "VERIFIED")
        self.assertFalse(res.is_budget_breached)
        self.assertLess(elapsed_ms, 50.0)  # Must be well under 50ms


if __name__ == "__main__":
    unittest.main()
