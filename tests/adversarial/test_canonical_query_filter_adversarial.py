"""Adversarial regression suite for canonical query filter hardening across 5 edge-case categories."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from dual_pipeline.decision_adapter_core.row_reader import _read_rows
from dual_pipeline.graph_retrieval_core.clearance_cache import _evaluate_record_clearance
from dual_pipeline.graph_writer_core.assertions import AssertionOpsMixin
from dual_pipeline.index_core.anchor_traversal_support import score_assertion_relevance
from dual_pipeline.index_core.eligibility import (
    classify_retrieval_eligibility,
    effective_retrieval_lane,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import CopilotGraphRetriever
from dual_pipeline.query_core.graph_first_engine import GraphFirstQueryEngine
from scripts.hitl_pre_tool_guard import handle_tool_call_guard
from tests.end_to_end.test_canonical_query_filter_e2e import _provision_wave3_test_db
from tools.apply_graph_hygiene_remediation import _remediate_stale_candidates
from tools.mcp_graphdb_server import graphdb_read_query


class CanonicalQueryFilterAdversarialTests(unittest.TestCase):
    """Covers 5 mandatory adversarial categories for canonical assertion query isolation."""

    def test_category1_exact_production_regression_7_years_experience_candidate(self) -> None:
        """Exact production regression: assertion-cd256f2fbf979bd7c87a5fae8fa8 on person_stephane."""
        payload = {
            "candidate": {
                "candidate_id": "candidate-118ee8abd8f46016b1f9fca5b8",
                "candidate_type": "assertion",
                "normalized_text": (
                    "Apex Global applies a screening heuristic favoring at least 7 years of experience, "
                    "requiring candidates with less tenure to demonstrate proven senior aptitude."
                ),
                "materiality": "medium",
                "classification": "confidential",
                "audiences": ["internal_consulting"],
                "extraction_confidence": 0.75,
            },
            "subject_node_id": "person_stephane",
            "predicate": "HAS_CLAIM_TEXT",
            "disposition": "CANDIDATE",
            "edge_traits": {"confidence": {"score": 0.45, "tier": "directional"}},
        }
        source = {"source_id": "src-nea", "source_version_id": "sv-1", "date": "2026-09-14"}
        vals = AssertionOpsMixin._assertion_values(
            payload=payload,
            target_id="assertion-cd256f2fbf979bd7c87a5fae8fa8",
            source=source,
            extraction_run_id="run-1",
            now="2026-09-14T22:08:46Z",
            validation_state="needs_review",
        )
        self.assertEqual(vals[28], "candidate")
        self.assertEqual(vals[29], 0)
        self.assertEqual(vals[30], 0)

        with tempfile.TemporaryDirectory(prefix="eval_sandbox_adv1_") as tmp:
            db_path = Path(tmp) / "graph.sqlite"
            _provision_wave3_test_db(db_path)
            engine = GraphFirstQueryEngine(repo_root=Path(tmp), graph_path=db_path)
            res = engine.execute_query("Cameron Mercer Q4 workstreams pipeline", confidence_threshold=0.65)
            self.assertNotIn(
                "assertion-cd256f2fbf979bd7c87a5fae8fa8",
                [a["assertion_id"] for a in res.assertions],
            )
            self.assertNotIn(
                "assertion-cd256f2fbf979bd7c87a5fae8fa8",
                [r["assertion_id"] for r in _read_rows(db_path)],
            )

    def test_category2_invariant_bypass_attempts_mcp_and_shell_sql(self) -> None:
        """Invariant bypass attempt: unfiltered SELECT * and projected SELECT omitting canonical_status."""
        for raw_sql in (
            "SELECT * FROM assertion WHERE subject_node_id = 'person_stephane'",
            "SELECT assertion_id, object_literal FROM assertion a WHERE a.subject_node_id = 'person_stephane'",
        ):
            mcp_call = json.dumps(
                {
                    "toolCall": {
                        "name": "call_mcp_tool",
                        "args": {
                            "ServerName": "graphdb-inspector",
                            "ToolName": "graphdb_read_query",
                            "Arguments": json.dumps({"query": raw_sql}),
                        },
                    }
                }
            )
            verdict = handle_tool_call_guard(mcp_call)
            self.assertIsNotNone(verdict)
            self.assertEqual("deny", verdict["decision"])

        with tempfile.TemporaryDirectory(prefix="eval_sandbox_adv2_") as tmp:
            db_path = Path(tmp) / "graph.sqlite"
            _provision_wave3_test_db(db_path)
            out = json.loads(
                graphdb_read_query(
                    "SELECT assertion_id, object_literal FROM assertion WHERE subject_node_id = 'person_stephane'",
                    db_path=str(db_path),
                )
            )
            returned_ids = [r["assertion_id"] for r in out["rows"]]
            self.assertEqual(["assertion-canonical-nea-q4"], returned_ids)
            self.assertNotIn("assertion-cd256f2fbf979bd7c87a5fae8fa8", returned_ids)

    def test_category3_boundary_payload_confidence_and_zero_topic_overlap(self) -> None:
        """Boundary payload: confidence threshold boundary (0.64 vs 0.65) and zero non-entity topic overlap."""
        below_floor = {
            "canonical_status": "canonical",
            "canonical_eligible": 1,
            "retrieval_eligible": 1,
            "currentness_state": "current",
            "validation_state": "approved",
            "confidence_score": 0.64,
            "classification": "internal",
            "allowed_audiences_json": '["internal_consulting"]',
        }
        at_floor = dict(below_floor, confidence_score=0.65)
        self.assertFalse(classify_retrieval_eligibility(below_floor)[0])
        self.assertTrue(classify_retrieval_eligibility(at_floor)[0])

        # Entity-only token match with zero topic overlap returns 0 relevance
        self.assertEqual(
            0,
            score_assertion_relevance(
                ["stephan", "marceau", "workstreams", "budget"],
                {
                    "subject_name": "Cameron Mercer",
                    "object_name": "",
                    "predicate": "APPLIES_HEURISTIC",
                    "object_literal": "7 years of experience screening heuristic",
                },
            ),
        )

    def test_category4_malformed_input_null_status_and_corrupted_audiences(self) -> None:
        """Malformed input: null/missing canonical_status, canonical_eligible=0, malformed audiences JSON."""
        malformed_rows = [
            {
                "canonical_status": None,
                "canonical_eligible": None,
                "retrieval_eligible": 1,
                "currentness_state": "current",
                "validation_state": "approved",
                "confidence_score": 0.90,
            },
            {
                "canonical_status": "canonical",
                "canonical_eligible": 0,
                "retrieval_eligible": 1,
                "currentness_state": "current",
                "validation_state": "approved",
                "confidence_score": 0.90,
            },
            {
                "canonical_status": "UNKNOWN_STATUS",
                "canonical_eligible": 1,
                "retrieval_eligible": 1,
                "currentness_state": "current",
                "validation_state": "approved",
                "confidence_score": 0.90,
            },
        ]
        for row in malformed_rows:
            self.assertEqual("candidate", effective_retrieval_lane(row))
            self.assertFalse(classify_retrieval_eligibility(row)[0])
            self.assertFalse(
                _evaluate_record_clearance(
                    {"lane": "approved", "classification": "internal", "audiences": ["internal_consulting"]},
                    row,
                )
            )

        topology = SimpleNamespace(
            nodes={"person_stephane": {"node_id": "person_stephane", "canonical_name": "Cameron Mercer"}},
            assertions={
                "bad-1": {
                    "assertion_id": "bad-1",
                    "subject_node_id": "person_stephane",
                    "predicate": "HAS_CLAIM_TEXT",
                    "claim_text": "Malformed candidate claim",
                    "canonical_status": "PROPOSED",
                    "canonical_eligible": 0,
                    "engagement_id": "nea",
                }
            },
            supporting_evidence={},
            forward_adjacency={},
        )
        retriever = CopilotGraphRetriever(topology=topology, attendees=["Cameron Mercer"], engagement_id="nea")
        self.assertEqual({}, retriever._indexed_assertion_records)

    def test_category5_rollback_and_recovery_hygiene_demotion_idempotency(self) -> None:
        """Rollback & recovery: idempotent stale candidate demotion across assertion and search_document."""
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_adv5_") as tmp:
            db_path = Path(tmp) / "graph.sqlite"
            _provision_wave3_test_db(db_path)
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("ALTER TABLE search_document ADD COLUMN updated_at TEXT DEFAULT ''")
                pkg = {
                    "findings": [
                        {
                            "proposed_fix": "archive_candidate_assertion",
                            "raw_data": {"assertion_id": "assertion-cd256f2fbf979bd7c87a5fae8fa8"},
                        }
                    ]
                }
                # First run demotes both tables
                c1, arch1 = _remediate_stale_candidates(conn, pkg, "2026-10-21T16:00:00Z", [])
                conn.commit()
                self.assertEqual(1, c1)
                self.assertEqual(["assertion-cd256f2fbf979bd7c87a5fae8fa8"], arch1)

                # Second run is idempotent: state across both tables remains demoted with retrieval_eligible=0
                c2, arch2 = _remediate_stale_candidates(conn, pkg, "2026-10-21T16:05:00Z", [])
                conn.commit()
                self.assertEqual(1, c2)
                self.assertEqual(["assertion-cd256f2fbf979bd7c87a5fae8fa8"], arch2)

                asrt = conn.execute(
                    "SELECT canonical_status, currentness_state, canonical_eligible, retrieval_eligible FROM assertion WHERE assertion_id='assertion-cd256f2fbf979bd7c87a5fae8fa8'"
                ).fetchone()
                sd = conn.execute(
                    "SELECT canonical_status, currentness_state, retrieval_eligible FROM search_document WHERE assertion_id='assertion-cd256f2fbf979bd7c87a5fae8fa8'"
                ).fetchone()
                self.assertEqual(("historical_candidate", "historical", 0, 0), tuple(asrt))
                self.assertEqual(("historical_candidate", "historical", 0), tuple(sd))
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
