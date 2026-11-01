"""
tests/test_ptc_phase2_e2e.py

End-to-end integration test suite for Programmatic Tool Calling (PTC) Phase 2:
- Pipeline A Stage 03 Programmatic Entity Resolution & Grounding Harness (PERH)
- Lifecycle 2 Programmatic Decision Matrix Evaluator (PDME)
- Integration into L2GraphExtractorExecutor and DecisionService
- AST-governed sandboxed execution and TokenBudget disk spooling
- Token optimization telemetry recording (> 75% token reduction)
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.decision_service import DecisionService
from dual_pipeline.ptc.contracts import (
    PTCExecutionContext,
    PTCStatus,
    TokenBudget,
)
from dual_pipeline.ptc.entity_resolution import (
    ALLOWED_ENTITY_PREFIXES,
)
from dual_pipeline.ptc.sandbox import PTCSandbox
from dual_pipeline.ptc.spooler import PTCOutputSpooler
from dual_pipeline.token_optimization.agent import TokenOptimizationAgent
from dual_pipeline.transcript_stages_core.l2_executors import L2GraphExtractorExecutor


class TestPTCPhase2EndToEnd(unittest.TestCase):

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.runtime_root = Path(self.temp_dir.name)
        self.spool_dir = self.runtime_root / "spool"
        self.spooler = PTCOutputSpooler(spool_dir=self.spool_dir)
        self.sandbox = PTCSandbox(spooler=self.spooler)
        self.token_agent = TokenOptimizationAgent()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_e2e_ptc_pipeline_a_stage03_entity_resolution_in_sandbox(self) -> None:
        mock_provider = MagicMock()
        extractor = L2GraphExtractorExecutor(provider=mock_provider)

        class ExtractorProxy:
            def __init__(self, ext: L2GraphExtractorExecutor):
                self.ext = ext

            def resolve_grounding(self, raw_triples, meeting_date, alias_map):
                return self.ext.execute_programmatic_grounding(
                    triples=raw_triples,
                    meeting_date=meeting_date,
                    catalog_alias_map=alias_map,
                )

        script = """
def run(proxy):
    raw_triples = [
        {
            "subject": "person:tanner",
            "predicate": "owns",
            "object": "system:governance_engine",
            "kind": "strategic",
            "confidence": 0.95
        },
        {
            "subject": "system:nexus_core",
            "predicate": "relies_on",
            "object": "system:maximo_asset",
            "kind": "technical",
            "confidence": 0.91
        },
        {
            "subject": "role:lead_architect",
            "predicate": "identified",
            "object": "Kafka Cold Start Lag",
            "kind": "risk",
            "confidence": 0.72
        }
    ]
    aliases = {
        "person:tanner": "person:tanner_stevens",
        "system:governance_engine": "system:governance_engine_v2"
    }
    result = proxy.resolve_grounding(raw_triples, "09/13/2026", aliases)
    return result
"""
        ctx = PTCExecutionContext(
            task_id="task_e2e_stage03_grounding",
            runtime_root=self.runtime_root,
            timeout_seconds=5.0,
            token_budget=TokenBudget(max_payload_bytes=4096),
            proxies={"proxy": ExtractorProxy(extractor)},
        )

        result = self.sandbox.execute(script, ctx)
        self.assertEqual(result.status, PTCStatus.SUCCESS)

        data = result.data
        self.assertEqual(data["total_triples"], 3)
        self.assertEqual(data["promoted_count"], 3)
        self.assertEqual(data["ambiguity_count"], 1)

        # Verify C-03/C-04 prefixes
        promoted = data["promoted_triples"]
        for t in promoted:
            subj_prefix = t["subject"].split(":", 1)[0] + ":"
            obj_prefix = t["object"].split(":", 1)[0] + ":"
            self.assertIn(subj_prefix, ALLOWED_ENTITY_PREFIXES)
            self.assertIn(obj_prefix, ALLOWED_ENTITY_PREFIXES)

        # Verify alias normalization
        self.assertEqual(promoted[0]["subject"], "person:tanner_stevens")
        self.assertEqual(promoted[0]["object"], "system:governance_engine_v2")

        # Verify W2 spine predicate normalization
        self.assertEqual(promoted[1]["predicate"], "depends_on")

        # Verify W3 temporal hygiene stamps (MM/DD/YYYY)
        for t in promoted:
            self.assertRegex(t["observed_at"], r"^\d{2}/\d{2}/\d{4}$")
            self.assertRegex(t["stale_after"], r"^\d{2}/\d{2}/\d{4}$")
            self.assertEqual(t["observed_at"], "09/13/2026")

        # Telemetry: Compare baseline raw JSON generation (~12KB) vs PTC structured result (~1.2KB)
        telemetry = self.token_agent.record_ptc_execution(
            task_id="task_e2e_stage03_grounding",
            baseline_bytes=12_288,
            spooled_summary_bytes=result.payload_bytes,
            execution_time_ms=result.execution_time_ms,
        )
        self.assertGreater(telemetry["savings_ratio"], 0.75)

    def test_e2e_ptc_lifecycle2_decision_service_in_sandbox(self) -> None:
        mock_query_engine = MagicMock()
        decision_service = DecisionService(
            query_engine=mock_query_engine,
            runtime_root=self.runtime_root,
        )

        class DecisionProxy:
            def __init__(self, svc: DecisionService):
                self.svc = svc

            def run_matrix(self, question, options, criteria, evidence, threshold=0.25):
                return self.svc.evaluate_programmatic_decision(
                    decision_question=question,
                    options=options,
                    criteria=criteria,
                    evidence_pack=evidence,
                    margin_threshold=threshold,
                )

        script_autonomous = """
