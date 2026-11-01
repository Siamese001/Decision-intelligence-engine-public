"""Wave 3 Acceptance Test Suite: Policy-Gated Retrieval & Cross-Cutting Micro-Judges.

Verifies:
1. MultiPerspectiveDispatcher bound to local Meta-Llama-3.1-8B under MLX.
2. RetrievalPerspectiveHealerAgent bound to local Qwen2.5-14B under MLX.
3. PolicyGatekeeperAgent enforcing Law 6 (Policy Before Retrieval) with zero leakage.
4. ReleaseGateAuditor enforcing all 11 non-compensating release gates.
5. ForensicTranscriptJudge, ExecutiveRecapJudge, and GraphExtractionJudge bound to local models.
6. Calibrated scoring rubrics and Rule C-19 Career Leverage Firewall enforcement.
"""
from __future__ import annotations

import asyncio
import json
import unittest

from dual_pipeline.a2a import A2ATask, A2ATaskStatus
from dual_pipeline.llm_judge import (
    DecomposedMultiJudge,
    ExecutiveRecapJudge,
    ForensicTranscriptJudge,
    GraphExtractionJudge,
)
from dual_pipeline.retrieval.agents.multi_perspective_dispatcher import MultiPerspectiveDispatcher
from dual_pipeline.retrieval.agents.policy_gatekeeper_agent import PolicyGatekeeperAgent
from dual_pipeline.retrieval.agents.release_gate_auditor import GATE_NAMES, ReleaseGateAuditor
from dual_pipeline.retrieval.agents.retrieval_perspective_healer import (
    RetrievalPerspectiveHealerAgent,
)


