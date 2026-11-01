#!/usr/bin/env python3
"""Regression tests for the sealed, network-free L2 evaluator."""

from __future__ import annotations

import unittest

from dual_pipeline.evals.fakes import _bind_required_material_records
from dual_pipeline.l2 import evaluate_semantic_quality
from dual_pipeline.query import reason_over_packet


class SealedEvalProviderTests(unittest.TestCase):
    def test_required_record_after_mapper_prefix_is_bound_and_passes_quality(self) -> None:
        approved = [
            {
                "record": {
                    "record_id": f"record-{index}",
                    "record_type": "assertion",
                    "title": f"Governed fact {index}",
                    "retrieval_text": f"Governed fact {index} is recorded.",
                    "evidence_ids": [f"evidence-{index}"],
                    "lane": "approved",
                },
                "retrieval_metadata": {"answer_use": "factual_answer"},
            }
            for index in range(6)
        ]
        packet = {
            "approved": approved,
            "candidate": [],
            "external_public": [],
            "counterevidence": [],
            "gaps": [],
            "required_material_record_ids_by_lane": {
                "approved": ["record-5"],
                "candidate": [],
                "external_public": [],
            },
        }
        plan = {"query_type": "cross_workstream_synthesis"}
        output = reason_over_packet(plan, packet)
        output["patterns"] = ["Governed pattern"]

        _bind_required_material_records(
            output=output,
            plan=plan,
            evidence_packet=packet,
        )

        deficiencies = evaluate_semantic_quality(
            question="Synthesize governed patterns.",
            plan=plan,
            evidence_packet=packet,
            answer=output,
        )
        self.assertEqual((), deficiencies)
        self.assertIn(
            "record-5",
            {citation for row in output["current_state"] for citation in row["citations"]},
        )

    def test_recommendation_wording_on_synthesis_route_gets_grounded_action(self) -> None:
        packet = {
            "approved": [
                {
                    "record": {
                        "record_id": "record-fact",
                        "record_type": "assertion",
                        "title": "Governed fact",
                        "retrieval_text": "Governed fact is recorded.",
                        "evidence_ids": ["evidence-fact"],
                        "lane": "approved",
                    },
                    "retrieval_metadata": {"answer_use": "factual_answer"},
                }
            ],
            "candidate": [],
            "external_public": [],
            "counterevidence": [],
            "gaps": [],
            "required_material_record_ids_by_lane": {
                "approved": ["record-fact"],
                "candidate": [],
                "external_public": [],
            },
        }
        plan = {"query_type": "cross_workstream_synthesis", "materiality": "medium"}
        output = reason_over_packet(plan, packet)
        output["patterns"] = ["Governed pattern"]
        _bind_required_material_records(
            output=output,
            plan=plan,
            evidence_packet=packet,
        )
        output["next_best_actions"] = [
            {
                "action": "Validate the governed fact with the accountable owner.",
                "basis_citations": ["record-fact"],
            }
        ]
        deficiencies = evaluate_semantic_quality(
            question="Separate approved facts and recommended next actions.",
            plan=plan,
            evidence_packet=packet,
            answer=output,
        )
        self.assertEqual((), deficiencies)


if __name__ == "__main__":
    unittest.main(verbosity=2)
