from __future__ import annotations

import unittest

from dual_pipeline.contracts import create_artifact, create_versioned_artifact


class DecisionRoutingContractTests(unittest.TestCase):
    def test_destination_decision_separates_delivery_from_acl(self) -> None:
        artifact = create_artifact(
            "query-destination-decision",
            "destination-contract-test",
            decision_id="destination-contract-test",
            request_id="request-destination-contract",
            run_id="run-destination-contract",
            trace_id="0123456789abcdef0123456789abcdef",
            destination="NEE_DECISION_INTELLIGENCE",
            eligibility_status="ELIGIBLE_PRE_RETRIEVAL",
            eligibility_checks={
                "material_outcome": "PASS",
                "graph_essential": "PASS",
                "multi_record_reasoning": "PASS",
                "l2_reasoning_required": "PASS",
                "authorized": "PASS",
            },
            query_type="relationship_explanation",
            materiality="medium",
            delivery_profile="internal_technical",
            artifact_type="answer",
            reason_codes=["MATERIAL_GRAPH_DECISION"],
            handoff_permitted=False,
            operator_override=False,
            automatic_egress_count=0,
            policy_digest="1" * 64,
            decided_at="2026-08-01T12:00:00Z",
        )
        self.assertNotIn("audiences", artifact)
        self.assertEqual("internal_technical", artifact["delivery_profile"])

    def test_v2_nee_plan_requires_assurance(self) -> None:
        common = {
            "plan_id": "plan-v2-contract-test",
            "request_id": "request-v2-contract-test",
            "run_id": "run-v2-contract-test",
            "trace_id": "0123456789abcdef0123456789abcdef",
            "destination_decision_digest": "2" * 64,
            "destination": "NEE_DECISION_INTELLIGENCE",
            "query_type": "relationship_explanation",
            "materiality": "medium",
            "delivery_profile": "internal_technical",
            "artifact_type": "answer",
            "l2_policy": "required",
            "l2_policy_reason": "Graph-dependent explanation requires L2.",
            "allowed_provider_class": "openai",
            "maximum_primary_calls": 1,
            "maximum_repair_calls": 0,
            "maximum_challenger_calls": 0,
            "maximum_judge_calls": 1,
            "required_output_schema": "nee-governed-answer-2.0.0",
            "provider_egress_allowed": True,
            "primary_entities": ["Project Beacon"],
            "time_horizon": None,
            "subquestions": ["How are the records related?"],
            "relationships_to_inspect": ["governed relationships"],
            "evidence_requirements": ["approved relationship evidence"],
            "retrieval_mode": "factual",
            "allowed_lanes": ["approved"],
            "retrieval": {
                "exact_anchors": True,
                "semantic": True,
                "graph_hops": 3,
                "lane_blending": False,
                "bm25": True,
                "typed_path_expansion": True,
                "connected_evidence_selection": True,
            },
            "external_research": "denied",
            "prompt_blueprint": "nee_decision_intelligence_v2",
            "output_schema": "nee-governed-answer-2.0.0",
            "exit_criteria": ["coverage complete", "X1 judge passes"],
        }
        artifact = create_versioned_artifact(
            "query-plan",
            "2.0.0",
            "plan-v2-contract-test",
            assurance_profile="standard",
            **common,
        )
        self.assertEqual("standard", artifact["assurance_profile"])
        with self.assertRaises(ValueError):
            create_versioned_artifact(
                "query-plan",
                "2.0.0",
                "plan-v2-contract-invalid",
                assurance_profile=None,
                **common,
            )


if __name__ == "__main__":
    unittest.main()
