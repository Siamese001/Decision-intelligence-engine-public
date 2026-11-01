"""
tests/test_ptc_decision_evaluator.py

Unit tests for Programmatic Decision Matrix Evaluator (PDME) in Lifecycle 2.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.ptc.contracts import (
    PTCExecutionContext,
    PTCStatus,
    TokenBudget,
)
from dual_pipeline.ptc.decision_evaluator import (
    PTCDecisionCriterion,
    PTCDecisionMatrixEvaluator,
    PTCDecisionMatrixPlan,
    PTCDecisionOption,
)
from dual_pipeline.ptc.sandbox import PTCSandbox


class TestPTCDecisionEvaluator(unittest.TestCase):

    def setUp(self) -> None:
        self.evaluator = PTCDecisionMatrixEvaluator()
        self.sample_evidence_pack = {
            "query": "Kubernetes vs Serverless for Realtime Processing",
            "top_facts": [
                {
                    "node_id": "node_k8s",
                    "name": "Kubernetes Microservices Cluster",
                    "snippet": "Provides high scalability, sub-millisecond network latency, and low operational cost at scale.",
                    "properties": {"tech": "k8s"},
                },
                {
                    "node_id": "node_serverless",
                    "name": "Cloud Functions",
                    "snippet": "High per-request licensing cost and cold-start latency risk.",
                    "properties": {"tech": "serverless"},
                },
            ],
        }

    def test_autonomous_resolution_when_delta_ge_25_percent(self) -> None:
        opt_k8s = PTCDecisionOption(
            option_id="opt_k8s",
            title="Kubernetes Microservices Cluster",
            evidenced_claims=["scalability", "latency", "operational cost"],
            risk_factors=[],
            base_feasibility=0.90,
        )
        opt_legacy = PTCDecisionOption(
            option_id="opt_legacy",
            title="Legacy Monolith Virtual Machine",
            evidenced_claims=[],
            risk_factors=["technical debt", "operational cost", "incident risk"],
            base_feasibility=0.40,
        )

        plan = PTCDecisionMatrixPlan(
            decision_question="Select compute runtime architecture for data ingestion",
            options=[opt_k8s, opt_legacy],
            criteria=[
                PTCDecisionCriterion(name="scalability", weight=1.5),
                PTCDecisionCriterion(name="operational_cost", weight=1.2),
            ],
            evidence_pack=self.sample_evidence_pack,
            margin_threshold=0.25,
        )

        receipt = self.evaluator.evaluate(plan)

        self.assertEqual(receipt.terminal_state, "DECISION_PRODUCED")
        self.assertTrue(receipt.is_autonomous_resolution)
        self.assertEqual(receipt.top_option_id, "opt_k8s")
        self.assertGreaterEqual(receipt.confidence_margin_delta, 0.25)
        self.assertIsNone(receipt.hitl_decision_payload)
        self.assertTrue(receipt.receipt_digest.startswith("sha256:"))

    def test_ambiguity_hitl_escalation_when_delta_lt_25_percent(self) -> None:
        # Two very close options
        opt_a = PTCDecisionOption(
            option_id="opt_a",
            title="Event Hub Direct Ingress",
            evidenced_claims=["scalability"],
            risk_factors=[],
            base_feasibility=0.82,
        )
        opt_b = PTCDecisionOption(
            option_id="opt_b",
            title="Kafka Dedicated Cluster",
            evidenced_claims=["scalability"],
            risk_factors=[],
            base_feasibility=0.80,
        )

        plan = PTCDecisionMatrixPlan(
            decision_question="Select message broker streaming tier",
            options=[opt_a, opt_b],
            criteria=[
                PTCDecisionCriterion(name="scalability", weight=1.0),
            ],
            evidence_pack=self.sample_evidence_pack,
            margin_threshold=0.25,
        )

        receipt = self.evaluator.evaluate(plan)

        self.assertEqual(receipt.terminal_state, "CLARIFICATION_REQUIRED")
        self.assertFalse(receipt.is_autonomous_resolution)
        self.assertLess(receipt.confidence_margin_delta, 0.25)
        self.assertIsNotNone(receipt.hitl_decision_payload)

        # Verify HITL payload format
        payload = receipt.hitl_decision_payload
        self.assertEqual(len(payload["options"]), 2)
        self.assertTrue(payload["options"][0]["title"].startswith("(Recommended)"))
        self.assertIn("%", payload["options"][0]["confidence"])

    def test_retrieval_rank_decoupling_invariant(self) -> None:
        # Option with claims evidenced in text vs option without
        opt_supported = PTCDecisionOption(
            option_id="opt_supported",
            title="Supported Architecture",
            evidenced_claims=["scalability"],
            base_feasibility=0.70,
        )
        opt_unsupported = PTCDecisionOption(
            option_id="opt_unsupported",
            title="Unsupported Guess",
            evidenced_claims=["unsupported fantasy claims"],
            risk_factors=["severe gap"],
            base_feasibility=0.70,
        )

        plan = PTCDecisionMatrixPlan(
            decision_question="Decoupled option scoring check",
            options=[opt_supported, opt_unsupported],
            criteria=[PTCDecisionCriterion(name="scalability", weight=1.0)],
            evidence_pack=self.sample_evidence_pack,
        )

        receipt = self.evaluator.evaluate(plan)
        # Supported option must score higher
        self.assertEqual(receipt.top_option_id, "opt_supported")

    def test_sandbox_executes_decision_matrix(self) -> None:
        class DecisionProxy:
            def __init__(self, evaluator):
                self.evaluator = evaluator

            def evaluate_matrix(self, question, options_list, evidence):
                opts = [PTCDecisionOption(**o) for o in options_list]
                crits = [PTCDecisionCriterion(name="scalability", weight=1.0)]
                plan = PTCDecisionMatrixPlan(
                    decision_question=question,
                    options=opts,
                    criteria=crits,
                    evidence_pack=evidence,
                    margin_threshold=0.20,
                )
                return self.evaluator.evaluate(plan).to_dict()

        script = """
def run(decision_proxy):
    opts = [
        {"option_id": "opt_1", "title": "Option 1", "evidenced_claims": ["scalability"], "base_feasibility": 0.9},
        {"option_id": "opt_2", "title": "Option 2", "base_feasibility": 0.5}
    ]
    res = decision_proxy.evaluate_matrix("Architecture Choice", opts, {"top_facts": [{"name": "scalability"}]})
    return {
        "status": res["terminal_state"],
        "winner": res["top_option_id"],
        "delta": res["confidence_margin_delta"]
    }
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ctx = PTCExecutionContext(
                task_id="task_decision_sandbox",
                runtime_root=Path(tmp_dir),
                timeout_seconds=5.0,
                token_budget=TokenBudget(max_payload_bytes=2048),
                proxies={"decision_proxy": DecisionProxy(self.evaluator)},
            )
            sandbox = PTCSandbox()
            summary = sandbox.execute(script, ctx)

        self.assertEqual(summary.status, PTCStatus.SUCCESS)
        self.assertEqual(summary.data["status"], "DECISION_PRODUCED")
        self.assertEqual(summary.data["winner"], "opt_1")
        self.assertGreaterEqual(summary.data["delta"], 0.20)


if __name__ == "__main__":
    unittest.main()
