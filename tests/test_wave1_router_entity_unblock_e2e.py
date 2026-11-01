"""Wave 1 End-to-End tests: Front-Door Query Routing, Dynamic Entity Recognition & Status Lane Unblocking."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.l2 import load_l2_config, query_retrieval_policy
from dual_pipeline.project_aliases import get_known_projects
from dual_pipeline.query_core.constants import _entities
from dual_pipeline.query_core.dynamic_entities import resolve_dynamic_entity_matches
from dual_pipeline.query_core.hydration import exact_retrieval
from dual_pipeline.query_core.planning import build_query_plan
from dual_pipeline.query_router import (
    QueryDestination,
    determine_query_project,
    route_query,
)


class Wave1RouterEntityUnblockE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.patcher = patch("dual_pipeline.query_router._check_graphdb_available", return_value=True)
        self.patcher.start()

    def tearDown(self) -> None:
        self.patcher.stop()

    def test_all_23_canonical_projects_route_to_governed_destinations_e2e(self) -> None:
        """All 23 canonical projects route analytical, status, and bounded queries without false handoff."""
        known_projects = sorted(get_known_projects())
        self.assertGreaterEqual(len(known_projects), 22)

        project_display_queries = [
            ("bmo", "Recommend next best actions and priorities for BMO."),
            ("conduit", "Diagnose the risks and gaps in Conduit."),
            ("project_beacon", "Evaluate Project Beacon options and trade-offs."),
            ("dociq", "Recommend next best actions for DocIQ."),
            ("ec_scheduling", "Assess the risks and gaps for EC Scheduling."),
            ("edo", "Evaluate EDO architecture options and trade-offs."),
            ("enterprise_architecture", "Recommend an Enterprise Architecture design across patterns."),
            ("enterprise_account_planning", "Assess risks and opportunities for Enterprise Account Planning."),
            ("project_atlas", "Recommend next best actions for Project Atlas."),
            ("gemini_enablement", "Evaluate Gemini Enablement adoption opportunities."),
            ("hrit", "Diagnose risks and gaps for HRIT and ENTERPRISE4U."),
            ("infrastructure_automation", "Recommend priorities for Infrastructure Automation."),
            ("internal_consulting", "Assess opportunities and risks for Internal Consulting."),
            ("market_intelligence", "Recommend next best actions from Market Intelligence and Weekly Trends."),
            ("nea", "Evaluate NEA platform options and priorities."),
            ("apex_trans", "Diagnose risks and gaps in APEX_TRANS Insights Hub."),
            ("partner_alliances", "Recommend next best actions for Partner Alliances."),
            ("project_jarvis", "Evaluate Project Jarvis architecture and next best actions."),
            ("project_lens", "Diagnose risks and gaps for Project Lens."),
            ("project_nea", "Recommend next best actions for Project NEA."),
            ("project_nexus", "Evaluate Project Nexus options and risks."),
            ("project_spring", "Assess risks and gaps for Project Spring."),
            ("regai", "Recommend next best actions for RegAI and FCDB."),
        ]

        for expected_proj, question in project_display_queries:
            with self.subTest(project=expected_proj, question=question):
                resolved_proj = determine_query_project(question)
                if expected_proj == "project_nea":
                    self.assertIn(resolved_proj, {"project_nea", "nea"})
                else:
                    self.assertEqual(expected_proj, resolved_proj)
                decision = route_query(question)
                self.assertEqual(QueryDestination.NEE_DECISION_INTELLIGENCE, decision.destination)
                self.assertIn(
                    decision.query_type,
                    {"opportunity_nba", "gap_risk_inconsistency", "cross_workstream_synthesis"},
                )
                artifact = decision.to_artifact(
                    request_id=f"req-{expected_proj}",
                    run_id=f"run-{expected_proj}",
                    trace_id="0123456789abcdef0123456789abcdef",
                    decided_at="2026-10-27T10:00:00Z",
                )
                validate_artifact(artifact)

    def test_status_routes_authorize_approved_and_candidate_lanes_e2e(self) -> None:
        """project_status_current and project_status_history authorize both approved and candidate lanes."""
        for query_type, expected_mode, question in (
            (
                "project_status_current",
                "status_snapshot_current",
                "What is the current status of DocIQ?",
            ),
            (
                "project_status_history",
                "status_snapshot_history",
                "Show the status history of Project Jarvis since June 2026.",
            ),
        ):
            decision = route_query(question)
            self.assertEqual(QueryDestination.GRAPH_EVIDENCE_UTILITY, decision.destination)
            self.assertEqual(query_type, decision.query_type)
            mode, lanes = query_retrieval_policy(decision.query_type)
            self.assertEqual(expected_mode, mode)
            self.assertEqual(("approved", "candidate"), lanes)

            dest_artifact = decision.to_artifact(
                request_id=f"req-{query_type}",
                run_id=f"run-{query_type}",
                trace_id="0123456789abcdef0123456789abcdef",
                decided_at="2026-10-27T10:00:00Z",
            )
            plan = build_query_plan(
                question=question,
                query_type=decision.query_type,
                request_id=dest_artifact["request_id"],
                run_id=dest_artifact["run_id"],
                current_trace_id=dest_artifact["trace_id"],
                l2_config=load_l2_config(),
                provider_egress_allowed=True,
                destination_decision=dest_artifact,
                assurance_profile=None,
            )
            validate_artifact(plan)
            self.assertEqual(["approved", "candidate"], plan["allowed_lanes"])

        # Verify exact_retrieval returns approved status assertions when no candidate snapshot dict exists
        with tempfile.TemporaryDirectory(prefix="wave1-status-e2e-") as tmpdir:
            graph = Path(tmpdir) / "graph.sqlite"
            conn = sqlite3.connect(graph)
            try:
                conn.executescript(
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
                conn.execute("INSERT INTO node VALUES ('node-dociq', 'DocIQ', 'DocIQ', 'workstream', 1)")
                conn.execute(
                    "INSERT INTO assertion VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        "ass-dociq-approved",
                        "node-dociq",
                        None,
                        "internal",
                        json.dumps(["nee-engineering"]),
                        "canonical",
                        "approved",
                        "current",
                        1,
                        json.dumps({}),
                    ),
                )
                conn.execute("INSERT INTO assertion_evidence VALUES ('ass-dociq-approved', 'ev-1', 'supports')")
                conn.execute(
                    "INSERT INTO search_document VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        "sd-1",
                        "assertion",
                        "ass-dociq-approved",
                        "DocIQ status",
                        "DocIQ returned to green status after architecture approval",
                        "canonical",
                        "current",
                        1,
                        "src-1",
                        "ass-dociq-approved",
                    ),
                )
                conn.commit()
            finally:
                conn.close()

            exact = exact_retrieval(
                "What is the current status of DocIQ?",
                graph,
                classification="internal",
                audiences=["nee-engineering"],
                retrieval_mode="status_snapshot_current",
            )
            self.assertEqual(["ass-dociq-approved"], exact["status_snapshot_assertion_ids"])
            self.assertEqual(1, len(exact["exact_anchors"]))
            self.assertEqual("approved", exact["exact_anchors"][0]["lane"])

    def test_dynamic_entity_resolution_multi_node_split_identity_and_no_nee_pollution_e2e(self) -> None:
        """Dynamic entity resolution matches split-identity twins and does not inject spurious 'NEE'."""
        with tempfile.TemporaryDirectory(prefix="wave1-dyn-ent-") as tmpdir:
            graph = Path(tmpdir) / "graph.sqlite"
            conn = sqlite3.connect(graph)
            try:
                conn.executescript(
                    """
                    CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT, display_name TEXT, node_type TEXT, active INTEGER);
                    CREATE TABLE canonical_alias (entity_id TEXT, display_alias TEXT, active INTEGER);
                    """
                )
                conn.execute("INSERT INTO node VALUES ('person_rich_argentieri', 'Rich Argentieri', 'Rich Argentieri', 'person', 1)")
                conn.execute("INSERT INTO node VALUES ('person:richard-argentieri', 'Richard Argentieri', 'Richard Argentieri', 'person', 1)")
                conn.execute("INSERT INTO node VALUES ('org_nee', 'NEE', 'Apex Global Enterprises', 'organization', 1)")
                conn.execute("INSERT INTO canonical_alias VALUES ('person_rich_argentieri', 'Argentieri', 1)")
                conn.execute("INSERT INTO canonical_alias VALUES ('person:richard-argentieri', 'Argentieri', 1)")
                conn.commit()

                matches = resolve_dynamic_entity_matches(
                    conn, "What are the priorities for Argentieri?"
                )
            finally:
                conn.close()

            matched_ids = {m["node_id"] for m in matches}
            self.assertIn("person_rich_argentieri", matched_ids)
            self.assertIn("person:richard-argentieri", matched_ids)
            self.assertNotIn("org_nee", matched_ids)
            self.assertEqual([], _entities("What are the priorities for Argentieri?", default_to_nee=False))


if __name__ == "__main__":
    unittest.main()
