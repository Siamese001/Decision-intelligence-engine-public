"""Wave 5 Adversarial & Edge-Case Tests: NBA Synthesis, Anti-Conflation & Copilot Boundaries.

Covers mandatory edge-case categories:
1. Boundary payload (empty opportunity/pain-point packet falls back safely without crashing;render_markdown handles None and empty next_best_actions)
2. Malformed input (MultiHopQueryNavigatorAgent rejects empty query or write-mode readonly=False)
3. Invariant bypass attempt (compound slash workstream conflation 'LENS / Sherpa' and uncorroborated workstream splicing still blocked by validate_answer_anti_conflation; candidate lane citations in next_best_actions rejected by validate_answer)
4. Quarantine & C-19 isolation (MultiHopQueryNavigatorAgent filters out INTERNAL_ADVISORY / stealth_score >= 1.0 assertions)
"""
from __future__ import annotations

import unittest

from dual_pipeline.query_core.anti_conflation import validate_answer_anti_conflation
from dual_pipeline.query_core.navigator_agent import MultiHopQueryNavigatorAgent
from dual_pipeline.query_core.reasoning import (
    reason_over_packet,
    render_markdown,
    validate_answer,
)


class Wave5NBASynthesisAdversarialTests(unittest.TestCase):
    def test_invariant_bypass_compound_slash_and_uncorroborated_splicing_blocked(self) -> None:
        """Category 3 (Invariant bypass): slash conflation ('LENS / Sherpa') and uncorroborated workstream mention are blocked."""
        packet = {
            "approved": [
                {
                    "record": {
                        "record_id": "rec-sherpa-only",
                        "title": "Project Beacon status",
                        "retrieval_text": "Project Beacon completed sprint demo.",
                        "evidence_ids": ["ev-1"],
                    }
                }
            ],
            "candidate": [],
            "external_public": [],
        }
        bad_answer = {
            "current_state": [
                {
                    "statement": "Project LENS / Sherpa completed the joint sprint demo.",
                    "citations": ["rec-sherpa-only"],
                },
                {
                    "statement": "Project Beacon and Jarvis share the same ingestion backend.",
                    "citations": ["rec-sherpa-only"],
                },
            ],
            "pain_points": [],
            "opportunities": [],
            "guidance": [],
            "hypotheses": [],
        }
        failures = validate_answer_anti_conflation(bad_answer, packet)
        self.assertTrue(any("compound_workstream_conflation" in f for f in failures))
        self.assertTrue(any("uncorroborated_workstream_splicing(jarvis)" in f for f in failures))

    def test_candidate_record_in_next_best_actions_rejected_by_validate_answer(self) -> None:
        """Category 3 (Invariant bypass): next_best_actions citing a candidate-lane record fails validate_answer."""
        packet = {
            "approved": [],
            "candidate": [
                {
                    "record": {
                        "record_id": "rec-cand-1",
                        "record_type": "assertion",
                        "lane": "candidate",
                        "title": "Candidate proposal",
                        "retrieval_text": "Unverified candidate idea",
                        "evidence_ids": ["ev-c1"],
                    },
                    "retrieval_metadata": {"answer_use": "candidate_hypothesis"},
                }
            ],
            "external_public": [],
        }
        bad_answer = {
            "query_type": "opportunity_nba",
            "current_state": [],
            "patterns": [],
            "pain_points": [],
            "opportunities": [],
            "guidance": [],
            "hypotheses": [],
            "source_evidence": [],
            "next_best_actions": [
                {
                    "action": "Execute unverified candidate proposal immediately.",
                    "basis_citations": ["rec-cand-1"],
                }
            ],
            "rationale": "Test.",
            "counterevidence": [],
            "missing_information": [],
            "assumptions": ["Snapshot is current."],
            "what_would_change_the_conclusion": ["New evidence."],
            "proposed_assertions": [],
            "automatic_graph_mutation_count": 0,
        }
        res = validate_answer(bad_answer, packet)
        self.assertEqual("FAIL", res["status"])
        self.assertIn("next_best_actions[0]", res["approved_lane_failures"])

    def test_navigator_quarantine_and_boundary_guards(self) -> None:
        """Category 1, 2 & 4 (Boundary, Malformed & Quarantine): Navigator rejects write mode/empty query and filters INTERNAL_ADVISORY records."""
        nav = MultiHopQueryNavigatorAgent(max_steps=2, max_tool_calls=4)
        with self.assertRaises(PermissionError):
            nav.navigate(query="test", seed_entities=["NEE"], readonly=False)
        with self.assertRaises(ValueError):
            nav.navigate(query="   ", seed_entities=["NEE"])

        assertions = [
            {
                "assertion_id": "quarantined-1",
                "subject_id": "Apex Global Enterprises",
                "predicate": "DELIVERS",
                "object_id": "Secret Career Target",
                "namespace": "INTERNAL_ADVISORY",
                "is_quarantined": 1,
                "stealth_score": 1.0,
            },
            {
                "assertion_id": "safe-1",
                "subject_id": "Apex Global Enterprises",
                "predicate": "DELIVERS",
                "object_id": "Grid Reliability",
                "namespace": "PUBLIC",
                "is_quarantined": 0,
                "stealth_score": 0.0,
            },
        ]
        res = nav.navigate(query="What does Apex Global Enterprises deliver?", seed_entities=["Apex Global Enterprises"], assertions=assertions)
        self.assertEqual(1, res.quarantined_filtered_count)
        self.assertEqual(["safe-1"], [a["assertion_id"] for a in res.expanded_assertions])

        # Boundary check on render_markdown with None answer
        md_none = render_markdown(None, "X3B_ESCALATE_HITL")
        self.assertIn("did not produce a deliverable answer", md_none)

        # Boundary check on reason_over_packet with empty packet
        empty_ans = reason_over_packet(
            {"query_type": "opportunity_nba", "retrieval_mode": "strategic", "allowed_lanes": ["approved"]},
            {"approved": [], "candidate": [], "external_public": [], "counterevidence": [], "gaps": ["No records"]},
        )
        self.assertEqual([], empty_ans["next_best_actions"])


if __name__ == "__main__":
    unittest.main()
