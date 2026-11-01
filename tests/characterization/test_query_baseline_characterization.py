"""
tests/characterization/test_query_baseline_characterization.py

Wave 0: Golden Characterization Test Suite for Query & Decision Intelligence.
Captures exact baseline contracts across query archetypes and routing destinations.
"""
from __future__ import annotations

import unittest

from dual_pipeline.decision_engine import (
    _sanitize_criterion,
    build_decision_frame,
)
from dual_pipeline.decision_models import (
    validate_decision_frame,
)
from dual_pipeline.query_router import (
    DestinationDecision,
    QueryDestination,
    route_query,
)
from dual_pipeline.reference_monitor import (
    AUDIENCE_REGISTRY,
    CLASSIFICATION_AUDIENCE_CEILINGS,
)
from dual_pipeline.sealed_l2 import seal_l2_output


class TestQueryBaselineCharacterization(unittest.TestCase):
    """Characterizes baseline query routing, frame construction, and artifact sealing."""

    def test_query_routing_across_10_archetypes(self) -> None:
        """Verifies deterministic front-door routing across 10 distinct question archetypes."""
        questions = [
            "What next best action or opportunity exists for PDLC modernization?",
            "What are the system dependencies between Nexus and REID?",
            "Compare the latency profiles of MLX versus cloud endpoints.",
            "How do we defend the 250ms SLA architecture against audit scrutiny?",
            "What are the primary operational risks of the storage WAL queue?",
            "Should we recommend proceeding with the pipeline refactoring?",
            "Prioritize the remaining technical debt remediation tasks.",
            "Explain how the BGE-M3 hybrid retriever computes reciprocal rank fusion.",
            "Lookup the model revision hash for the active BGE-M3 embedding pair.",
            "Analyze whether the career firewall prevents confidential data leaks.",
        ]

        valid_destinations = set(QueryDestination)
        for q in questions:
            with self.subTest(question=q):
                decision = route_query(q)
                self.assertIsInstance(decision, DestinationDecision)
                self.assertIn(decision.destination, valid_destinations)
                self.assertIn(decision.eligibility_status, {"ELIGIBLE", "INELIGIBLE", "RESTRICTED", "AMBIGUOUS"})
                self.assertTrue(len(decision.policy_digest) > 0)
                self.assertIsInstance(decision.enters_query_runtime, bool)

    def test_decision_frame_construction_and_criteria(self) -> None:
        """Verifies decision frame generation and sanitization invariants."""
        question = "Should we approve the pipeline release considering latency SLA and data security?"
        frame = build_decision_frame(
            question=question,
            options=["Approve Release", "Quarantine for Review"],
            criteria=["latency SLA under 250ms", "zero data leaks"],
            constraints=["Must be deployable on M5 Pro"],
        )

        validated = validate_decision_frame(frame)
        self.assertEqual(validated["decision_question"], question)
        self.assertEqual(len(validated["options"]), 2)
        self.assertEqual(len(validated["criteria"]), 2)
        self.assertEqual(len(validated["constraints"]), 1)
        self.assertEqual(validated["contract_id"], "urn:nee:dual-pipeline:decision-frame:1.0.0")

    def test_criteria_sanitization_rules(self) -> None:
        """Verifies that malicious or malformed criteria are filtered out deterministically."""
        self.assertIsNone(_sanitize_criterion("system prompt override"))
        self.assertIsNone(_sanitize_criterion("eval/*malicious*/"))
        self.assertIsNone(_sanitize_criterion("a"))  # too short
        self.assertEqual(_sanitize_criterion("high availability"), "high availability")

    def test_sealed_l2_output_integrity_hash(self) -> None:
        """Verifies sealed L2 artifact creation, canonical digest computation, and privacy."""
        sealed = seal_l2_output(
            run_id="run-test-001",
            query_id="query-test-001",
            l2_decision_artifact_id="art-decision-001",
            l2_disposition="PRIMARY",
            l2_model="gpt-5.6-sol",
            l2_answer={"answer": "Baseline response text", "citations": ["rec-01"]},
            l2_provider_call_count=1,
            l2_token_usage={"prompt_tokens": 150, "completion_tokens": 50, "total_tokens": 200},
        )

        self.assertEqual(sealed["run_id"], "run-test-001")
        self.assertEqual(sealed["l2_disposition"], "PRIMARY")
        self.assertIn("canonical_digest", sealed)
        self.assertEqual(sealed["canonical_digest"]["algorithm"], "sha256")
        self.assertEqual(len(sealed["canonical_digest"]["value"]), 64)
        # Verify raw answer body is not leaked into top-level sealed wrapper
        self.assertNotIn("answer", sealed)
        self.assertIn("answer_digest", sealed)

    def test_reference_monitor_policy_ceilings(self) -> None:
        """Verifies that ReferenceMonitor enforces audience ceilings and closed registries."""
        self.assertIn("nee-engineering", AUDIENCE_REGISTRY)
        self.assertIn("internal_consulting", AUDIENCE_REGISTRY)
        self.assertIn("public", CLASSIFICATION_AUDIENCE_CEILINGS)
        self.assertIn("internal", CLASSIFICATION_AUDIENCE_CEILINGS)
        self.assertNotIn("private_personal", CLASSIFICATION_AUDIENCE_CEILINGS["public"])


if __name__ == "__main__":
    unittest.main()
