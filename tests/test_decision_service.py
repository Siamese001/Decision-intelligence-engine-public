from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from dual_pipeline.core import write_json
from dual_pipeline.decision_service import DecisionService


def _packet() -> dict[str, object]:
    def row(record_id: str, title: str, text: str, evidence_id: str) -> dict[str, object]:
        return {
            "record": {
                "record_id": record_id,
                "title": title,
                "retrieval_text": text,
                "record_type": "assertion",
                "evidence_ids": [evidence_id],
                "currentness_state": "current",
            },
            "retrieval_metadata": {"answer_use": "factual_answer"},
        }

    return {
        "approved": [
            row("alpha-record", "Alpha", "Alpha improves impact and cost", "evidence-alpha"),
            row("beta-record", "Beta", "Beta improves impact", "evidence-beta"),
        ],
        "candidate": [
            {
                "record": {
                    "record_id": "candidate-record",
                    "title": "Nightly evaluation candidate",
                    "retrieval_text": "Sherpa evaluation is running nightly.",
                    "record_type": "assertion",
                    "evidence_ids": ["candidate-evidence"],
                    "currentness_state": "current",
                },
                "retrieval_metadata": {"answer_use": "candidate_hypothesis"},
            }
        ],
        "external_public": [],
        "graph_paths": [],
        "counterevidence": [],
        "gaps": [],
    }


class StubQueryEngine:
    def __init__(self, runtime: Path) -> None:
        self.runtime = runtime
        self.calls: list[dict[str, Any]] = []
        self.provider_failure = False

    def ask(self, question: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"question": question, **kwargs})
        run_id = "run-test-decision"
        run_dir = self.runtime / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        write_json(run_dir / "evidence_packet.json", _packet())
        result = {
            "status": "COMPLETED",
            "run_id": run_id,
            "generation_id": "generation-1",
            "graph_snapshot": "snapshot-1",
            "graph_sha256_before": "a" * 64,
            "graph_sha256_after": "a" * 64,
            "embedding_backend": "test",
            "bm25_executed": True,
            "dense_executed": True,
            "sparse_executed": True,
            "colbert_executed": True,
            "graph_traversal_executed": True,
            "answer_delivery": "grounded_synthesis",
            "synthesis_status": "completed",
            "total_external_model_call_count": 1,
            "x3_outcome": "X3D_ALLOW_FINISH",
            "retrieval_evidence": {
                "terminal_coverage_disposition": "COMPLETE",
                "coverage_counts": {"supported": 2},
            },
            "answer": {"opportunities": []},
        }
        if self.provider_failure:
            result.update(
                {
                    "status": "BLOCKED",
                    "x3_outcome": "X3B_ESCALATE_HITL",
                    "l2_disposition": "FAILED",
                    "l2_reason_code": "USER_REQUESTED_RECOMMENDATION",
                    "provider_failure_code": "PROVIDER_UNAVAILABLE",
                }
            )
        return result


class DecisionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime = Path(self.temp.name) / "runtime"
        self.engine = StubQueryEngine(self.runtime)
        self.service = DecisionService(query_engine=self.engine, runtime_root=self.runtime)

    def test_missing_criteria_clarifies_before_retrieval(self) -> None:
        result = self.service.query(
            "Compare NEE evidence across options and recommend which to choose"
        )
        self.assertEqual("CLARIFICATION_REQUIRED", result["terminal_state"])
        self.assertEqual(0, result["retrieval_call_count"])
        self.assertEqual([], self.engine.calls)

    def test_decision_path_uses_query_engine_once_and_persists_result(self) -> None:
        result = self.service.query(
            "Compare NEE evidence across options and recommend which to choose",
            options=["Alpha", "Beta"],
            criteria=["impact", "cost"],
        )
        self.assertEqual("DECISION_PRODUCED", result["terminal_state"])
        self.assertEqual("Alpha", result["result"]["recommended_option"]["label"])
        self.assertEqual(1, result["retrieval_call_count"])
        self.assertEqual(1, result["provider_call_count"])
        self.assertEqual(1, len(self.engine.calls))
        run_dir = self.runtime / "runs" / "run-test-decision"
        decision_dir = run_dir / "decision_results" / result["request_id"]
        self.assertTrue((decision_dir / "decision_result.json").is_file())
        self.assertTrue((decision_dir / "decision_result.md").is_file())

    def test_distinct_decision_frames_do_not_overwrite_a_shared_query_run(self) -> None:
        first = self.service.query(
            "Compare NEE evidence across options and recommend which to choose",
            options=["Alpha", "Beta"],
            criteria=["impact", "cost"],
            constraints=["Budget A"],
        )
        second = self.service.query(
            "Compare NEE evidence across options and recommend which to choose",
            options=["Alpha", "Beta"],
            criteria=["impact", "cost"],
            constraints=["Budget B"],
        )
        self.assertNotEqual(first["request_id"], second["request_id"])
        run_dir = self.runtime / "runs" / "run-test-decision" / "decision_results"
        self.assertTrue((run_dir / first["request_id"] / "decision_result.json").is_file())
        self.assertTrue((run_dir / second["request_id"] / "decision_result.json").is_file())

    def test_candidate_review_keeps_a_candidate_without_mutation(self) -> None:
        result = self.service.query(
            "Should the candidate be promoted or kept?",
            options=["Promote the candidate", "Keep the candidate"],
            criteria=["source-bound evidence"],
            query_type="candidate_promotion_review",
        )
        self.assertEqual("DECISION_PRODUCED", result["terminal_state"])
        self.assertEqual("Keep the candidate", result["result"]["recommended_option"]["label"])
        self.assertEqual([], result["result"]["recommended_option"]["evidence_ids"])
        self.assertIn("candidate-evidence", result["citations"])
        self.assertEqual("a" * 64, result["result"]["execution"]["graph_sha256_before"])
        self.assertEqual("a" * 64, result["result"]["execution"]["graph_sha256_after"])

    def test_general_route_returns_typed_handoff_without_query_runtime(self) -> None:
        result = self.service.query("Write an email summarizing these notes")
        self.assertEqual("HANDOFF_READY", result["terminal_state"])
        self.assertEqual("GeneralHandler", result["handler"])
        self.assertEqual(0, result["retrieval_call_count"])
        self.assertEqual([], self.engine.calls)

    def test_provider_failure_is_preserved_in_typed_non_success_result(self) -> None:
        self.engine.provider_failure = True
        result = self.service.query(
            "Compare NEE evidence across options and recommend which to choose",
            options=["Alpha", "Beta"],
            criteria=["impact", "cost"],
        )
        self.assertEqual(
            "EVIDENCE_READY_DECISION_NOT_PRODUCED", result["terminal_state"]
        )
        self.assertEqual("PROVIDER_UNAVAILABLE", result["error"]["reason_code"])
        self.assertEqual(
            "PROVIDER_UNAVAILABLE",
            result["result"]["execution"]["provider_failure_code"],
        )
        self.assertIn("PROVIDER_UNAVAILABLE", result["result"]["recommendation_rationale"])

    def test_ambiguous_route_returns_specific_clarification(self) -> None:
        result = self.service.query("What should we do?")
        self.assertEqual("CLARIFICATION_REQUIRED", result["terminal_state"])
        self.assertEqual("ClarificationHandler", result["handler"])
        self.assertIn("direct graph-evidence lookup", result["result"]["clarification_question"])

    def test_decision_query_emits_sealed_witness_subgraph(self) -> None:
        import sqlite3
        self.runtime.mkdir(parents=True, exist_ok=True)
        graph_path = self.runtime / "graph.sqlite"
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
            """INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id)
               VALUES ('a:1', 'n:alpha', 'IMPROVES', 'n:impact')"""
        )
        cur.execute("INSERT INTO assertion_evidence VALUES ('a:1', 'evidence-alpha', 'supports')")
        conn.commit()
        conn.close()

        from dual_pipeline.core import sha256_file
        g_sha = sha256_file(graph_path)
        write_json(
            self.runtime / "active_pair.json",
            {
                "generation_id": "gen-test-pair",
                "graph_path": "graph.sqlite",
                "graph_sha256": g_sha,
                "sidecar_path": "sidecar.sqlite",
                "sidecar_sha256": "0" * 64,
            },
        )

        result = self.service.query(
            "Compare NEE evidence across options and recommend which to choose",
            options=["Alpha", "Beta"],
            criteria=["impact", "cost"],
        )
        self.assertEqual("DECISION_PRODUCED", result["terminal_state"])
        witness = result["result"]["extensions"].get("com.partner_advisory.nee.decision-witness-subgraph")
        self.assertIsNotNone(witness)
        self.assertEqual("urn:nee:dual-pipeline:decision-witness-subgraph:1.0.0", witness["contract_id"])
        self.assertEqual(g_sha, witness["graph_sha256"])

        # Check that witness artifact file was written to run dir
        run_id = result["run_id"]
        witness_file = self.runtime / "runs" / run_id / "decision_results" / result["request_id"] / "decision_witness_subgraph.json"
        self.assertTrue(witness_file.is_file())


if __name__ == "__main__":
    unittest.main()
