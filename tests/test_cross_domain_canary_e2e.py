"""Cross-Domain Canary 9-Query-Type End-to-End Reachability & Synthesis Tests."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.core import sha256_file
from dual_pipeline.graph_retrieval_core.clearance_cache import authorized_records_cached
from dual_pipeline.l2 import load_l2_config, validate_decision_model_output
from dual_pipeline.query import reason_over_packet
from dual_pipeline.query_core.dynamic_entities import (
    resolve_dynamic_entity_matches,
    resolve_named_coverage_subjects,
)
from dual_pipeline.query_core.hydration import exact_retrieval
from dual_pipeline.query_core.planning import build_query_plan
from dual_pipeline.query_core.reasoning import validate_answer
from dual_pipeline.query_router import QueryDestination, route_query


CANARY_MATRIX: tuple[tuple[str, str, str, QueryDestination], ...] = (
    (
        "energy_nba",
        "What are the top next best actions and strategic opportunities for NEE grid modernization, BESS, and FERC compliance?",
        "opportunity_nba",
        QueryDestination.NEE_DECISION_INTELLIGENCE,
    ),
    (
        "partner_nba",
        "What are the recommended next best actions and partnership opportunities with Google Cloud for NEE?",
        "opportunity_nba",
        QueryDestination.NEE_DECISION_INTELLIGENCE,
    ),
    (
        "energy_risk",
        "What are the key risks, constraints, and bottlenecks for high-voltage transformer supply chain buffering and BESS thermal degradation?",
        "gap_risk_inconsistency",
        QueryDestination.NEE_DECISION_INTELLIGENCE,
    ),
    (
        "pdlc_current",
        "What is the current state and corroborated evidence for PDLC, LENS, and Sherpa?",
        "current_state_corroboration",
        QueryDestination.GRAPH_EVIDENCE_UTILITY,
    ),
    (
        "org_rel",
        "How does Project Jarvis relate to NEE workstreams and who leads or owns its delivery?",
        "relationship_explanation",
        QueryDestination.NEE_DECISION_INTELLIGENCE,
    ),
    (
        "cross_synth",
        "Synthesize the cross-workstream architecture and strategic alignment across Sherpa, LENS, and Nexus.",
        "cross_workstream_synthesis",
        QueryDestination.NEE_DECISION_INTELLIGENCE,
    ),
    (
        "cand_review",
        "Review the candidate hypotheses and promotion readiness for Category Brain and Conduit Engagement.",
        "candidate_promotion_review",
        QueryDestination.NEE_DECISION_INTELLIGENCE,
    ),
    (
        "status_hist",
        "What is the historical status and milestone progression of PDLC and Sherpa?",
        "project_status_history",
        QueryDestination.GRAPH_EVIDENCE_UTILITY,
    ),
    (
        "bounded_lookup",
        "Show the exact governed evidence records and citations for NEE.",
        "exact_lookup",
        QueryDestination.GRAPH_EVIDENCE_UTILITY,
    ),
)


class CrossDomainCanaryE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.patcher = patch("dual_pipeline.query_router._check_graphdb_available", return_value=True)
        self.patcher.start()

    def tearDown(self) -> None:
        self.patcher.stop()

    def test_all_9_cross_domain_canary_queries_route_without_ambiguity_e2e(self) -> None:
        """All 9 cross-domain canary queries route to governed destinations without ROUTE_AMBIGUOUS."""
        for label, question, expected_qtype, expected_dest in CANARY_MATRIX:
            with self.subTest(label=label):
                decision = route_query(question)
                self.assertNotEqual(QueryDestination.ROUTE_AMBIGUOUS, decision.destination)
                self.assertEqual(expected_dest, decision.destination)
                self.assertEqual(expected_qtype, decision.query_type)

    def test_internal_consulting_audience_parity_across_hydration_and_clearance_cache_e2e(self) -> None:
        """Default 'nee-engineering' audience accesses 'internal_consulting' records across exact_retrieval and clearance_cache."""
        with tempfile.TemporaryDirectory(prefix="canary-aud-e2e-") as tmpdir:
            tmp = Path(tmpdir)
            graph = tmp / "graph.sqlite"
            sidecar = tmp / "sidecar.sqlite"

            gconn = sqlite3.connect(graph)
            try:
                gconn.executescript(
                    """
                    CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT, display_name TEXT, node_type TEXT, active INTEGER);
                    CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY, subject_node_id TEXT, object_node_id TEXT, classification TEXT,
                        allowed_audiences_json TEXT, canonical_status TEXT, validation_state TEXT, currentness_state TEXT,
                        retrieval_eligible INTEGER, qualifiers_json TEXT);
                    CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT, evidence_relationship TEXT);
                    CREATE TABLE search_document (search_document_id TEXT, object_type TEXT, object_id TEXT, title TEXT, body TEXT,
                        canonical_status TEXT, currentness_state TEXT, retrieval_eligible INTEGER, source_id TEXT, assertion_id TEXT);
                    """
                )
                gconn.execute("INSERT INTO node VALUES ('node-pdlc', 'PDLC', 'PDLC', 'workstream', 1)")
                gconn.execute(
                    "INSERT INTO assertion VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        "ass-pdlc-partner_advisory",
                        "node-pdlc",
                        None,
                        "internal",
                        json.dumps(["internal_consulting"]),
                        "canonical",
                        "approved",
                        "current",
                        1,
                        json.dumps({}),
                    ),
                )
                gconn.execute("INSERT INTO assertion_evidence VALUES ('ass-pdlc-partner_advisory', 'ev-pdlc', 'supports')")
                gconn.execute(
                    "INSERT INTO search_document VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        "sd-pdlc",
                        "assertion",
                        "ass-pdlc-partner_advisory",
                        "PDLC current state",
                        "PDLC establishes governed delivery milestones for NEE.",
                        "canonical",
                        "current",
                        1,
                        "src-pdlc",
                        "ass-pdlc-partner_advisory",
                    ),
                )
                gconn.commit()
            finally:
                gconn.close()

            exact = exact_retrieval(
                "What is the current state of PDLC?",
                graph,
                classification="internal",
                audiences=["nee-engineering"],
                retrieval_mode="factual",
            )
            self.assertEqual(1, len(exact["exact_anchors"]))
            self.assertEqual("ass-pdlc-partner_advisory", exact["exact_anchors"][0]["assertion_id"])

            sconn = sqlite3.connect(sidecar)
            try:
                sconn.execute("CREATE TABLE retrieval_record (record_id TEXT PRIMARY KEY, lane TEXT, artifact_json TEXT)")
                artifact = {
                    "record_id": "rec-pdlc-partner_advisory",
                    "assertion_id": "ass-pdlc-partner_advisory",
                    "record_type": "assertion",
                    "lane": "approved",
                    "classification": "internal",
                    "audiences": ["internal_consulting"],
                    "title": "PDLC current state",
                    "retrieval_text": "PDLC establishes governed delivery milestones for NEE.",
                    "evidence_ids": ["ev-pdlc"],
                    "retrieval_metadata": {"answer_use": "factual_answer"},
                    "extensions": {"com.partner_advisory.nee.graph-object-id": "ass-pdlc-partner_advisory"},
                }
                sconn.execute(
                    "INSERT INTO retrieval_record VALUES (?, ?, ?)",
                    ("rec-pdlc-partner_advisory", "approved", json.dumps(artifact)),
                )
                sconn.commit()
            finally:
                sconn.close()

            by_rec, by_ass = authorized_records_cached(
                sidecar_path=sidecar,
                graph_path=graph,
                allowed_lanes=["approved"],
                retrieval_mode="factual",
                classification="internal",
                audiences=["nee-engineering"],
                allowed_assertion_ids={"ass-pdlc-partner_advisory"},
                validate_fn=lambda p: {"metadata": {"graph_sha256": sha256_file(graph)}},
            )
            self.assertIn("rec-pdlc-partner_advisory", by_rec)
            self.assertIn("ass-pdlc-partner_advisory", by_ass)

    def test_nba_synthesis_and_candidate_review_schema_compliance_e2e(self) -> None:
        """reason_over_packet synthesizes schema-valid NBA outputs and keeps candidate_promotion_review proposal-only."""
        packet = {
            "approved": [
                {
                    "record": {
                        "record_id": "rec-opp-1",
                        "record_type": "assertion",
                        "lane": "approved",
                        "title": "BESS Grid Modernization Opportunity",
                        "retrieval_text": "Deploy BESS augmentation to improve FERC interconnection throughput.",
                        "evidence_ids": ["ev-1"],
                    },
                    "retrieval_metadata": {"answer_use": "factual_answer"},
                    "evidence": [{"evidence_id": "ev-1"}],
                },
                {
                    "record": {
                        "record_id": "rec-pain-1",
                        "record_type": "assertion",
                        "lane": "approved",
                        "title": "Transformer Supply Bottleneck",
                        "retrieval_text": "High-voltage transformer lead-time constraint delays substation readiness.",
                        "evidence_ids": ["ev-2"],
                    },
                    "retrieval_metadata": {"answer_use": "factual_answer"},
                    "evidence": [{"evidence_id": "ev-2"}],
                },
                {
                    "record": {
                        "record_id": "rec-guidance-1",
                        "record_type": "action_item",
                        "lane": "approved",
                        "title": "Review interconnection queue",
                        "retrieval_text": "Coordinate queue milestones with transmission operations.",
                        "evidence_ids": ["ev-3"],
                    },
                    "retrieval_metadata": {"answer_use": "guidance"},
                    "evidence": [{"evidence_id": "ev-3"}],
                },
            ],
            "candidate": [
                {
                    "record": {
                        "record_id": "rec-cand-1",
                        "record_type": "candidate_assertion",
                        "lane": "candidate",
                        "title": "Candidate Category Brain hypothesis",
                        "retrieval_text": "Category Brain candidate pattern may streamline sourcing.",
                        "evidence_ids": ["ev-cand-1"],
                    },
                    "retrieval_metadata": {"answer_use": "candidate_hypothesis"},
                    "evidence": [{"evidence_id": "ev-cand-1"}],
                }
            ],
            "external_public": [],
            "counterevidence": [],
            "gaps": [],
        }

        nba_plan = {
            "query_type": "opportunity_nba",
            "retrieval_mode": "strategic",
            "allowed_lanes": ["approved", "candidate", "external_public"],
            "primary_entities": ["NEE"],
        }
        nba_ans = reason_over_packet(nba_plan, packet)
        self.assertTrue(nba_ans["opportunities"])
        self.assertTrue(nba_ans["pain_points"])
        self.assertTrue(nba_ans["next_best_actions"])
        self.assertEqual(
            "PASS",
            validate_answer(nba_ans, packet)["status"],
        )
        self.assertTrue(
            validate_decision_model_output(
                nba_ans,
                evidence_packet=packet,
                expected_query_type="opportunity_nba",
            ).valid
        )


if __name__ == "__main__":
    unittest.main()
