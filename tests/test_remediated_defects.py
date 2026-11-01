from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.query_router import QueryDestination, route_query


class RemediatedDefectsTests(unittest.TestCase):
    def test_routing_synthesize_keyword_routes_to_nee(self) -> None:
        """Synthesize/Synthesis queries should route to NEE_DECISION_INTELLIGENCE."""
        queries = [
            "Synthesize cross-workstream architecture patterns across A2A and Project Beacon",
            "Synthesize cross-workstream risk and gap analysis across A2A and Project Beacon",
            "Synthesizing architecture options across A2A and Project Beacon",
        ]
        for query in queries:
            with self.subTest(query=query):
                decision = route_query(query)
                self.assertEqual(
                    QueryDestination.NEE_DECISION_INTELLIGENCE,
                    decision.destination,
                    f"Query failed to route to NEE_DECISION_INTELLIGENCE: {query}",
                )

    def test_graph_retrieval_unauthorized_seed_suppressed(self) -> None:
        """expand_connected_retrieval should suppress uncleared seeds rather than raising RuntimeError."""
        from dual_pipeline.graph_retrieval import expand_connected_retrieval

        # Create a mock retrieval dict with an uncleared record ID
        mock_retrieval = {
            "retrieval_mode": "candidate_review",
            "allowed_lanes": ["candidate"],
            "lanes": {
                "candidate": {
                    "results": [
                        {
                            "record_id": "uncleared-legacy-chunk-001",
                            "lane": "candidate",
                            "record": {"record_id": "uncleared-legacy-chunk-001"},
                        }
                    ]
                }
            },
        }

        # Patch _authorized_records to return empty records_by_id (simulating uncleared seeds)
        with patch("dual_pipeline.graph_retrieval_core.traversal._authorized_records") as mock_auth, \
             patch("dual_pipeline.graph_retrieval_core.traversal._load_graph") as mock_load, \
             patch("dual_pipeline.graph_retrieval_core.traversal.validate_sidecar"), \
             patch("dual_pipeline.graph_retrieval_core.traversal.sha256_file", return_value="dummy_hash"), \
             patch("dual_pipeline.graph_retrieval_core.traversal.create_artifact") as mock_art:

            mock_auth.return_value = ({}, {})
            mock_load.return_value = ({}, {}, {}, {}, {}, {}, {})
            mock_art.return_value = {
                "artifact_id": "test",
                "canonical_digest": {"value": "dummy_digest"},
            }

            # Should complete without RuntimeError
            combined, packet = expand_connected_retrieval(
                retrieval=mock_retrieval,
                question="What are candidate patterns?",
                graph_path=Path("dummy_graph.sqlite"),
                sidecar_path=Path("dummy_sidecar.sqlite"),
                run_id="run-test-123",
                graph_snapshot="snap-123",
                query_plan_digest="digest-123",
                requested_hops=1,
                relationships_to_inspect=[],
                classification="INTERNAL",
                audiences=["engineering"],
                retrieval_mode="candidate_review",
            )
            self.assertIsNotNone(combined)
            self.assertIsNotNone(packet)
            self.assertEqual(0, combined["final_record_count"])

    def test_provider_outage_evidence_only_fallback_contract(self) -> None:
        """evidence_only should engage when provider call fails and answer is None."""
        from dual_pipeline.query import evidence_only_over_packet

        plan = {
            "query_type": "cross_workstream_synthesis",
            "evidence_requirements": [],
        }
        l2_packet = {
            "approved": [],
            "candidate": [],
        }
        ans = evidence_only_over_packet(plan, l2_packet, reason_code="PROVIDER_FAILED")
        self.assertIsNotNone(ans)
        self.assertEqual("nee-governed-answer-1.0.0", ans["schema_version"])
        self.assertEqual("cross_workstream_synthesis", ans["query_type"])


if __name__ == "__main__":
    unittest.main()
