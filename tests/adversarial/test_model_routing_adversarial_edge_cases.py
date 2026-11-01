"""
tests/adversarial/test_model_routing_adversarial_edge_cases.py

Adversarial stress and edge-case testing for 2026+ Decoupled Model Routing Architecture.
Covers >=4 mandatory edge-case categories:
1. Boundary Payloads:
   - False-Negative Trap: 40-token CapEx/SOW dispute with high blast radius forced to Sol 6.1.
   - False-Positive Trap: 60,000-token caption transform forced to cheap Luna.
2. Invariant Bypass & Schema Violation:
   - Malformed entity IDs violating W2 Semantic Spine caught in <50ms and escalated to Sol.
3. Concurrency / Quota Exhaustion & Multi-Gen Cascade:
   - HTTP 429 quota exhaustion on Luna, HTTP 503 on Sol 6.1, recovering on SRE fallback.
4. Epistemic Ambiguity Margin Jitter:
   - Jitter right around 15% threshold; verified suppression of false-alarm HITL modals.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

import pytest

from dual_pipeline.cost_tracker import BudgetExhaustionError, RunCostTracker
from dual_pipeline.manifest_runner.remediation_healer import heal_output_3_with_fast_validation
from dual_pipeline.model_registry import ModelTier, resolve_tier_model_name
from dual_pipeline.query_router_core.complexity_classifier import (
    evaluate_complexity_tensor,
)
from dual_pipeline.query_router_core.escalation_shield import (
    execute_with_multigen_fallback,
    resolve_two_stage_ambiguity,
    validate_fast_schema_57,
)


@pytest.mark.adversarial
class TestModelRoutingAdversarialEdgeCases(unittest.TestCase):
    """Adversarial stress harness for model routing and escalation shields."""

    def setUp(self) -> None:
        self.env_patcher = mock.patch.dict(
            os.environ,
            {
                "FAST_ROUTING_MODEL": "",
                "FAST_ROUTING_REASONING_EFFORT": "",
                "COPILOT_MODEL": "",
                "COPILOT_REASONING_EFFORT": "",
                "QUERY_REASONING_MODEL": "",
                "QUERY_REASONING_REASONING_EFFORT": "",
                "OPENAI_MODEL": "",
                "OPENAI_REASONING_EFFORT": "",
            },
        )
        self.env_patcher.start()

    def tearDown(self) -> None:
        self.env_patcher.stop()

    def test_adversarial_boundary_false_negative_short_financial_prompt(self) -> None:
        """
        Category 1: Boundary Payloads (False Negative).
        A 35-token short prompt with subtle contractual dispute and CapEx penalty.
        Under naive 1D token routing (<16k), this would route to Luna and miss the liability.
        Under 5D tensor (Cognitive Depth 4, Blast Radius 4), it MUST route to Sol 6.1.
        """
        prompt = "Does SOW section 4.2 allow contractor to bill overtime without prior VP CapEx sign-off?"
        tensor, tier = evaluate_complexity_tensor(
            payload=prompt,
            metadata={"task_type": "contract_analysis", "mutates_graph": True},
        )
        self.assertEqual(tier, ModelTier.QUERY_REASONING)
        sol_model = resolve_tier_model_name(tier)
        self.assertIn("sol", sol_model.lower())
        self.assertGreaterEqual(tensor.blast_radius, 4)
        self.assertGreaterEqual(tensor.cognitive_depth, 4)

    def test_adversarial_boundary_false_positive_60k_caption_transform(self) -> None:
        """
        Category 1: Boundary Payloads (False Positive).
        A 60,000-token massive WebVTT file needing timestamp removal and plain text formatting.
        Under naive 1D token routing (>16k), this would route to expensive Sol or Astra ($1.50+).
        Under 5D tensor (Cognitive Depth 1, Blast Radius 1), it MUST route to Luna ($0.03).
        """
        # Simulate 60,000 token caption transform
        prompt = "WEBVTT\n" + ("00:01:00.000 --> 00:01:05.000\n[Music playing]\n" * 5000)
        tensor, tier = evaluate_complexity_tensor(
            payload=prompt,
            metadata={"task_type": "webvtt_strip", "mutates_graph": False, "draft_only": True},
        )
        self.assertEqual(tier, ModelTier.FAST_ROUTING)
        luna_model = resolve_tier_model_name(tier)
        self.assertIn("luna", luna_model.lower())
        self.assertEqual(tensor.cognitive_depth, 1)
        self.assertLessEqual(tensor.blast_radius, 2)

    def test_adversarial_invariant_bypass_malformed_w2_prefixes(self) -> None:
        """
        Category 2: Invariant Bypass & Malformed Input.
        Output 03 payload contains invalid prefixes violating W2 Semantic Spine.
        Verified that validate_fast_schema_57 catches defects in <50ms and
        heal_output_3_with_fast_validation triggers instant Sol escalation.
        """
        malformed_f3 = {
            "project": "project_nexus",
            "entities": [
                {"id": "user:jen", "type": "person", "canonical_name": "Jen"},
                {"id": "vendor:partner_advisory", "type": "organization", "canonical_name": "PartnerAdvisory"},
                {"id": "table:raw_costs", "type": "dataset", "canonical_name": "Raw Costs"},
            ],
            "relationships": [
                {"source": "user:jen", "target": "vendor:partner_advisory", "predicate": "MANAGES"},
            ],
        }
        res = validate_fast_schema_57(malformed_f3)
        self.assertFalse(res.is_valid)
        self.assertLess(res.elapsed_ms, 50.0)
        self.assertEqual(len(res.errors), 3)
        self.assertTrue(all("W2_PREFIX_VIOLATION" in e for e in res.errors))

        import json
        _, was_escalated, msg = heal_output_3_with_fast_validation(
            f3_text=json.dumps(malformed_f3),
            f1_text="",
            model=None,
            client=None,
        )
        self.assertTrue(was_escalated)
        self.assertIn("Escalated to", msg)

    def test_adversarial_quota_exhaustion_multi_tier_sre_fallback(self) -> None:
        """
        Category 3: Concurrency / Quota Exhaustion.
        Simulates HTTP 429 (rate-limit) on primary Luna, HTTP 503 on Sol 6.1,
        and verifies automatic cascade recovery to gpt-5.6-terra without data loss.
        """
        class MockHttpError(Exception):
            def __init__(self, status_code: int, msg: str):
                super().__init__(msg)
                self.status_code = status_code

        def endpoint_dispatcher(model: str) -> str:
            if "luna" in model:
                raise MockHttpError(429, "Rate limit exceeded: 30000 TPM quota reached")
            if "6.1" in model or "sol" in model:
                raise MockHttpError(503, "503 Service Unavailable: upstream timeout")
            if "terra" in model:
                return f"SUCCESS_RECOVERED_BY_{model}"
            return f"DEFAULT_SUCCESS_{model}"

        custom_chain = ["gpt-6-luna", "gpt-6.1-sol", "gpt-5.6-terra", "gpt-6-luna"]
        res, chosen_model, log = execute_with_multigen_fallback(
            call_fn=endpoint_dispatcher,
            primary_model="gpt-6-luna",
            fallback_chain=custom_chain,
            max_retries_per_tier=1,
        )

        self.assertEqual(chosen_model, "gpt-5.6-terra")
        self.assertIn("SUCCESS_RECOVERED", res)
        self.assertEqual(len(log), 3)
        self.assertEqual(log[0]["status"], "FAILED")
        self.assertEqual(log[1]["status"], "FAILED")
        self.assertEqual(log[2]["status"], "SUCCESS")

    def test_adversarial_ambiguity_margin_jitter_shield(self) -> None:
        """
        Category 4: Epistemic Ambiguity Jitter.
        Tests jittering margin around 0.15 threshold:
        - When primary margin is 0.14 (<0.15) but Sol yields 0.25 (>=0.15):
          HITL is SUPPRESSED.
        - When primary margin is 0.05 (<0.15) and Sol confirms 0.06 (<0.15):
          HITL is REQUIRED.
        """
        # Sub-test 1: Margin 0.14 -> Sol 0.25 -> Suppress HITL
        res_suppressed = resolve_two_stage_ambiguity(
            candidate_name_1="Alice",
            confidence_1=0.57,
            candidate_name_2="Bob",
            confidence_2=0.43,
            secondary_evaluator_fn=lambda: ("Alice", 0.75, 0.25),
            threshold=0.15,
        )
        self.assertFalse(res_suppressed.hitl_required)
        self.assertEqual(res_suppressed.resolved_by, "SOL_SHIELD_AUTONOMOUS")

        # Sub-test 2: Margin 0.05 -> Sol 0.06 -> Require HITL
        res_required = resolve_two_stage_ambiguity(
            candidate_name_1="Alice",
            confidence_1=0.525,
            candidate_name_2="Bob",
            confidence_2=0.475,
            secondary_evaluator_fn=lambda: ("Alice", 0.53, 0.06),
            threshold=0.15,
        )
        self.assertTrue(res_required.hitl_required)
        self.assertEqual(res_required.resolved_by, "HITL_REQUIRED")

    def test_adversarial_budget_ceiling_circuit_breaker(self) -> None:
        """
        Category 5: Budget Exhaustion Circuit Breaker.
        Verifies that RunCostTracker halts execution when budget ceiling is exceeded.
        """
        tracker = RunCostTracker(max_cost_usd=1.00)
        # Record a cheap call
        tracker.record_call("gpt-6-luna", uncached_prompt_tokens=1000, completion_tokens=500)
        self.assertLess(tracker.total_cost_usd, 1.00)

        # Attempt to record an expensive call exceeding $1.00
        with self.assertRaises(BudgetExhaustionError):
            tracker.record_call("gpt-6-luna", uncached_prompt_tokens=200000, completion_tokens=50000)


if __name__ == "__main__":
    unittest.main()