def run(proxy):
    options = [
        {
            "option_id": "opt_fastapi_k8s",
            "title": "FastAPI on Managed Kubernetes",
            "evidenced_claims": ["throughput", "low latency", "cost efficiency"],
            "risk_factors": [],
            "base_feasibility": 0.92
        },
        {
            "option_id": "opt_monolith_vm",
            "title": "Monolithic Virtual Machine",
            "evidenced_claims": [],
            "risk_factors": ["high blast radius", "maintenance overhead"],
            "base_feasibility": 0.45
        }
    ]
    criteria = [
        {"name": "throughput", "weight": 1.5, "stakeholder_role": "enterprise_architect"},
        {"name": "cost_efficiency", "weight": 1.2, "stakeholder_role": "finance_controller"}
    ]
    evidence = {
        "top_facts": [
            {"node_id": "n1", "snippet": "Kubernetes provides high throughput and cost efficiency under load."}
        ]
    }
    return proxy.run_matrix("Compute Architecture Selection", options, criteria, evidence, 0.25)
"""
        ctx_auto = PTCExecutionContext(
            task_id="task_e2e_decision_auto",
            runtime_root=self.runtime_root,
            timeout_seconds=5.0,
            token_budget=TokenBudget(max_payload_bytes=4096),
            proxies={"proxy": DecisionProxy(decision_service)},
        )

        result_auto = self.sandbox.execute(script_autonomous, ctx_auto)
        self.assertEqual(result_auto.status, PTCStatus.SUCCESS)

        data_auto = result_auto.data
        self.assertEqual(data_auto["terminal_state"], "DECISION_PRODUCED")
        self.assertTrue(data_auto["is_autonomous_resolution"])
        self.assertEqual(data_auto["top_option_id"], "opt_fastapi_k8s")
        self.assertGreaterEqual(data_auto["confidence_margin_delta"], 0.25)
        self.assertIsNone(data_auto["hitl_decision_payload"])
        self.assertTrue(data_auto["receipt_digest"].startswith("sha256:"))

        # Test ambiguous scenario requiring atomic HITL escalation (< 25% margin)
        script_ambiguous = """
def run(proxy):
    options = [
        {
            "option_id": "opt_eventhub",
            "title": "Azure Event Hub Dedicated",
            "evidenced_claims": ["throughput"],
            "base_feasibility": 0.85
        },
        {
            "option_id": "opt_confluent",
            "title": "Confluent Cloud Kafka",
            "evidenced_claims": ["throughput"],
            "base_feasibility": 0.83
        }
    ]
    criteria = [
        {"name": "throughput", "weight": 1.0, "stakeholder_role": "data_engineer"}
    ]
    evidence = {
        "top_facts": [{"node_id": "n2", "snippet": "Both platforms deliver massive streaming throughput."}]
    }
    return proxy.run_matrix("Streaming Broker Selection", options, criteria, evidence, 0.25)
