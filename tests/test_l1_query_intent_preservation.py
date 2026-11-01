"""Unit and contract tests for L1 query intent and domain keyword preservation.

Verifies:
1. Domain entities (EDO, Enterprise Data Office, Databricks, AWS, Lakehouse, GridOpti, Project Spring)
   are extracted deterministically from user questions.
2. Query plan subquestions preserve and lead with the primary user question intent.
3. Coverage manifest compiles named_subject requirements for domain entities, preventing 0-coverage replan loops.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import digest
from dual_pipeline.l2 import load_l2_config
from dual_pipeline.query import build_query_plan, compile_query_coverage_manifest
from dual_pipeline.query_core.constants import _entities
from dual_pipeline.query_router import QueryDestination, route_query


class TestL1QueryIntentPreservation(unittest.TestCase):
    """Test suite ensuring domain keywords survive L1 planning and coverage compilation."""

    def test_domain_entity_extraction_extracts_lakehouse_and_utility_entities(self) -> None:
        q = "What is the Enterprise Data Office (EDO) journey with Databricks and AWS Lakehouse at Apex Global Enterprises?"
        entities = _entities(q)
        self.assertIn("Enterprise Data Office", entities)
        self.assertIn("Databricks", entities)
        self.assertIn("AWS", entities)
        self.assertIn("Lakehouse", entities)
        self.assertIn("Apex Global Enterprises", entities)

    def test_query_plan_anchors_primary_question_in_subquestions(self) -> None:
        question = "What is the latest EDO data modernization architecture?"
        route = route_query(question, query_type_override="current_state_corroboration")
        route_artifact = route.to_artifact(
            request_id="test-req-001",
            run_id="test-run-001",
            trace_id=digest(["test-trace", "001"])[:32],
            decided_at="2026-09-16T12:00:00Z",
        )
        graph_utility = route_artifact["destination"] == QueryDestination.GRAPH_EVIDENCE_UTILITY.value
        plan = build_query_plan(
            question=question,
            query_type="current_state_corroboration",
            request_id="test-req-001",
            run_id="test-run-001",
            current_trace_id=digest(["test-trace", "001"])[:32],
            l2_config=load_l2_config(),
            provider_egress_allowed=False,
            destination_decision=route_artifact,
            assurance_profile=None if graph_utility else "standard",
        )

        subquestions = plan["subquestions"]
        self.assertGreaterEqual(len(subquestions), 2)
        # Leading subquestion must be the user's domain question
        self.assertEqual(subquestions[0], question)
        self.assertIn("Enterprise Data Office", plan["primary_entities"])

    def test_coverage_manifest_preserves_domain_named_subjects(self) -> None:
        question = "What are the core Databricks data products for GridOpti and Project Spring in EDO?"
        route = route_query(question, query_type_override="cross_workstream_synthesis")
        route_artifact = route.to_artifact(
            request_id="test-req-002",
            run_id="test-run-002",
            trace_id=digest(["test-trace", "002"])[:32],
            decided_at="2026-09-16T12:00:00Z",
        )
        graph_utility = route_artifact["destination"] == QueryDestination.GRAPH_EVIDENCE_UTILITY.value
        plan = build_query_plan(
            question=question,
            query_type="cross_workstream_synthesis",
            request_id="test-req-002",
            run_id="test-run-002",
            current_trace_id=digest(["test-trace", "002"])[:32],
            l2_config=load_l2_config(),
            provider_egress_allowed=False,
            destination_decision=route_artifact,
            assurance_profile=None if graph_utility else "standard",
        )

        manifest = compile_query_coverage_manifest(
            question=question,
            validated_request_digest="0" * 64,
            plan=plan,
            active={"graph_snapshot": "snapshot-test", "graph_sha256": "1" * 64},
            authorization_scope_digest="2" * 64,
        )

        requirement_labels = [r["requirement_label"] for r in manifest["requirements"]]
        self.assertIn("Enterprise Data Office", requirement_labels)
        self.assertIn("Databricks", requirement_labels)
        self.assertIn("GridOpti", requirement_labels)
        self.assertIn("Project Spring", requirement_labels)


if __name__ == "__main__":
    unittest.main()
