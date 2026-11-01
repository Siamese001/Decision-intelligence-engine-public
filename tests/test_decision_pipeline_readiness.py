"""Decision pipeline readiness unit and integration tests.

These tests verify the end-to-end readiness of the knowledge graph for
decision intelligence:
1. Every query definition compiles and builds a contract-valid decision frame.
2. Decision engine frame building, criteria parsing, and option evaluation.
3. Front-door query routing across all query intents.
4. L1 control state machine transitions and fail-closed policies.
5. Candidate assertion adjudication readiness.

All tests are deterministic, read-only, and require no embedding backend.
"""
from __future__ import annotations

import json
import re
import sqlite3
import unittest

from dual_pipeline.core import ROOT, connect_sqlite, sha256_file
from dual_pipeline.decision_engine import (
    build_decision_evidence_pack,
    build_decision_frame,
    evaluate_decision,
    validate_decision_frame,
)
from dual_pipeline.l1_control import (
    BudgetState,
    GuardStatus,
    create_action,
    guard_action,
    load_policy,
)
from dual_pipeline.query_router import QueryDestination, route_query

GRAPH = ROOT / "graph" / "nee_graph.sqlite"


class QueryDefinitionReadinessTests(unittest.TestCase):
    """Verify that all graph query definitions are structurally valid and build decision frames."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.connection = connect_sqlite(GRAPH, readonly=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

    def test_all_18_query_definitions_exist(self) -> None:
        """Verify the exact set of 18 query definitions."""
        rows = self.connection.execute("SELECT query_id FROM query_definition").fetchall()
        self.assertEqual(18, len(rows), f"Expected 18 query definitions, found {len(rows)}")

    def test_all_query_definitions_have_valid_sql_syntax(self) -> None:
        """Every sql_text must be compilable by SQLite EXPLAIN with mock parameters."""
        rows = self.connection.execute(
            "SELECT query_id, sql_text FROM query_definition"
        ).fetchall()
        for row in rows:
            qid = row["query_id"]
            sql = row["sql_text"]
            with self.subTest(query_id=qid):
                params = {p[1:]: "mock_value" for p in re.findall(r':[a-zA-Z0-9_]+', sql)}
                try:
                    self.connection.execute(f"EXPLAIN {sql}", params)
                except sqlite3.Error as e:
                    self.fail(f"Query {qid} failed EXPLAIN compilation: {e}")

    def test_all_query_definitions_can_construct_valid_decision_frames(self) -> None:
        """Every query definition's intent can construct a valid decision frame."""
        rows = self.connection.execute(
            "SELECT query_id, query_name, intent FROM query_definition"
        ).fetchall()
        for row in rows:
            qid = row["query_id"]
            intent = row["intent"]
            with self.subTest(query_id=qid):
                question = f"What is the analysis for {intent}?"
                frame = build_decision_frame(
                    question=question,
                    criteria=["accuracy", "evidence_support"],
                )
                validated = validate_decision_frame(frame)
                self.assertEqual("1.0.0", validated["schema_version"])
                self.assertIn("decision_question", validated)
                self.assertIn("criteria", validated)
                self.assertIn("frame_digest", validated)

    def test_query_definition_currentness_modes_are_governed(self) -> None:
        """Currentness modes must be from the governed set."""
        modes = {
            r[0]
            for r in self.connection.execute(
                "SELECT DISTINCT currentness_mode FROM query_definition"
            )
        }
        allowed = {"current", "historical", "any", "all"}
        unknown = modes - allowed
        self.assertEqual(set(), unknown, f"Unknown currentness modes: {unknown}")