"""
        ctx_amb = PTCExecutionContext(
            task_id="task_e2e_decision_amb",
            runtime_root=self.runtime_root,
            timeout_seconds=5.0,
            token_budget=TokenBudget(max_payload_bytes=4096),
            proxies={"proxy": DecisionProxy(decision_service)},
        )

        result_amb = self.sandbox.execute(script_ambiguous, ctx_amb)
        self.assertEqual(result_amb.status, PTCStatus.SUCCESS)

        data_amb = result_amb.data
        self.assertEqual(data_amb["terminal_state"], "CLARIFICATION_REQUIRED")
        self.assertFalse(data_amb["is_autonomous_resolution"])
        self.assertLess(data_amb["confidence_margin_delta"], 0.25)
        self.assertIsNotNone(data_amb["hitl_decision_payload"])

        # Check HITL payload compliance
        hitl = data_amb["hitl_decision_payload"]
        self.assertEqual(len(hitl["options"]), 2)
        self.assertTrue(hitl["options"][0]["title"].startswith("(Recommended)"))
        self.assertIn("%", hitl["options"][0]["confidence"])

        # Telemetry: Compare baseline LLM dialectical reasoning (~16KB) vs PTC matrix evaluation (<2KB)
        telemetry = self.token_agent.record_ptc_execution(
            task_id="task_e2e_decision_auto",
            baseline_bytes=16_384,
            spooled_summary_bytes=result_auto.payload_bytes,
            execution_time_ms=result_auto.execution_time_ms,
        )
        self.assertGreater(telemetry["savings_ratio"], 0.75)

    def test_e2e_ptc_full_lifecycle_grounding_to_decision_evaluation(self) -> None:
        mock_provider = MagicMock()
        extractor = L2GraphExtractorExecutor(provider=mock_provider)
        mock_query_engine = MagicMock()
        decision_service = DecisionService(
            query_engine=mock_query_engine,
            runtime_root=self.runtime_root,
        )

        class FullPipelineProxy:
            def __init__(self, ext: L2GraphExtractorExecutor, svc: DecisionService):
                self.ext = ext
                self.svc = svc

            def ground_and_evaluate(self, raw_triples, meeting_date, question, options, criteria):
                grounded = self.ext.execute_programmatic_grounding(
                    triples=raw_triples,
                    meeting_date=meeting_date,
                )
                evidence_pack = {
                    "query": question,
                    "top_facts": [
                        {
                            "node_id": t["object"],
                            "name": t["subject"],
                            "snippet": f"{t['subject']} {t['predicate']} {t['object']}",
                        }
                        for t in grounded["promoted_triples"]
                    ],
                }
                receipt = self.svc.evaluate_programmatic_decision(
                    decision_question=question,
                    options=options,
                    criteria=criteria,
                    evidence_pack=evidence_pack,
                    margin_threshold=0.25,
                )
                return {
                    "grounding": grounded,
                    "decision": receipt,
                }

        script = """
def run(pipeline):
    triples = [
        {
            "subject": "system:governance_mesh",
            "predicate": "depends_on",
            "object": "system:bge_m3_retrieval",
            "kind": "technical",
            "confidence": 0.94
        }
    ]
    options = [
        {
            "option_id": "opt_mesh",
            "title": "Adopt BGE-M3 Dense-Sparse Hybrid Mesh",
            "evidenced_claims": ["bge_m3_retrieval"],
            "base_feasibility": 0.90
        },
        {
            "option_id": "opt_dense_only",
            "title": "Single-Vector Dense Only",
            "evidenced_claims": [],
            "risk_factors": ["out of vocabulary blindspot"],
            "base_feasibility": 0.50
        }
    ]
    criteria = [
        {"name": "retrieval_accuracy", "weight": 1.5}
    ]
    return pipeline.ground_and_evaluate(triples, "09/13/2026", "Embedding Backend Selection", options, criteria)
"""
        ctx = PTCExecutionContext(
            task_id="task_e2e_full_lifecycle",
            runtime_root=self.runtime_root,
            timeout_seconds=5.0,
            token_budget=TokenBudget(max_payload_bytes=8192),
            proxies={"pipeline": FullPipelineProxy(extractor, decision_service)},
        )

        result = self.sandbox.execute(script, ctx)
        self.assertEqual(result.status, PTCStatus.SUCCESS)

        data = result.data
        self.assertEqual(data["grounding"]["promoted_count"], 1)
        self.assertEqual(data["decision"]["terminal_state"], "DECISION_PRODUCED")
        self.assertEqual(data["decision"]["top_option_id"], "opt_mesh")
        self.assertGreaterEqual(data["decision"]["confidence_margin_delta"], 0.25)

        # Full pipeline token savings telemetry
        telemetry = self.token_agent.record_ptc_execution(
            task_id="task_e2e_full_lifecycle",
            baseline_bytes=24_576,
            spooled_summary_bytes=result.payload_bytes,
            execution_time_ms=result.execution_time_ms,
        )
        self.assertGreater(telemetry["savings_ratio"], 0.80)


if __name__ == "__main__":
    unittest.main()
