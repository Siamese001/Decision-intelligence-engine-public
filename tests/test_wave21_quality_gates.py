#!/usr/bin/env python3
"""Wave 21 unit tests: Tightened Deterministic Semantic Quality Gates."""
from __future__ import annotations

import unittest

from dual_pipeline.l2 import (
    QualityFailureCode,
    evaluate_semantic_quality,
)


class Wave21QualityGatesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = {
            "query_type": "current_state_corroboration",
            "materiality": "low",
        }
        self.evidence_packet = {
            "approved": [{"record": {"record_id": "app_1"}}],
            "candidate": [],
            "counterevidence": [],
            "graph_paths": [],
        }

    def test_citation_density_insufficient(self) -> None:
        # Current state with statement but missing citations
        answer = {
            "current_state": [
                {
                    "statement": "This is an ungrounded claim without citations.",
                    "citations": [],
                    "evidence_ids": [],
                    "lane": "approved",
                }
            ],
            "rationale": "Grounded rationale",
        }
        deficiencies = evaluate_semantic_quality(
            question="What is the status?",
            plan=self.plan,
            evidence_packet=self.evidence_packet,
            answer=answer,
        )
        codes = [d.code for d in deficiencies]
        self.assertIn(QualityFailureCode.CITATION_DENSITY_INSUFFICIENT, codes)

        # Current state with valid citations
        valid_answer = {
            "current_state": [
                {
                    "statement": "This is a grounded claim with citations.",
                    "citations": ["app_1"],
                    "evidence_ids": ["ev_1"],
                    "lane": "approved",
                }
            ],
            "rationale": "Grounded rationale",
        }
        valid_deficiencies = evaluate_semantic_quality(
            question="What is the status?",
            plan=self.plan,
            evidence_packet=self.evidence_packet,
            answer=valid_answer,
        )
        valid_codes = [d.code for d in valid_deficiencies]
        self.assertNotIn(QualityFailureCode.CITATION_DENSITY_INSUFFICIENT, valid_codes)

    def test_unresolved_contradiction_posture(self) -> None:
        evidence_with_conflict = {
            "approved": [{"record": {"record_id": "app_1"}}],
            "candidate": [],
            "counterevidence": [
                {
                    "record_id": "conflict_rec_1",
                    "statement": "Contradictory claim",
                    "citations": ["conflict_rec_1"],
                    "evidence_ids": ["ev_c1"],
                    "lane": "approved",
                }
            ],
            "graph_paths": [],
        }

        # What changes does not address contradiction
        answer_unaddressed = {
            "current_state": [
                {
                    "statement": "Current state fact",
                    "citations": ["app_1"],
                    "evidence_ids": ["ev_1"],
                    "lane": "approved",
                }
            ],
            "counterevidence": [
                {
                    "statement": "Contradictory claim",
                    "citations": ["conflict_rec_1"],
                    "evidence_ids": ["ev_c1"],
                    "lane": "approved",
                }
            ],
            "what_would_change_the_conclusion": [
                "Only general business growth",
            ],
            "rationale": "Rationale",
        }
        deficiencies = evaluate_semantic_quality(
            question="Analyze contradictions",
            plan=self.plan,
            evidence_packet=evidence_with_conflict,
            answer=answer_unaddressed,
        )
        codes = [d.code for d in deficiencies]
        self.assertIn(QualityFailureCode.UNRESOLVED_CONTRADICTION_POSTURE, codes)

        # What changes addresses contradiction
        answer_addressed = dict(answer_unaddressed)
        answer_addressed["what_would_change_the_conclusion"] = [
            "Resolution of conflict regarding conflict_rec_1",
        ]
        addressed_deficiencies = evaluate_semantic_quality(
            question="Analyze contradictions",
            plan=self.plan,
            evidence_packet=evidence_with_conflict,
            answer=answer_addressed,
        )
        addressed_codes = [d.code for d in addressed_deficiencies]
        self.assertNotIn(QualityFailureCode.UNRESOLVED_CONTRADICTION_POSTURE, addressed_codes)

    def test_action_feasibility_incomplete(self) -> None:
        plan_action = {
            "query_type": "opportunity_nba",
            "materiality": "medium",
        }
        # Next action missing basis_citations
        answer_infeasible = {
            "current_state": [],
            "opportunities": [
                {
                    "opportunity": "Opp 1",
                    "why_prioritize": "High ROI",
                    "addresses": ["Pain 1"],
                    "citations": ["app_1"],
                    "evidence_ids": ["ev_1"],
                }
            ],
            "next_best_actions": [
                {
                    "action": "Execute marketing campaign",
                    "basis_citations": [],  # Empty citations
                }
            ],
            "rationale": "Detailed rationale",
            "assumptions": ["Assumption 1"],
            "what_would_change_the_conclusion": ["Condition 1"],
        }
        deficiencies = evaluate_semantic_quality(
            question="Recommend next best actions",
            plan=plan_action,
            evidence_packet=self.evidence_packet,
            answer=answer_infeasible,
        )
        codes = [d.code for d in deficiencies]
        self.assertIn(QualityFailureCode.ACTION_FEASIBILITY_INCOMPLETE, codes)

        # Next action with valid basis_citations
        answer_feasible = dict(answer_infeasible)
        answer_feasible["next_best_actions"] = [
            {
                "action": "Execute marketing campaign",
                "basis_citations": ["app_1"],
            }
        ]
        feasible_deficiencies = evaluate_semantic_quality(
            question="Recommend next best actions",
            plan=plan_action,
            evidence_packet=self.evidence_packet,
            answer=answer_feasible,
        )
        feasible_codes = [d.code for d in feasible_deficiencies]
        self.assertNotIn(QualityFailureCode.ACTION_FEASIBILITY_INCOMPLETE, feasible_codes)


if __name__ == "__main__":
    unittest.main()
