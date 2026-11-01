"""
tests/characterization/test_wave6_convergence.py

Wave 6 final convergence and release promotion characterization tests:
- Verifies existence and validity of all wave receipts (Waves 0–5)
- Asserts strict module line budget compliance (<= 400 lines) across all refactored modules
- Validates end-to-end multi-wave integration across Ingestion, Query, Release, and Telemetry
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from dual_pipeline.core import write_json
from dual_pipeline.decision_service import DecisionService
from dual_pipeline.llm_judge_core.orchestrator import DecomposedMultiJudge
from dual_pipeline.release_policy import (
    evaluate_retrieval_11_gates,
)
from dual_pipeline.telemetry import RunRecorder


class _StubQueryEngine:
    def __init__(self, runtime: Path) -> None:
        self.runtime = runtime
        self.calls: list[dict[str, Any]] = []

    def ask(self, question: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"question": question, **kwargs})
        run_id = "run-test-wave6-decision"
        run_dir = self.runtime / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        packet = {
            "approved": [
                {
                    "evidence_id": "ev-01",
                    "content": "Alpha reduces customer costs by 15%",
                    "source_id": "src-01",
                    "record": {
                        "record_id": "rec-alpha",
                        "title": "Alpha Option",
                        "retrieval_text": "Alpha improves impact and cost",
                        "record_type": "assertion",
                        "evidence_ids": ["ev-01"],
                        "currentness_state": "current",
                    },
                    "retrieval_metadata": {"answer_use": "factual_answer"},
                },
                {
                    "evidence_id": "ev-02",
                    "content": "Beta optimizes operational impact",
                    "source_id": "src-02",
                    "record": {
                        "record_id": "rec-beta",
                        "title": "Beta Option",
                        "retrieval_text": "Beta improves impact",
                        "record_type": "assertion",
                        "evidence_ids": ["ev-02"],
                        "currentness_state": "current",
                    },
                    "retrieval_metadata": {"answer_use": "factual_answer"},
                },
            ],
            "candidate": [],
            "external_public": [],
            "graph_paths": [],
            "counterevidence": [],
            "gaps": [],
        }
        write_json(run_dir / "evidence_packet.json", packet)
        return {
            "status": "COMPLETED",
            "run_id": run_id,
            "generation_id": "gen-01",
            "graph_snapshot": "snap-01",
            "graph_sha256_before": "a" * 64,
            "graph_sha256_after": "a" * 64,
            "embedding_backend": "test",
            "bm25_executed": True,
            "dense_executed": True,
            "sparse_executed": True,
            "colbert_executed": True,
            "graph_traversal_executed": True,
            "l2_invoked": False,
            "evidence_packet": packet,
            "retrieval_record_ids": ["rr-01"],
            "retrieval_record_count": 1,
            "l2_output": None,
        }


class TestWave6Convergence(unittest.TestCase):
    """Characterization tests certifying final multi-wave convergence and release readiness."""

    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parent.parent.parent

    def test_wave_receipts_integrity(self) -> None:
        """Verify receipts for Waves 0 through 5 exist and have terminal SUCCESS status."""
        required_receipts = [
            "reports/wave0_characterization_receipt.json",
            "reports/wave1_p0_correctness_receipt.json",
            "reports/wave2_ingestion_orchestration_receipt.json",
            "reports/wave3_query_orchestration_receipt.json",
            "reports/wave4_release_evaluation_receipt.json",
            "reports/wave5_observability_telemetry_receipt.json",
        ]
        for receipt_rel in required_receipts:
            receipt_path = self.repo_root / receipt_rel
            self.assertTrue(receipt_path.is_file(), f"Missing required wave receipt: {receipt_rel}")
            data = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(data.get("status"), "COMPLETE", f"Receipt {receipt_rel} status not COMPLETE")
            self.assertEqual(
                data.get("terminal_outcome"),
                "SUCCESS",
                f"Receipt {receipt_rel} terminal_outcome not SUCCESS",
            )
            self.assertIn("deliverable_digests", data, f"Receipt {receipt_rel} missing deliverable_digests")

    def test_core_refactored_modules_line_budgets(self) -> None:
        """Verify all refactored modules across all waves strictly comply with the 400-line budget."""
        target_modules = [
            # Wave 1 & 2 (Ingestion)
            "pipeline_b_extraction/agents/extraction_coordinator.py",
            "pipeline_a_intake/agents/ingestion_coordinator.py",
            "dual_pipeline/pipeline_runner.py",
            # Wave 3 (Query & Decision)
            "dual_pipeline/decision_service.py",
            "dual_pipeline/decision_service_core/service.py",
            "dual_pipeline/decision_service_core/handlers.py",
            "dual_pipeline/decision_service_core/envelope.py",
            "dual_pipeline/decision_service_core/lazy_service.py",
            "dual_pipeline/decision_service_core/programmatic.py",
            "dual_pipeline/retrieval_service.py",
            "dual_pipeline/query_router_core/router.py",
            "dual_pipeline/query_router_core/intent.py",
            # Wave 4 (Release & Evaluation)
            "dual_pipeline/release_policy.py",
            "dual_pipeline/release_thresholds.py",
            "dual_pipeline/transcript_qa_core/evaluator.py",
            "dual_pipeline/llm_judge_core/orchestrator.py",
            # Wave 5 (Observability & Telemetry)
            "dual_pipeline/telemetry.py",
            "dual_pipeline/otel_engine.py",
            "dual_pipeline/telemetry_core/constants.py",
            "dual_pipeline/telemetry_core/recorder.py",
            "dual_pipeline/telemetry_core/events.py",
            "dual_pipeline/telemetry_core/agent_trajectory.py",
        ]
        for rel_path in target_modules:
            abs_path = self.repo_root / rel_path
            self.assertTrue(abs_path.exists(), f"Module {rel_path} does not exist")
            line_count = len(abs_path.read_text(encoding="utf-8").splitlines())
            self.assertLessEqual(
                line_count,
                400,
                f"Module {rel_path} exceeds 400-line budget ({line_count} lines)",
            )

    def test_e2e_query_and_decision_convergence(self) -> None:
        """Verify DecisionService handles decision queries with valid terminal outcome."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = Path(tmpdir) / "runtime"
            engine_stub = _StubQueryEngine(runtime)
            decision_service = DecisionService(query_engine=engine_stub, runtime_root=runtime)

            result = decision_service.query(
                "Compare NEE evidence across options and recommend which to choose",
                options=["Alpha", "Beta"],
                criteria=["impact", "cost"],
            )
            self.assertIsInstance(result, dict)
            self.assertIn(
                result.get("terminal_state"),
                {"DECISION_PRODUCED", "EVIDENCE_READY_DECISION_NOT_PRODUCED", "NO_DECISION"},
            )
            self.assertIsNone(result.get("error"))

    def test_e2e_judge_evaluation_and_release_policy_convergence(self) -> None:
        """Verify LLM multi-judge evaluation and release policy validation."""
        judge = DecomposedMultiJudge(mock=True)
        eval_result = judge.evaluate_parallel(
            f1_path=None,
            f2_path=None,
            f3_path=None,
            source_path=None,
            output_1_text="Speaker 1: Confirmed.\nSpeaker 2: Agreed.",
            output_2_text="# Executive Recap\n\n## Decisions\n- Decision 1",
            output_3_text='{"nodes": [{"id": "decision:1"}], "edges": []}',
            raw_transcript="Speaker 1: Confirmed.\nSpeaker 2: Agreed.",
            metadata={"meeting_name": "Test", "date": "09-13-2026"},
        )
        self.assertIn("evaluation_receipt_digest", eval_result)
        self.assertEqual(len(eval_result["evaluation_receipt_digest"]), 64)

        # Validate release policy 11-gate check
        gates_11 = {
            "real_bge_m3": True,
            "pair_binding": True,
            "coverage_complete": True,
            "core_thresholds": True,
            "delta_thresholds": True,
            "critical_cases": True,
            "rank_stability": True,
            "repeat_stability": True,
            "forbidden_leakage": True,
            "latency": True,
            "bm25_execution": True,
        }
        passed, failures = evaluate_retrieval_11_gates(gates_11)
        self.assertTrue(passed)
        self.assertEqual(failures, [])

    def test_e2e_telemetry_ledger_recording_and_sealing(self) -> None:
        """Verify RunRecorder execution, sealing, and audit across lifecycle."""
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = RunRecorder(
                Path(tmpdir),
                run_id="run-wave6-convergence",
                trace_id="0123456789abcdef0123456789abcdef",
                use_case="query",
                graph_snapshot=None,
            )
            correlation = {
                "source_id": None,
                "candidate_id": None,
                "query_id": "query-001",
                "graph_operation_id": None,
                "hitl_item_id": None,
            }
            # Record L1 plan
            event = recorder.record(
                stage="L1",
                event_type="query_plan",
                payload={"plan_id": "p-001"},
                correlation=correlation,
                status="PASS",
            )
            self.assertEqual(event["stage"], "L1")

            # Seal run
            seal_result = recorder.seal(
                required_stages=["L1", "L4"],
                correlation=correlation,
                status="PASS",
            )
            self.assertEqual(seal_result["status"], "PASS")
            self.assertEqual(seal_result["event_count"], 2)


if __name__ == "__main__":
    unittest.main()
