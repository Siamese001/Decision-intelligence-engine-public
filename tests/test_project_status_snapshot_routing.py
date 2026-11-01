"""Regression coverage for dated, citation-only project-status queries."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.l2 import load_l2_config, query_retrieval_policy
from dual_pipeline.project_status import build_snapshot_profile, validate_snapshot_profile
from dual_pipeline.query import (
    _delivery_evidence_ready,
    build_query_plan,
    build_query_route,
    exact_retrieval,
)
from dual_pipeline.query_router import QueryDestination, route_query


class ProjectStatusSnapshotRoutingTests(unittest.TestCase):
    def test_snapshot_profile_is_dated_and_cannot_be_promoted(self) -> None:
        profile = build_snapshot_profile(scope_id="edo-program", as_of_date="2026-06-29")
        self.assertEqual("citation_only_weekly_snapshot", profile["source_posture"])
        self.assertFalse(profile["fact_promotion_eligible"])
        with self.assertRaisesRegex(ValueError, "invalid values"):
            validate_snapshot_profile({**profile, "fact_promotion_eligible": True})

    def test_current_status_is_provider_free_citation_only_retrieval(self) -> None:
        question = "What is the current status of the EDO Atlas Pilot project?"
        decision = route_query(question)
        self.assertEqual(QueryDestination.GRAPH_EVIDENCE_UTILITY, decision.destination)
        self.assertEqual("project_status_current", decision.query_type)
        self.assertIn("PROJECT_STATUS_SNAPSHOT_CURRENT", decision.reason_codes)
        mode, lanes = query_retrieval_policy(decision.query_type)
        self.assertEqual("status_snapshot_current", mode)
        self.assertEqual(("approved", "candidate"), lanes)

        destination = decision.to_artifact(
            request_id="request-status-current-test",
            run_id="run-status-current-test",
            trace_id="0123456789abcdef0123456789abcdef",
            decided_at="2026-08-04T12:00:00Z",
        )
        plan = build_query_plan(
            question=question,
            query_type=decision.query_type,
            request_id=destination["request_id"],
            run_id=destination["run_id"],
            current_trace_id=destination["trace_id"],
            l2_config=load_l2_config(),
            provider_egress_allowed=True,
            destination_decision=destination,
            assurance_profile=None,
        )
        validate_artifact(plan)
        self.assertEqual("status_snapshot_current", plan["retrieval_mode"])
        self.assertEqual(["approved", "candidate"], plan["allowed_lanes"])
        self.assertFalse(plan["provider_egress_allowed"])
        self.assertEqual(0, plan["maximum_primary_calls"])
        self.assertIn("citation-only", " ".join(plan["evidence_requirements"]).lower())
        self.assertEqual("PROJECT_STATUS_CURRENT", build_query_route(plan)["query_route"])

    def test_current_mode_excludes_older_snapshot_before_hybrid_retrieval(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nee-project-status-") as directory:
            graph = Path(directory) / "graph.sqlite"
            connection = sqlite3.connect(graph)
            try:
                connection.executescript(
                    """
                    CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY, subject_node_id TEXT,
                        object_node_id TEXT, classification TEXT, allowed_audiences_json TEXT,
                        canonical_status TEXT, validation_state TEXT, currentness_state TEXT,
                        retrieval_eligible INTEGER, qualifiers_json TEXT);
                    CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT,
                        evidence_relationship TEXT);
                    CREATE TABLE search_document (search_document_id TEXT, object_type TEXT,
                        object_id TEXT, title TEXT, body TEXT, canonical_status TEXT,
                        currentness_state TEXT, retrieval_eligible INTEGER, source_id TEXT,
                        assertion_id TEXT);
                    CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT,
                        node_type TEXT, active INTEGER);
                    """
                )
                connection.execute("INSERT INTO node VALUES ('edo','EDO','program',1)")
                for assertion_id, as_of_date in (("status-old", "2026-06-15"), ("status-new", "2026-06-29")):
                    qualifiers = {
                        "dual_pipeline_candidate_type": "source_chunk",
                        "project_status_snapshot": {
                            "schema_version": "1.0.0", "snapshot_id": assertion_id,
                            "scope_id": "edo-program", "as_of_date": as_of_date,
                            "source_posture": "citation_only_weekly_snapshot",
                            "fact_promotion_eligible": False,
                        },
                    }
                    connection.execute(
                        "INSERT INTO assertion VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (assertion_id, "edo", None, "internal", json.dumps(["internal_consulting"]),
                         "candidate", "needs_review", "current", 0, json.dumps(qualifiers)),
                    )
                    connection.execute("INSERT INTO assertion_evidence VALUES (?,?,?)", (assertion_id, assertion_id + "-e", "supports"))
                    connection.execute(
                        "INSERT INTO search_document VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (assertion_id + "-search", "assertion", assertion_id, "EDO status",
                         "EDO Atlas Pilot status", "candidate", "current", 0, "edo-status", assertion_id),
                    )
                connection.commit()
            finally:
                connection.close()
            current = exact_retrieval(
                "What is the current status of the EDO Atlas Pilot project?", graph,
                classification="internal", audiences=["internal_consulting"],
                retrieval_mode="status_snapshot_current",
            )
            self.assertEqual(["status-new"], current["status_snapshot_assertion_ids"])
            self.assertEqual("latest_per_scope", current["status_snapshot_selection"])
            history = exact_retrieval(
                "Show the status history of the EDO Atlas Pilot project.", graph,
                classification="internal", audiences=["internal_consulting"],
                retrieval_mode="status_snapshot_history",
            )
            self.assertEqual(["status-new", "status-old"], history["status_snapshot_assertion_ids"])

    def test_status_history_is_explicit_and_update_work_stays_operational(self) -> None:
        history = route_query(
            "Show the status history of the EDO Atlas Pilot project since June 2026."
        )
        self.assertEqual(QueryDestination.GRAPH_EVIDENCE_UTILITY, history.destination)
        self.assertEqual("project_status_history", history.query_type)
        self.assertIn("PROJECT_STATUS_SNAPSHOT_HISTORY", history.reason_codes)

        update = route_query("Update the current status of the EDO Atlas Pilot project.")
        self.assertEqual(QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF, update.destination)
        self.assertIsNone(update.query_type)

    def test_citation_only_status_evidence_is_deliverable_at_x2(self) -> None:
        packet = {"approved": [], "candidate": [{"record": {"record_id": "status"}}]}
        self.assertTrue(
            _delivery_evidence_ready(
                {"retrieval_mode": "status_snapshot_current"}, packet
            )
        )
        self.assertTrue(
            _delivery_evidence_ready(
                {"retrieval_mode": "status_snapshot_history"}, packet
            )
        )
        self.assertFalse(
            _delivery_evidence_ready({"retrieval_mode": "factual"}, packet)
        )


if __name__ == "__main__":
    unittest.main()
