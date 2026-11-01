"""
tests/test_ptc_hybrid_query.py

Unit tests for Programmatic Hybrid Graph Query Execution (PHGQE) in Pipeline C.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.ptc.contracts import (
    PTCExecutionContext,
    PTCStatus,
    TokenBudget,
)
from dual_pipeline.ptc.sandbox import PTCSandbox
from pipeline_c_retrieval.contracts import (
    CLIENT_SAFE_NAMESPACE,
    RetrievedFact,
)
from pipeline_c_retrieval.engine import PolicyGatedRetrievalEngine


class TestPTCHybridQuery(unittest.TestCase):

    def setUp(self) -> None:
        self.mock_db = MagicMock()
        self.engine = PolicyGatedRetrievalEngine(self.mock_db)

    def test_evidence_pack_assembly_and_compact_size(self) -> None:
        mock_facts = [
            RetrievedFact(
                node_id="node_nexus_01",
                evidence_id="ev_001",
                namespace=CLIENT_SAFE_NAMESPACE,
                type="system",
                name="Project Nexus Core",
                snippet="Architectural foundation for decision intelligence engine.",
                strategic_value=0.95,
                confidence=0.90,
                tacticality=0.10,
                is_quarantined=False,
                relevance_score=0.88,
                properties={"context": "Core engine"},
                connected_edges=[
                    {"from_node_id": "node_nexus_01", "to_node_id": "node_maximo_02", "relationship": "depends_on"}
                ],
            ),
            RetrievedFact(
                node_id="node_maximo_02",
                evidence_id="ev_002",
                namespace=CLIENT_SAFE_NAMESPACE,
                type="system",
                name="IBM Maximo",
                snippet="Work order management system.",
                strategic_value=0.80,
                confidence=0.85,
                tacticality=0.20,
                is_quarantined=False,
                relevance_score=0.75,
                properties={"context": "Asset tracking"},
                connected_edges=[],
            ),
        ]

        self.engine.search = MagicMock(return_value=mock_facts)

        result_dict = self.engine.execute_programmatic_query(
            query="Nexus Maximo integration",
            stealth_mode=True,
            limit=5,
        )

        self.assertEqual(result_dict["query"], "Nexus Maximo integration")
        self.assertEqual(len(result_dict["top_facts"]), 2)
        self.assertEqual(result_dict["top_facts"][0]["citation"], "[1]")
        self.assertEqual(result_dict["citation_index"]["[1]"]["node_id"], "node_nexus_01")
        self.assertEqual(len(result_dict["subgraph_edges"]), 1)
        self.assertLess(result_dict["payload_bytes"], 2048)

    def test_sandbox_executes_programmatic_retrieval(self) -> None:
        mock_facts = [
            RetrievedFact(
                node_id="node_infra_01",
                evidence_id="ev_100",
                namespace=CLIENT_SAFE_NAMESPACE,
                type="system",
                name="Kubernetes Ingress",
                snippet="Edge routing proxy.",
                strategic_value=0.85,
                confidence=0.90,
                tacticality=0.15,
                is_quarantined=False,
                relevance_score=0.92,
                properties={},
                connected_edges=[],
            )
        ]
        self.engine.search = MagicMock(return_value=mock_facts)

        class RetrievalProxy:
            def __init__(self, engine):
                self.engine = engine
            def query(self, text: str):
                return self.engine.execute_programmatic_query(query=text)

        script = """
def run(retrieval):
    pack = retrieval.query("Kubernetes")
    top_node = pack["top_facts"][0]["name"]
    citation = pack["top_facts"][0]["citation"]
    return {"top_system": top_node, "citation": citation, "matches": pack["total_matches"]}
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ctx = PTCExecutionContext(
                task_id="task_retrieval_test",
                runtime_root=Path(tmp_dir),
                timeout_seconds=5.0,
                token_budget=TokenBudget(max_payload_bytes=2048),
                proxies={"retrieval": RetrievalProxy(self.engine)},
            )
            sandbox = PTCSandbox()
            summary = sandbox.execute(script, ctx)

        self.assertEqual(summary.status, PTCStatus.SUCCESS)
        self.assertEqual(summary.data["top_system"], "Kubernetes Ingress")
        self.assertEqual(summary.data["citation"], "[1]")
        self.assertEqual(summary.data["matches"], 1)


if __name__ == "__main__":
    unittest.main()
