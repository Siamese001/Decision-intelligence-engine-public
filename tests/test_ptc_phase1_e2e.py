"""
tests/test_ptc_phase1_e2e.py

End-to-end integration test suite for Programmatic Tool Calling (PTC) Phase 1:
- Pipeline C Hybrid Retrieval (PHGQE) with Law 6 policy pre-filtering
- Universal Write Gateway (UWG) 11-Gate Benchmark Harness (PGBH)
- AST-governed sandboxed execution and TokenBudget disk spooling
- Token optimization telemetry recording
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.ptc.benchmark_harness import (
    PTC11GateBenchmarkHarness,
    PTC11GateReleasePlan,
)
from dual_pipeline.ptc.contracts import (
    PTCExecutionContext,
    PTCStatus,
    TokenBudget,
)
from dual_pipeline.ptc.sandbox import PTCSandbox
from dual_pipeline.ptc.spooler import PTCOutputSpooler
from dual_pipeline.token_optimization.agent import TokenOptimizationAgent
from pipeline_c_retrieval.contracts import (
    CLIENT_SAFE_NAMESPACE,
    RetrievedFact,
)
from pipeline_c_retrieval.engine import PolicyGatedRetrievalEngine


class TestPTCPhase1EndToEnd(unittest.TestCase):

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.runtime_root = Path(self.temp_dir.name)
        self.spool_dir = self.runtime_root / "spool"
        self.spooler = PTCOutputSpooler(spool_dir=self.spool_dir)
        self.sandbox = PTCSandbox(spooler=self.spooler)
        self.token_agent = TokenOptimizationAgent()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_e2e_ptc_pipeline_c_retrieval_and_spooling(self) -> None:
        # Mock database with canonical and advisory facts
        mock_db = MagicMock()
        engine = PolicyGatedRetrievalEngine(mock_db)

        # Build mock facts
        mock_facts = [
            RetrievedFact(
                node_id=f"node_{i:02d}",
                evidence_id=f"ev_{i:03d}",
                namespace=CLIENT_SAFE_NAMESPACE,
                type="system",
                name=f"Enterprise Service {i}",
                snippet=f"Critical architecture component {i} handling event streaming.",
                strategic_value=0.85,
                confidence=0.90,
                tacticality=0.10,
                is_quarantined=False,
                relevance_score=0.90 - (i * 0.05),
                properties={"context": f"Service context {i}"},
                connected_edges=[
                    {"from_node_id": f"node_{i:02d}", "to_node_id": "node_core", "relationship": "integrates_with"}
                ],
            )
            for i in range(1, 6)
        ]
        engine.search = MagicMock(return_value=mock_facts)

        class RetrievalProxy:
            def __init__(self, eng):
                self.eng = eng
            def search_hybrid(self, query: str):
                return self.eng.execute_programmatic_query(query=query, limit=5)

        script = """
def run(retrieval):
    pack = retrieval.search_hybrid("architecture streaming")
    top_services = [f["name"] for f in pack["top_facts"]]
    edges = pack["subgraph_edges"]
    return {
        "status": "PROCESSED",
        "top_services": top_services,
        "edge_count": len(edges),
        "total_matches": pack["total_matches"]
    }
"""
        ctx = PTCExecutionContext(
            task_id="task_e2e_retrieval",
            runtime_root=self.runtime_root,
            timeout_seconds=5.0,
            token_budget=TokenBudget(max_payload_bytes=1024),
            proxies={"retrieval": RetrievalProxy(engine)},
        )

        result = self.sandbox.execute(script, ctx)
        self.assertEqual(result.status, PTCStatus.SUCCESS)
        self.assertEqual(result.data["status"], "PROCESSED")
        self.assertEqual(len(result.data["top_services"]), 5)
        self.assertEqual(result.data["edge_count"], 5)

        # Telemetry recording: Compare baseline (~20KB raw dump) vs PTC pack (<1.5KB)
        telemetry = self.token_agent.record_ptc_execution(
            task_id="task_e2e_retrieval",
            baseline_bytes=20_480,
            spooled_summary_bytes=result.payload_bytes,
            execution_time_ms=result.execution_time_ms,
        )
        self.assertGreater(telemetry["savings_ratio"], 0.85)

    @patch("dual_pipeline.release.ReleaseManager")
    @patch("dual_pipeline.retrieval_release.RetrievalReleaseEvaluator")
    @patch("dual_pipeline.index.get_backend")
    def test_e2e_ptc_11gate_benchmark_and_receipt_sealing(
        self, mock_get_backend, mock_evaluator_cls, mock_release_mgr_cls
    ) -> None:
        mock_evaluator = MagicMock()
        mock_evaluator_cls.return_value = mock_evaluator

        mock_eval_data = {
            "prepared_receipt_id": "receipt_e2e_01",
            "gates": {
                "real_bge_m3": True,
                "pair_binding": True,
                "coverage_complete": True,
                "core_thresholds": True,
                "delta_thresholds": True,
                "critical_cases": True,
                "rank_stability": True,
                "repeat_stability": True,
                "forbidden_leakage": True,
                "latency_threshold": True,
                "component_execution": True,
            },
            "metrics": {
                "recall_at_k": 1.0,
                "mrr": 1.0,
                "ndcg_at_k": 1.0,
                "max_latency_ms": 135.0,
                "leakage_violations": 0,
            },
        }
        mock_evaluator.evaluate.return_value = {
            "evaluation": mock_eval_data,
            "evaluation_path": str(self.runtime_root / "sealed_eval.json"),
        }

        class BenchmarkProxy:
            def __init__(self, root):
                self.harness = PTC11GateBenchmarkHarness()
                self.root = root

            def run_11gate_benchmark(self):
                plan = PTC11GateReleasePlan(
                    runtime_root=self.root,
                    receipt_path=self.root / "receipt.json",
                    suite_path=self.root / "suite.json",
                )
                receipt = self.harness.evaluate_release(plan)
                return receipt.to_dict()

        script = """
def run(benchmark):
    receipt = benchmark.run_11gate_benchmark()
    return {
        "status": receipt["status"],
        "qualified": receipt["all_11_gates_passed"],
        "digest": receipt["receipt_digest"],
        "latency_ms": receipt["metrics_summary"]["max_latency_ms"]
    }
"""
        ctx = PTCExecutionContext(
            task_id="task_e2e_benchmark",
            runtime_root=self.runtime_root,
            timeout_seconds=10.0,
            token_budget=TokenBudget(max_payload_bytes=1024),
            proxies={"benchmark": BenchmarkProxy(self.runtime_root)},
        )

        result = self.sandbox.execute(script, ctx)
        self.assertEqual(result.status, PTCStatus.SUCCESS)
        self.assertEqual(result.data["status"], "DELIVERY_READY")
        self.assertTrue(result.data["qualified"])
        self.assertTrue(result.data["digest"].startswith("sha256:"))
        self.assertLessEqual(result.data["latency_ms"], 7500.0)


if __name__ == "__main__":
    unittest.main()