class TestWave3RetrievalJudges(unittest.TestCase):
    """Wave 3 Acceptance Test Suite for Policy-Gated Retrieval and Micro-Judges."""

    def test_multi_perspective_dispatcher_local_binding(self) -> None:
        """Verifies MultiPerspectiveDispatcher operates under local Meta-Llama-3.1-8B MLX model."""
        agent = MultiPerspectiveDispatcher(use_local_model=True, mock_mode=True)
        self.assertEqual(agent.card.model_binding.model_id, "mlx-community/Meta-Llama-3.1-8B-Instruct-4bit")
        self.assertEqual(agent.card.model_binding.provider, "mlx")
        self.assertIn("mlx_inference", agent.card.allowed_tools)

        task = A2ATask(
            task_id="dispatcher-task-001",
            parent_task_id=None,
            sender_agent_id="urn:nee:coordinator",
            recipient_agent_id=agent.card.agent_id,
            objective="Decompose executive query across perspectives",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={"query": "What is the technical architecture for the telemetry platform?"},
        )

        res = asyncio.run(agent.handle_task(task))
        self.assertEqual(res["status"], "COMPLETED")
        self.assertEqual(res["perspective_count"], 4)
        perspectives = {sq["perspective"] for sq in res["sub_queries"]}
        self.assertEqual(perspectives, {"TECHNICAL", "COMMERCIAL", "ADOPTION", "BLOCKER"})

    def test_retrieval_perspective_healer_local_binding(self) -> None:
        """Verifies RetrievalPerspectiveHealerAgent operates under local Qwen2.5-14B MLX model."""
        agent = RetrievalPerspectiveHealerAgent(use_local_model=True, mock_mode=True)
        self.assertEqual(agent.card.model_binding.model_id, "mlx-community/Qwen2.5-14B-Instruct-4bit")
        self.assertEqual(agent.card.model_binding.provider, "mlx")
        self.assertIn("mlx_inference", agent.card.allowed_tools)

        task = A2ATask(
            task_id="healer-task-001",
            parent_task_id=None,
            sender_agent_id="urn:nee:coordinator",
            recipient_agent_id=agent.card.agent_id,
            objective="Repair starved perspective lane",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={
                "query": "Solar battery telemetry constraints",
                "starved_lens": "TECHNICAL",
                "ontology_terms": ["system:telemetry_bus", "workstream:data_intake"],
            },
        )

        res = asyncio.run(agent.handle_task(task))
        self.assertEqual(res["status"], "COMPLETED")
        self.assertIn("healing_result", res)

    def test_law6_policy_before_retrieval_zero_leakage(self) -> None:
        """Verifies Law 6: Hard pre-retrieval filtering guarantees zero leakage of INTERNAL_ADVISORY."""
        gatekeeper = PolicyGatekeeperAgent()
        candidate_facts = [
            {"node_id": "fact-1", "namespace": "CLIENT_SAFE", "claim": "Telemetry data pipeline architecture."},
            {"node_id": "fact-2", "namespace": "INTERNAL_ADVISORY", "claim": "$1.2M executive advisory wedge."},
            {"node_id": "fact-3", "namespace": "CLIENT_SAFE", "claim": "AWS S3 storage bucket configuration."},
            {"node_id": "fact-4", "namespace": "INTERNAL_ADVISORY", "claim": "Confidential compensation negotiation."},
            {"node_id": "fact-5", "namespace": "CLIENT_SAFE", "claim": "Quarantined assertion", "is_quarantined": True},
        ]

        task = A2ATask(
            task_id="policy-task-001",
            parent_task_id=None,
            sender_agent_id="urn:nee:coordinator",
            recipient_agent_id=gatekeeper.card.agent_id,
            objective="Filter facts prior to semantic retrieval",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={
                "candidate_facts": candidate_facts,
                "caller_namespace": "CLIENT_SAFE",
                "stealth_mode": True,
                "include_quarantined": False,
            },
        )

        res = asyncio.run(gatekeeper.handle_task(task))
        self.assertEqual(res["status"], "COMPLETED")
        self.assertEqual(res["blocked_leak_count"], 2)
        self.assertEqual(res["passed_count"], 2)

        # Ensure no INTERNAL_ADVISORY facts leaked into filtered results
        filtered_namespaces = [f["namespace"] for f in res["filtered_facts"]]
        self.assertNotIn("INTERNAL_ADVISORY", filtered_namespaces)
        self.assertEqual(set(filtered_namespaces), {"CLIENT_SAFE"})

    def test_release_gate_auditor_11_gates_qualification(self) -> None:
        """Verifies ReleaseGateAuditor strictly verifies all 11 non-compensating release gates."""
        auditor = ReleaseGateAuditor(mock_mode=True)
        self.assertEqual(len(GATE_NAMES), 11)

        # Normal qualifying run
        task_pass = A2ATask(
            task_id="audit-task-001",
            parent_task_id=None,
            sender_agent_id="urn:nee:coordinator",
            recipient_agent_id=auditor.card.agent_id,
            objective="Audit 11 release gates",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={
                "graph_sha256": "fcc2c5d791539e52bcdd49dabb9d3fdf1c1cf74e6eda1de815ae4e0f0f7d29d2",
                "sidecar_sha256": "e38eb897ba416b60434ca61fac736b02f205c22b5dd2a781a9d1ae752d92bad8",
                "max_query_latency_ms": 145.0,
            },
        )
        res_pass = asyncio.run(auditor.handle_task(task_pass))
        self.assertTrue(res_pass["all_gates_passed"])
        self.assertEqual(res_pass["passed_gate_count"], 11)
        self.assertEqual(res_pass["qualification_status"], "DELIVERY_READY")

        # Non-compensating latency ceiling violation (>7500ms)
        task_fail = A2ATask(
            task_id="audit-task-002",
            parent_task_id=None,
            sender_agent_id="urn:nee:coordinator",
            recipient_agent_id=auditor.card.agent_id,
            objective="Audit 11 release gates with high latency",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={
                "graph_sha256": "fcc2c5d791539e52bcdd49dabb9d3fdf1c1cf74e6eda1de815ae4e0f0f7d29d2",
                "sidecar_sha256": "e38eb897ba416b60434ca61fac736b02f205c22b5dd2a781a9d1ae752d92bad8",
                "max_query_latency_ms": 8200.0,
            },
        )
        res_fail = asyncio.run(auditor.handle_task(task_fail))
        self.assertFalse(res_fail["all_gates_passed"])
        self.assertEqual(res_fail["gate_results"]["latency_threshold"]["status"], "FAIL")
        self.assertEqual(res_fail["qualification_status"], "BLOCKED")

    def test_forensic_transcript_judge_local_binding_and_c19(self) -> None:
        """Verifies ForensicTranscriptJudge local Qwen2.5-14B binding and Rule C-19 enforcement."""
        judge = ForensicTranscriptJudge(use_local_model=True)
        self.assertEqual(judge.model, "mlx-community/Qwen2.5-14B-Instruct-4bit")

        # Clean evaluation
        clean_res = judge.evaluate(
            f1_text="[00:01:00] Alex Rivera: Discussing system boundaries.",
            raw_source_text="Alex Rivera: Discussing system boundaries.",
            mock=True,
        )
        self.assertEqual(clean_res["verdict"], "PASS")
        self.assertGreaterEqual(clean_res["composite_score"], 80)

        # C-19 Career Leverage violation
        leaked_res = judge.evaluate(
            f1_text="[00:01:00] Alex Rivera: Discussing executive compensation band and internal advisory wedge.",
            raw_source_text="Alex Rivera: Discussing system boundaries.",
            mock=True,
        )
        self.assertEqual(leaked_res["verdict"], "FAIL")
        self.assertLessEqual(leaked_res["composite_score"], 30)
        self.assertIn("RULE C-19 VIOLATION", leaked_res["forensic_rationale"])

    def test_executive_recap_judge_local_binding_and_c19(self) -> None:
        """Verifies ExecutiveRecapJudge local Qwen2.5-14B binding and Rule C-19 enforcement."""
        judge = ExecutiveRecapJudge(use_local_model=True)
        self.assertEqual(judge.model, "mlx-community/Qwen2.5-14B-Instruct-4bit")

        # Clean evaluation
        clean_res = judge.evaluate(
            f2_text="## Executive Objective\nAdvance technical telemetry architecture.",
            f1_text="Alex Rivera: Discussing system boundaries.",
            mock=True,
        )
        self.assertEqual(clean_res["verdict"], "PASS")
        self.assertGreaterEqual(clean_res["composite_score"], 95)

        # C-19 Career Leverage violation
        leaked_res = judge.evaluate(
            f2_text="## Executive Objective\nSecure $1.2M political_rationale advisory positioning.",
            f1_text="Alex Rivera: Discussing system boundaries.",
            mock=True,
        )
        self.assertEqual(leaked_res["verdict"], "FAIL")
        self.assertLessEqual(leaked_res["composite_score"], 30)
        self.assertIn("RULE C-19 VIOLATION", leaked_res["forensic_rationale"])

    def test_graph_extraction_judge_local_binding_and_c19(self) -> None:
        """Verifies GraphExtractionJudge local DeepSeek-R1 binding and Rule C-19 enforcement."""
        judge = GraphExtractionJudge(use_local_model=True, mock=True)
        self.assertEqual(judge.model, "mlx-community/DeepSeek-R1-Distill-Qwen-14B-4bit")

        # Clean evaluation
        clean_f3 = json.dumps({"entities": [{"id": "system:telemetry"}], "relations": []})
        clean_res = judge.evaluate(
            f3_text=clean_f3,
            f1_text="Alex Rivera: Discussing system boundaries.",
            mock=True,
        )
        self.assertEqual(clean_res["verdict"], "PASS")
        self.assertGreaterEqual(clean_res["composite_score"], 95)

        # C-19 Career Leverage violation
        leaked_f3 = json.dumps({"entities": [{"id": "system:telemetry", "notes": "INTERNAL_ADVISORY leverage"}]})
        leaked_res = judge.evaluate(
            f3_text=leaked_f3,
            f1_text="Alex Rivera: Discussing system boundaries.",
            mock=True,
        )
        self.assertEqual(leaked_res["verdict"], "FAIL")
        self.assertLessEqual(leaked_res["composite_score"], 30)
        self.assertIn("RULE C-19 VIOLATION", leaked_res["forensic_rationale"])

    def test_decomposed_multi_judge_orchestrator_local_mode(self) -> None:
        """Verifies DecomposedMultiJudge propagates use_local_model to all child micro-judges."""
        orchestrator = DecomposedMultiJudge(use_local_model=True, mock=True)
        self.assertTrue(orchestrator.judge_forensic.use_local_model)
        self.assertTrue(orchestrator.judge_recap.use_local_model)
        self.assertTrue(orchestrator.judge_graph.use_local_model)
        self.assertEqual(orchestrator.judge_forensic.model, "mlx-community/Qwen2.5-14B-Instruct-4bit")
        self.assertEqual(orchestrator.judge_recap.model, "mlx-community/Qwen2.5-14B-Instruct-4bit")
        self.assertEqual(orchestrator.judge_graph.model, "mlx-community/DeepSeek-R1-Distill-Qwen-14B-4bit")


if __name__ == "__main__":
    unittest.main()
