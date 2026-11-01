"""Wave 3 Characterization & Regression Suite: Unified Query & Decision Orchestration.

Validates:
1. Architectural modularization and 400-line module budget enforcement.
2. Governed QueryEngine / RetrievalService delegation without raw SQLite bypasses.
3. Provider-free L1 routing boundaries and intent classification.
4. DecisionService and LazyDecisionService response envelope contracts.
5. Error resilience in graph topology hydration (no NameError on sqlite3 errors).
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

from dual_pipeline.core import sha256_file, write_json
from dual_pipeline.decision_service import DecisionService, LazyDecisionService
from dual_pipeline.query_router import QueryDestination, route_query
from dual_pipeline.retrieval_service import RetrievalService


class StubEngine:
    def __init__(self, runtime: Path) -> None:
        self.runtime = runtime
        self.calls: list[dict[str, Any]] = []

    def ask(self, question: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"question": question, **kwargs})
        run_id = "run-wave3-test"
        run_dir = self.runtime / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        write_json(
            run_dir / "evidence_packet.json",
            {
                "approved": [
                    {
                        "record": {
                            "record_id": "rec-1",
                            "title": "Alpha Option Evidence",
                            "retrieval_text": "Alpha delivers superior scalability.",
                            "record_type": "assertion",
                            "evidence_ids": ["ev-1"],
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
        )
        return {
            "status": "COMPLETED",
            "run_id": run_id,
            "generation_id": "gen-wave3",
            "graph_snapshot": "snapshot-wave3",
            "graph_sha256_before": "0" * 64,
            "graph_sha256_after": "0" * 64,
            "answer_delivery": "grounded_synthesis",
            "synthesis_status": "completed",
            "total_external_model_call_count": 1,
            "x3_outcome": "X3D_ALLOW_FINISH",
            "retrieval_evidence": {
                "terminal_coverage_disposition": "COMPLETE",
                "coverage_counts": {"supported": 1},
            },
            "answer": {"opportunities": []},
        }


class TestWave3QueryOrchestration(unittest.TestCase):
    """Regression tests for Wave 3 Query & Decision Orchestration."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.runtime_root = Path(self.temp_dir.name) / "runtime"
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.engine = StubEngine(self.runtime_root)
        self.service = DecisionService(
            query_engine=self.engine,
            runtime_root=self.runtime_root,
        )

    def test_module_budget_under_400_lines(self) -> None:
        """Verify all Wave 3 modified and created modules adhere to the 400-line budget."""
        repo_root = Path(__file__).resolve().parent.parent.parent
        target_files = [
            "dual_pipeline/decision_service.py",
            "dual_pipeline/decision_service_core/__init__.py",
            "dual_pipeline/decision_service_core/service.py",
            "dual_pipeline/decision_service_core/envelope.py",
            "dual_pipeline/decision_service_core/handlers.py",
            "dual_pipeline/decision_service_core/lazy_service.py",
            "dual_pipeline/decision_service_core/programmatic.py",
            "dual_pipeline/query_router_core/router.py",
            "dual_pipeline/query_router_core/intent.py",
            "dual_pipeline/retrieval_service.py",
        ]
        for rel_path in target_files:
            file_path = repo_root / rel_path
            self.assertTrue(file_path.is_file(), f"Expected file {rel_path} to exist")
            line_count = len(file_path.read_text(encoding="utf-8").splitlines())
            self.assertLessEqual(
                line_count,
                400,
                f"Module {rel_path} has {line_count} lines, exceeding 400-line budget!",
            )

    def test_topology_missing_active_pair(self) -> None:
        """RetrievalService safely returns (None, None) when active_pair is absent."""
        retrieval_service = RetrievalService(
            query_engine=self.engine,
            runtime_root=self.runtime_root,
        )
        topology, active_pair = retrieval_service.get_topology()
        self.assertIsNone(topology)
        self.assertIsNone(active_pair)

    def test_topology_error_resilience(self) -> None:
        """Corrupt graph database does not crash with NameError: sqlite3."""
        corrupt_graph = self.runtime_root / "corrupt_graph.sqlite"
        corrupt_graph.write_text("NOT_A_VALID_SQLITE_FILE", encoding="utf-8")
        write_json(
            self.runtime_root / "active_pair.json",
            {
                "generation_id": "gen-test",
                "graph_path": "corrupt_graph.sqlite",
                "graph_sha256": "0" * 64,
            },
        )
        retrieval_service = RetrievalService(
            query_engine=self.engine,
            runtime_root=self.runtime_root,
        )
        topology, active_pair = retrieval_service.get_topology()
        self.assertIsNone(topology)
        self.assertIsNone(active_pair)

    def test_topology_caching_and_delegation(self) -> None:
        """Valid SQLite graph is loaded once, cached, and returned by DecisionService."""
        graph_path = self.runtime_root / "graph.sqlite"
        conn = sqlite3.connect(graph_path)
        cur = conn.cursor()
        cur.execute(
            """CREATE TABLE node (
                node_id TEXT PRIMARY KEY, canonical_name TEXT, display_name TEXT,
                node_type TEXT, description TEXT, status TEXT, active INTEGER
            )"""
        )
        cur.execute(
            """CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY, subject_node_id TEXT, predicate TEXT,
                object_node_id TEXT, object_literal TEXT, assertion_type TEXT DEFAULT 'fact',
                atomicity_state TEXT DEFAULT 'atomic', canonical_status TEXT DEFAULT 'approved',
                retrieval_eligible INTEGER DEFAULT 1, materiality TEXT DEFAULT 'high',
                classification TEXT DEFAULT 'internal',
                allowed_audiences_json TEXT DEFAULT '["partner_advisory_consulting"]',
                hitl_required INTEGER DEFAULT 0, origin_type TEXT DEFAULT 'legacy_migration'
            )"""
        )
        cur.execute(
            """CREATE TABLE assertion_evidence (
                assertion_id TEXT, evidence_id TEXT, evidence_relationship TEXT
            )"""
        )
        cur.execute(
            """CREATE VIEW v_canonical_edge AS
            SELECT assertion_id, subject_node_id AS from_node_id, 'RELATES_TO' AS relationship_family,
                   predicate AS edge_type, object_node_id AS to_node_id, materiality,
                   'src' AS source_id, hitl_required
              FROM assertion WHERE object_node_id IS NOT NULL"""
        )
        cur.execute("INSERT INTO node VALUES ('n:alpha', 'Alpha', 'Alpha', 'opt', '', 'active', 1)")
        cur.execute("INSERT INTO node VALUES ('n:impact', 'Impact', 'Impact', 'crit', '', 'active', 1)")
        cur.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id) "
            "VALUES ('a:1', 'n:alpha', 'IMPROVES', 'n:impact')"
        )
        cur.execute("INSERT INTO assertion_evidence VALUES ('a:1', 'ev-1', 'supports')")
        conn.commit()
        conn.close()

        g_sha = sha256_file(graph_path)
        write_json(
            self.runtime_root / "active_pair.json",
            {
                "generation_id": "gen-test-pair",
                "graph_path": "graph.sqlite",
                "graph_sha256": g_sha,
            },
        )

        top1, pair1 = self.service._get_topology()
        self.assertIsNotNone(top1)
        self.assertEqual(g_sha, top1.graph_sha256)
        self.assertEqual("gen-test-pair", pair1.get("generation_id"))

        # Cached check
        top2, pair2 = self.service._get_topology()
        self.assertIs(top1, top2)

    def test_routing_provider_free_boundaries(self) -> None:
        """Route query respects provider-free boundaries and classifies intents."""
        # General writing -> general assistant handoff
        decision = route_query("Write an email summarizing these notes")
        self.assertEqual(QueryDestination.GENERAL_ASSISTANT_HANDOFF, decision.destination)
        self.assertFalse(decision.enters_query_runtime)

        # Operational workflow -> workflow handoff
        decision = route_query("Publish the current branch to the remote.")
        self.assertEqual(QueryDestination.OPERATIONAL_WORKFLOW_HANDOFF, decision.destination)
        self.assertFalse(decision.enters_query_runtime)

        # Ambiguous conflict -> clarification required
        decision = route_query("What should we do?")
        self.assertEqual(QueryDestination.ROUTE_AMBIGUOUS, decision.destination)

    def test_lazy_decision_service_deferred_factory(self) -> None:
        """LazyDecisionService does not call factory when query does not enter runtime."""
        factory_called = [False]

        def factory() -> DecisionService:
            factory_called[0] = True
            return self.service

        lazy = LazyDecisionService(
            runtime_root=self.runtime_root,
            runtime_service_factory=factory,
        )

        # General query does not invoke factory
        resp = lazy.query("Write an email summarizing these notes")
        self.assertEqual("HANDOFF_READY", resp["terminal_state"])
        self.assertFalse(factory_called[0])

        # Graph decision query invokes factory
        resp2 = lazy.query(
            "Compare NEE evidence across options and recommend which to choose",
            options=["Alpha", "Beta"],
            criteria=["impact", "cost"],
        )
        self.assertTrue(factory_called[0])
        self.assertIn(
            resp2["terminal_state"],
            {"DECISION_PRODUCED", "EVIDENCE_READY_DECISION_NOT_PRODUCED", "NO_DECISION"},
        )

    def test_programmatic_decision_evaluation_plan(self) -> None:
        """evaluate_programmatic_decision runs PDME dialectical matrix."""
        res = self.service.evaluate_programmatic_decision(
            decision_question="Which pipeline architecture should be chosen?",
            options=[
                {"option_id": "opt-a", "title": "Stream Pipeline", "description": "Low latency"},
                {"option_id": "opt-b", "title": "Batch Pipeline", "description": "High throughput"},
            ],
            criteria=[
                {"name": "latency", "weight": 0.6, "description": "Response time"},
                {"name": "cost", "weight": 0.4, "description": "Compute cost"},
            ],
            evidence_pack={"facts": []},
            margin_threshold=0.25,
        )
        self.assertIsInstance(res, dict)
        self.assertIn("top_option_id", res)
        self.assertIn("option_rankings", res)


if __name__ == "__main__":
    unittest.main()
