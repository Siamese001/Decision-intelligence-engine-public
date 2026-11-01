"""
tests/characterization/test_query_baseline.py

Characterization baseline tests for the query and decision pipelines:
- Query routing contracts and destination decisions
- Typed decision frame construction and evaluation
- Response terminal state contracts and confidence bands
"""
from __future__ import annotations

import unittest

from dual_pipeline.decision_engine import (
    build_decision_evidence_pack,
    build_decision_frame,
    evaluate_decision,
)
from dual_pipeline.decision_models import (
    AssessmentState,
    ConfidenceBand,
    DecisionTerminalState,
    validate_decision_frame,
)
from dual_pipeline.query_router import (
    DestinationDecision,
    QueryDestination,
    route_query,
)


class TestQueryAndDecisionBaseline(unittest.TestCase):
    """Characterizes baseline behavior of query routing and decision intelligence."""

    def test_query_routing_baseline(self) -> None:
        """Verifies deterministic front-door routing of questions."""
        question = "What is the status of the LENS pipeline architecture?"
        decision = route_query(question)

        self.assertIsInstance(decision, DestinationDecision)
        self.assertIsInstance(decision.destination, QueryDestination)
        self.assertIn(decision.eligibility_status, {"ELIGIBLE", "INELIGIBLE", "RESTRICTED", "AMBIGUOUS"})
        self.assertTrue(len(decision.policy_digest) > 0)
        self.assertIsInstance(decision.enters_query_runtime, bool)

    def test_decision_models_contract_baseline(self) -> None:
        """Verifies frozen enum values for decision terminal states and confidence bands."""
        expected_states = {
            "DECISION_PRODUCED",
            "CLARIFICATION_REQUIRED",
            "NO_DECISION",
            "EVIDENCE_READY_DECISION_NOT_PRODUCED",
        }
        actual_states = {state.value for state in DecisionTerminalState}
        self.assertEqual(expected_states, actual_states)

        expected_bands = {"HIGH", "MEDIUM", "LOW", "INSUFFICIENT"}
        actual_bands = {band.value for band in ConfidenceBand}
        self.assertEqual(expected_bands, actual_bands)

        expected_assessments = {"SUPPORTS", "OPPOSES", "MIXED", "UNKNOWN"}
        actual_assessments = {a.value for a in AssessmentState}
        self.assertEqual(expected_assessments, actual_assessments)

    def test_build_and_evaluate_decision_baseline(self) -> None:
        """Verifies deterministic frame building and evaluation against evidence packs."""
        question = "Should we adopt Option A (cloud-native) or Option B (hybrid Metal)?"
        options = ["Option A: Cloud-native S3", "Option B: Hybrid Metal on-prem"]
        criteria = ["Latency SLA under 250ms", "Zero confidential leakage"]

        frame = build_decision_frame(
            question=question,
            options=options,
            criteria=criteria,
            time_horizon="3 months",
        )

        self.assertEqual(frame["contract_id"], "urn:nee:dual-pipeline:decision-frame:1.0.0")
        self.assertEqual(frame["decision_question"], question)
        self.assertEqual(len(frame["options"]), 2)
        self.assertEqual(len(frame["criteria"]), 2)

        # Validate against strict contract
        validated = validate_decision_frame(frame)
        self.assertEqual(validated["artifact_id"], frame["artifact_id"])

        # Build mock evidence pack matching query_result and evidence_packet schema
        query_result = {
            "generation_id": "gen-test-01",
            "graph_snapshot": "snap-01",
            "graph_sha256_after": "hash-01",
            "embedding_backend": "BAAI/bge-m3",
            "dense_executed": True,
            "sparse_executed": True,
        }
        evidence_packet = {
            "approved": [
                {
                    "record": {
                        "record_id": "rec-01",
                        "title": "Latency Evaluation",
                        "retrieval_text": "Hybrid Metal achieves 180ms latency without cloud egress.",
                        "evidence_ids": ["ev-01"],
                        "record_type": "technical",
                        "currentness_state": "current",
                    },
                    "retrieval_metadata": {"answer_use": "factual_answer"},
                }
            ],
            "candidate": [],
            "external_public": [],
            "graph_paths": [],
            "counterevidence": [],
            "gaps": [],
        }

        evidence_pack = build_decision_evidence_pack(
            query_result=query_result,
            evidence_packet=evidence_packet,
        )

        self.assertEqual(
            evidence_pack["contract_id"],
            "urn:nee:dual-pipeline:decision-evidence-pack:1.0.0",
        )

        result = evaluate_decision(
            frame=frame,
            evidence_pack=evidence_pack,
            answer=None,
            execution={"query_run_id": "run-baseline-01"},
        )
        self.assertEqual(
            result["contract_id"],
            "urn:nee:dual-pipeline:decision-result:1.0.0",
        )
        self.assertIn("terminal_state", result)
        self.assertIn(result["terminal_state"], {s.value for s in DecisionTerminalState})


if __name__ == "__main__":
    unittest.main()
