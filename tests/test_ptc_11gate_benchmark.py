"""
tests/test_ptc_11gate_benchmark.py

Unit tests for Programmatic 11-Gate Benchmark Harness (PGBH) in UWG / Release Qualification.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.ptc.benchmark_harness import (
    PTC11GateBenchmarkHarness,
    PTC11GateReceipt,
    PTC11GateReleasePlan,
)
from dual_pipeline.ptc.contracts import (
    PTCExecutionContext,
    PTCStatus,
    TokenBudget,
)
from dual_pipeline.ptc.sandbox import PTCSandbox


class TestPTC11GateBenchmark(unittest.TestCase):

    def test_receipt_compact_serialization(self) -> None:
        gates = {
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
        }
        receipt = PTC11GateReceipt(
            receipt_id="rec_release_001",
            status="DELIVERY_READY",
            all_11_gates_passed=True,
            gate_results=gates,
            metrics_summary={"recall_at_10": 1.0, "mrr": 1.0, "ndcg_at_10": 1.0},
            receipt_digest="sha256:abcd1234efgh5678",
            evaluation_path="/tmp/eval.json",
        )
        compact_str = receipt.to_compact_json()
        self.assertLess(len(compact_str.encode("utf-8")), 600)
        self.assertIn("DELIVERY_READY", compact_str)
        self.assertTrue(receipt.all_11_gates_passed)

    @patch("dual_pipeline.release.ReleaseManager")
    @patch("dual_pipeline.retrieval_release.RetrievalReleaseEvaluator")
    @patch("dual_pipeline.index.get_backend")
    def test_harness_evaluates_release_pass(
        self, mock_get_backend, mock_evaluator_cls, mock_release_mgr_cls
    ) -> None:
        mock_evaluator = MagicMock()
        mock_evaluator_cls.return_value = mock_evaluator

        mock_eval_data = {
            "prepared_receipt_id": "receipt_test_01",
            "gates": {f"gate_{i}": True for i in range(1, 12)},
            "metrics": {"recall_at_k": 1.0, "mrr": 1.0, "ndcg_at_k": 1.0, "max_latency_ms": 120.0},
        }
        mock_evaluator.evaluate.return_value = {
            "evaluation": mock_eval_data,
            "evaluation_path": "/tmp/eval_output.json",
        }

        harness = PTC11GateBenchmarkHarness()
        plan = PTC11GateReleasePlan(
            runtime_root=Path("/tmp"),
            receipt_path=Path("/tmp/receipt.json"),
            suite_path=Path("/tmp/suite.json"),
        )

        receipt = harness.evaluate_release(plan)
        self.assertEqual(receipt.status, "DELIVERY_READY")
        self.assertTrue(receipt.all_11_gates_passed)
        self.assertEqual(receipt.metrics_summary["recall_at_10"], 1.0)
        self.assertTrue(receipt.receipt_digest.startswith("sha256:"))

    @patch("dual_pipeline.release.ReleaseManager")
    @patch("dual_pipeline.retrieval_release.RetrievalReleaseEvaluator")
    @patch("dual_pipeline.index.get_backend")
    def test_harness_evaluates_release_fail_closed(
        self, mock_get_backend, mock_evaluator_cls, mock_release_mgr_cls
    ) -> None:
        mock_evaluator = MagicMock()
        mock_evaluator_cls.return_value = mock_evaluator

        gates = {f"gate_{i}": True for i in range(1, 12)}
        gates["gate_4"] = False  # Core thresholds failed

        mock_eval_data = {
            "prepared_receipt_id": "receipt_fail_01",
            "gates": gates,
            "metrics": {"recall_at_k": 0.85, "mrr": 0.90},
        }
        mock_evaluator.evaluate.return_value = {
            "evaluation": mock_eval_data,
            "evaluation_path": "/tmp/eval_fail.json",
        }

        harness = PTC11GateBenchmarkHarness()
        plan = PTC11GateReleasePlan(
            runtime_root=Path("/tmp"),
            receipt_path=Path("/tmp/receipt.json"),
            suite_path=Path("/tmp/suite.json"),
        )

        receipt = harness.evaluate_release(plan)
        self.assertEqual(receipt.status, "BLOCKED")
        self.assertFalse(receipt.all_11_gates_passed)

    def test_sandbox_invokes_11gate_harness(self) -> None:
        class BenchmarkProxy:
            def run_evaluation(self):
                return {
                    "receipt_id": "rec_001",
                    "status": "DELIVERY_READY",
                    "all_11_gates_passed": True,
                    "receipt_digest": "sha256:1234567890abcdef",
                }

        script = """
def run(benchmark):
    receipt = benchmark.run_evaluation()
    return {
        "status": receipt["status"],
        "qualified": receipt["all_11_gates_passed"],
        "digest": receipt["receipt_digest"]
    }
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ctx = PTCExecutionContext(
                task_id="task_benchmark_sandbox",
                runtime_root=Path(tmp_dir),
                timeout_seconds=5.0,
                token_budget=TokenBudget(max_payload_bytes=2048),
                proxies={"benchmark": BenchmarkProxy()},
            )
            sandbox = PTCSandbox()
            summary = sandbox.execute(script, ctx)

        self.assertEqual(summary.status, PTCStatus.SUCCESS)
        self.assertEqual(summary.data["status"], "DELIVERY_READY")
        self.assertTrue(summary.data["qualified"])
        self.assertEqual(summary.data["digest"], "sha256:1234567890abcdef")


if __name__ == "__main__":
    unittest.main()
