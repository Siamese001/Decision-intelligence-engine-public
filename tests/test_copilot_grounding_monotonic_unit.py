"""Unit tests for Phase 5: Monotonic candidate rejection, graph pinning, and rendering fidelity."""
import unittest
from typing import Any, Dict
from unittest.mock import MagicMock

from dual_pipeline.cli.copilot_speech import (
    SpeechRenderingResult,
    render_speaker_comments,
    speaker_comments,
    spoken_claim,
)
from dual_pipeline.live_meeting_recorder_core.copilot_attempt_ledger import (
    CATEGORY_RENDERING_FAILURE,
    categorize_copilot_terminal_reason,
    create_attempt_record,
)
from dual_pipeline.live_meeting_recorder_core.copilot_grounding_verifier import (
    CopilotGroundingVerifier,
    check_assertion_validity,
)


class TestCopilotGroundingMonotonicUnit(unittest.TestCase):
    """Verifies monotonic rejection, graph pinning, and rendering invariants."""

    def test_monotonic_rejection_validity_stage_under_budget_breach(self) -> None:
        """Stale, quarantined, superseded, and pointer-mismatched assertions must never be restored upon budget breach."""
        verifier = CopilotGroundingVerifier(budget_ms=0.00001)  # Force immediate budget breach
        candidates = [
            {"assertion_id": "c19_bad", "claim_text": "Private client billing rate is 500", "is_quarantined": 1},
            {"assertion_id": "superseded_bad", "claim_text": "Old plan", "status": "SUPERSEDED"},
            {"assertion_id": "valid_1", "claim_text": "Valid architectural fact", "is_active": True, "evidence_ids": ["e1"]},
        ]
        res = verifier.verify_and_ground(candidates, dialogue="architecture")
        self.assertTrue(res.is_budget_breached)
        self.assertEqual(res.rigor_tier, "FAST")
        verified_ids = [c["assertion_id"] for c in res.verified_assertions]
        self.assertNotIn("c19_bad", verified_ids)
        self.assertNotIn("superseded_bad", verified_ids)
        self.assertIn("valid_1", verified_ids)

    def test_monotonic_rejection_provenance_stage_under_budget_breach(self) -> None:
        """Low-provenance assertions must not be restored when deadline triggers after provenance scoring."""
        verifier = CopilotGroundingVerifier(budget_ms=0.00001, min_provenance_score=0.4)
        candidates = [
            # High provenance
            {"assertion_id": "hi_prov", "is_active": True, "claim_text": "Well-sourced fact", "evidence_ids": ["e1", "e2", "e3"], "source_ids": ["s1", "s2"]},
            # Low provenance (0 evidence, 0 sources)
            {"assertion_id": "lo_prov", "is_active": True, "claim_text": "Unverified rumor", "evidence_ids": [], "source_ids": []},
        ]
        res = verifier.verify_and_ground(candidates, dialogue="fact")
        verified_ids = [c["assertion_id"] for c in res.verified_assertions]
        self.assertNotIn("lo_prov", verified_ids)
        self.assertIn("hi_prov", verified_ids)

    def test_monotonic_rejection_conflict_stage_excludes_contradictions(self) -> None:
        """Contradictory assertions are surfaced in conflicts and excluded from verified output even on deadline."""
        verifier = CopilotGroundingVerifier(budget_ms=0.00001)
        candidates = [
            {
                "assertion_id": "c_yes",
                "is_active": True,
                "subject_node_id": "project_nexus",
                "predicate": "STATUS",
                "object_literal": "approved",
                "claim_text": "Project Nexus is approved",
                "evidence_ids": ["e1"],
            },
            {
                "assertion_id": "c_no",
                "is_active": True,
                "subject_node_id": "project_nexus",
                "predicate": "STATUS",
                "object_literal": "rejected",
                "claim_text": "Project Nexus is rejected",
                "evidence_ids": ["e2"],
            },
            {
                "assertion_id": "c_unrelated",
                "is_active": True,
                "subject_node_id": "anchor_scada",
                "predicate": "STATUS",
                "object_literal": "active",
                "claim_text": "SCADA is active",
                "evidence_ids": ["e3"],
            },
        ]
        res = verifier.verify_and_ground(candidates, dialogue="project")
        verified_ids = [c["assertion_id"] for c in res.verified_assertions]
        self.assertNotIn("c_yes", verified_ids)
        self.assertNotIn("c_no", verified_ids)
        self.assertIn("c_unrelated", verified_ids)
        self.assertEqual(len(res.conflicts), 1)

    def test_long_first_short_second_candidate_rendering(self) -> None:
        """An oversized first candidate must not break the loop and starve subsequent concise candidates."""
        retriever = MagicMock()
        retriever._topology = None

        long_claim = "a " * 30  # 30 words
        short_claim = "concise verified fact"  # 3 words

        assertions = [
            {"assertion_id": "ast_long", "object_literal": long_claim},
            {"assertion_id": "ast_short", "object_literal": short_claim},
        ]

        # Word budget: 15 words. Long claim (30 words) cannot fit; short claim (3 words) fits easily.
        res = render_speaker_comments(
            intent="respond",
            target="Mitch",
            assertions=assertions,
            max_words=15,
            retriever=retriever,
        )
        self.assertEqual(res.rendered_count, 1)
        self.assertEqual(res.budget_exhausted_count, 1)
        self.assertIn("concise verified fact", res.body)
        self.assertNotIn(long_claim, res.body)

    def test_distinguish_malformed_vs_word_budget_exhaustion(self) -> None:
        """Speech synthesis failure reason distinguishes malformed entities from word budget limits."""
        retriever = MagicMock()
        retriever._topology = None

        # Scenario A: Malformed claims (missing literals and nodes)
        malformed_assertions = [
            {"assertion_id": "bad_1", "subject_node_id": None, "object_literal": None},
            {"assertion_id": "bad_2", "object_literal": "```injection```"},
        ]
        res_malformed = render_speaker_comments("respond", "Mitch", malformed_assertions, max_words=50, retriever=retriever)
        self.assertEqual(res_malformed.body, "")
        self.assertEqual(res_malformed.failure_reason, "MALFORMED_REPRESENTATION")

        # Scenario B: Valid claims, but each exceeds tight word budget
        valid_assertions = [
            {"assertion_id": "good_1", "object_literal": "This is a ten word statement that is well formed"},
        ]
        res_budget = render_speaker_comments("respond", "Mitch", valid_assertions, max_words=3, retriever=retriever)
        self.assertEqual(res_budget.body, "")
        self.assertEqual(res_budget.failure_reason, "WORD_BUDGET_EXHAUSTED")

        # Diagnostic codes map to rendering failure category
        cat1, _ = categorize_copilot_terminal_reason("MALFORMED_REPRESENTATION")
        cat2, _ = categorize_copilot_terminal_reason("WORD_BUDGET_EXHAUSTED")
        self.assertEqual(cat1, CATEGORY_RENDERING_FAILURE)
        self.assertEqual(cat2, CATEGORY_RENDERING_FAILURE)

    def test_pinned_generation_and_graph_sha_validation(self) -> None:
        """Candidates tagged with mismatched generation or SHA are rejected as GRAPH_POINTER_MISMATCH."""
        assertion_valid = {
            "assertion_id": "a1",
            "is_active": True,
            "claim_text": "Good",
            "_pinned_generation": "gen-100",
            "_pinned_graph_sha": "sha-100",
        }
        ok, err = check_assertion_validity(
            assertion_valid,
            pinned_generation="gen-100",
            packet_graph_sha="sha-100",
        )
        self.assertTrue(ok)
        self.assertIsNone(err)

        assertion_wrong_gen = {
            "assertion_id": "a2",
            "is_active": True,
            "claim_text": "Good",
            "_pinned_generation": "gen-999",
            "_pinned_graph_sha": "sha-100",
        }
        ok2, err2 = check_assertion_validity(
            assertion_wrong_gen,
            pinned_generation="gen-100",
            packet_graph_sha="sha-100",
        )
        self.assertFalse(ok2)
        self.assertEqual(err2, "GRAPH_POINTER_MISMATCH")

    def test_attempt_record_captures_pinned_generation(self) -> None:
        """Attempt ledger records capture pinned generation and graph SHA256."""
        rec = create_attempt_record(
            terminal_outcome="SUCCESS",
            terminal_reason="SUCCESS",
            generation="generation-20261007",
            graph_sha256="abc12345def67890",
        )
        self.assertEqual(rec.generation, "generation-20261007")
        self.assertEqual(rec.graph_sha256, "abc12345def67890")
        d = rec.to_dict()
        self.assertEqual(d["generation"], "generation-20261007")
        self.assertEqual(d["graph_sha256"], "abc12345def67890")


if __name__ == "__main__":
    unittest.main()