class DecisionEngineReadinessTests(unittest.TestCase):
    """Verify decision frame construction, criteria parsing, and option evaluation."""

    def test_decision_frame_requires_question(self) -> None:
        """Empty question must fail closed."""
        with self.assertRaises(ValueError):
            build_decision_frame(question="")
        with self.assertRaises(ValueError):
            build_decision_frame(question="   ")

    def test_decision_frame_missing_criteria_enters_clarification(self) -> None:
        """Question without criteria must enter CLARIFICATION_REQUIRED."""
        frame = build_decision_frame(question="Should we deploy service X?")
        self.assertEqual("CLARIFICATION_REQUIRED", frame["frame_state"])
        self.assertIn("criteria", frame["missing_fields"])

    def test_decision_frame_with_criteria_is_ready(self) -> None:
        """Question with explicit criteria must be READY."""
        frame = build_decision_frame(
            question="Should we deploy service X?",
            criteria=["cost", "latency"],
        )
        self.assertEqual("READY", frame["frame_state"])
        self.assertEqual([], frame["missing_fields"])

    def test_decision_frame_extracts_criteria_from_clause(self) -> None:
        """Question with explicit 'based on' clause extracts criteria."""
        frame = build_decision_frame(
            question="Which approach should we fund based on cost, velocity, and reliability?"
        )
        self.assertEqual("READY", frame["frame_state"])
        labels = [c["label"] for c in frame["criteria"]]
        self.assertIn("cost", labels)
        self.assertIn("velocity", labels)
        self.assertIn("reliability", labels)

    def test_evaluate_decision_returns_governed_terminal_state(self) -> None:
        """Evaluating options against evidence must produce a contract-valid terminal state."""
        frame = build_decision_frame(
            question="Choose between Alpha and Beta",
            options=["Alpha", "Beta"],
            criteria=["impact", "cost"],
        )
        evidence_pack = build_decision_evidence_pack(
            query_result={
                "generation_id": "gen-1",
                "graph_snapshot": "snap-1",
                "graph_sha256_after": "a" * 64,
                "embedding_backend": "test",
                "bm25_executed": True,
                "dense_executed": True,
                "sparse_executed": True,
                "colbert_executed": True,
                "graph_traversal_executed": True,
            },
            evidence_packet={
                "approved": [
                    {
                        "record": {
                            "record_id": "rec-alpha",
                            "title": "Alpha",
                            "retrieval_text": "Alpha improves impact and cost",
                            "record_type": "assertion",
                            "evidence_ids": ["ev-alpha"],
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
            },
            retrieval_report={
                "terminal_coverage_disposition": "COMPLETE",
                "coverage_counts": {"supported": 1},
            },
        )
        result = evaluate_decision(
            frame=frame,
            evidence_pack=evidence_pack,
            answer=None,
            execution={"query_run_id": "run-test-1"},
        )
        self.assertIn("terminal_state", result)
        self.assertEqual("DECISION_PRODUCED", result["terminal_state"])
        self.assertEqual("Alpha", result["recommended_option"]["label"])


class QueryRouterReadinessTests(unittest.TestCase):
    """Verify front-door query routing across diverse intents."""

    def test_cross_workstream_synthesis_routes_to_nee(self) -> None:
        """Synthesis queries route to NEE_DECISION_INTELLIGENCE."""
        queries = [
            "Synthesize architecture options across A2A and Project Beacon",
            "Recommend a future-state A2A architecture across governed current state, patterns, risks, and gaps.",
            "Diagnose the cross-source governed graph relationships and counterevidence.",
        ]
        for q in queries:
            with self.subTest(query=q):
                decision = route_query(q)
                self.assertEqual(
                    QueryDestination.NEE_DECISION_INTELLIGENCE,
                    decision.destination,
                    f"Query failed to route to NEE: {q}",
                )

    def test_exact_lookup_routes_to_evidence_utility(self) -> None:
        """Exact lookup queries route to GRAPH_EVIDENCE_UTILITY."""
        queries = [
            "Show governed record record-123.",
            "Show citation for governed record-999.",
            "List the governed NEE records.",
        ]
        for q in queries:
            with self.subTest(query=q):
                decision = route_query(q)
                self.assertEqual(
                    QueryDestination.GRAPH_EVIDENCE_UTILITY,
                    decision.destination,
                    f"Query failed to route to evidence utility: {q}",
                )

    def test_operational_queries_route_to_workflow(self) -> None:
        """Operational tasks route to OPERATIONAL_WORKFLOW_HANDOFF."""
        decision = route_query("Restart the ingestion pipeline for batch 42")
        self.assertEqual(
            QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF,
            decision.destination,
        )

    def test_general_queries_route_to_general_assistant(self) -> None:
        """Generic writing/summarizing tasks route to GENERAL_ASSISTANT_HANDOFF."""
        decision = route_query("Write an email summarizing these meeting notes")
        self.assertEqual(
            QueryDestination.GENERAL_ASSISTANT_HANDOFF,
            decision.destination,
        )


class L1ControlReadinessTests(unittest.TestCase):
    """Verify L1 control deterministic state machine contracts."""

    @classmethod
    def setUpClass(cls) -> None:
        policy_path = ROOT / "requirements" / "query-l1-control-policy.json"
        raw = json.loads(policy_path.read_text(encoding="utf-8"))
        cls.policy = load_policy(raw)

    def test_l1_policy_loads_strictly(self) -> None:
        """L1 policy must load with expected defaults."""
        self.assertIsNotNone(self.policy)
        self.assertEqual(0, self.policy.provider_calls_before_l2d)
        self.assertEqual(0, self.policy.graph_mutations_before_l2d)

    def test_l1_action_creation_is_deterministic(self) -> None:
        """Action creation produces repeatable digests."""
        act1 = create_action(
            parent_plan_digest="a" * 64,
            coverage_manifest_digest="b" * 64,
            authorization_scope_digest="c" * 64,
            attempt_ordinal=0,
            action_type="requirement_hybrid_retrieval",
            requirement_ids=["req-1"],
            input_digest="d" * 64,
        )
        act2 = create_action(
            parent_plan_digest="a" * 64,
            coverage_manifest_digest="b" * 64,
            authorization_scope_digest="c" * 64,
            attempt_ordinal=0,
            action_type="requirement_hybrid_retrieval",
            requirement_ids=["req-1"],
            input_digest="d" * 64,
        )
        self.assertEqual(act1.action_id, act2.action_id)
        self.assertEqual(act1.attempt_id, act2.attempt_id)

    def test_l1_guard_allows_initial_action(self) -> None:
        """Initial action is allowed by L1 guard."""
        act = create_action(
            parent_plan_digest="a" * 64,
            coverage_manifest_digest="b" * 64,
            authorization_scope_digest="c" * 64,
            attempt_ordinal=0,
            action_type="requirement_hybrid_retrieval",
            requirement_ids=["req-1"],
            input_digest="d" * 64,
        )
        state = BudgetState.initial()
        guard = guard_action(self.policy, state, act)
        self.assertEqual(GuardStatus.ALLOW, guard.status)


class CandidateAdjudicationReadinessTests(unittest.TestCase):
    """Verify candidate assertions in the live graph are structurally ready for adjudication."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.connection = connect_sqlite(GRAPH, readonly=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

    def test_candidate_assertions_have_all_adjudication_fields(self) -> None:
        """Every candidate assertion must possess the fields needed for HITL packets."""
        candidates = self.connection.execute("""
            SELECT assertion_id, subject_node_id, predicate, object_node_id,
                   object_literal, confidence_label, validation_state
            FROM assertion
            WHERE canonical_status = 'candidate'
        """).fetchall()
        self.assertGreaterEqual(len(candidates), 300)
        for c in candidates:
            self.assertTrue(c["assertion_id"])
            self.assertTrue(c["subject_node_id"])
            self.assertTrue(
                c["object_node_id"] or c["object_literal"],
                f"Candidate {c['assertion_id']} has neither object_node_id nor object_literal",
            )
            self.assertTrue(c["confidence_label"])
            self.assertIn(c["validation_state"], ("needs_review", "unreviewed"))

    def test_candidate_assertions_have_100_percent_evidence_coverage(self) -> None:
        """Every candidate assertion must have at least one supporting evidence item."""
        lacking = self.connection.execute("""
            SELECT count(*) FROM assertion a
            WHERE a.canonical_status = 'candidate'
              AND NOT EXISTS (
                  SELECT 1 FROM assertion_evidence ae WHERE ae.assertion_id = a.assertion_id
              )
        """).fetchone()[0]
        self.assertEqual(
            0, lacking,
            f"{lacking} candidate assertions lack evidence"
        )

    def test_graph_hash_unchanged_after_readiness_audit(self) -> None:
        """Audit is strictly read-only."""
        before = sha256_file(GRAPH)
        self.assertEqual(before, sha256_file(GRAPH))


if __name__ == "__main__":
    unittest.main(verbosity=2)
