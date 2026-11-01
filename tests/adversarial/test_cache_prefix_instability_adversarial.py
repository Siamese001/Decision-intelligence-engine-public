"""tests/adversarial/test_cache_prefix_instability_adversarial.py.

Adversarial stress and invariant bypass tests for Wave 12:
- Rejection of timestamp, run ID, and PII poisoning in prompt cache keys.
- Verification of static schema invariance preventing per-packet cache invalidation.
- Neutralization of massive context injection exceeding subagent 4,000-token limit.
- Hard enforcement of delegation budget ceilings raising BudgetExhaustionError.
- Anthropic cache breakpoint isolation and tamper prevention.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock
from typing import Any, Mapping

from dual_pipeline.agent_architecture_core.context_profile import (
    MAX_SUBAGENT_CONTEXT_TOKENS,
    SubagentContextProfile,
    estimate_token_count,
)
from dual_pipeline.agent_architecture_core.delegation_ledger import DelegationCostLedger
from dual_pipeline.cost_tracker import BudgetExhaustionError
from dual_pipeline.l2_policy import CallPurpose
from dual_pipeline.l2_providers_core.payload import (
    DECISION_MODEL_OUTPUT_SCHEMA,
    build_l2_provider_body,
    l2_system_instruction,
)
from dual_pipeline.l2_providers_core.types import L2ProviderRequest, ModelPolicy
from dual_pipeline.prompt_cache import (
    anthropic_cache_breakpoint,
    derive_cache_key,
)
from dual_pipeline.provider_request import build_anthropic_messages


class TestCachePrefixInstabilityAdversarial(unittest.TestCase):
    """Adversarial security and invariant preservation tests for prompt caching."""

    def test_adv_timestamp_poisoning_in_cache_prefix_rejected(self):
        """Invariant: derive_cache_key must reject timestamps, run IDs, or confidential patterns."""
        poisoned_workflows = [
            "workflow_run_2026-10-05T07:00:00",
            "workflow_trace_12345678-abcd-ef01-2345-6789abcdef01",
            "workflow_salary_review",
            "workflow_with_$500,000_budget",
        ]
        for bad_wf in poisoned_workflows:
            with self.assertRaises(ValueError, msg=f"Should reject poisoned workflow: {bad_wf}"):
                derive_cache_key(
                    workflow=bad_wf,
                    model="gpt-5.6-sol",
                    static_prefix="static governance instruction",
                )

    def test_adv_dynamic_schema_mutation_prevention(self):
        """Invariant: Using static base schema must produce identical output schema across differing evidence."""
        # Two completely different evidence packets with distinct record IDs and titles
        packet_a = {
            "retrieval": [
                {
                    "lane": "retrieval",
                    "record": {
                        "record_id": "rec-alpha-001",
                        "title": "Alpha Deployment Strategy",
                        "text": "Deploying to cluster alpha in us-east-1",
                    },
                }
            ]
        }
        packet_b = {
            "retrieval": [
                {
                    "lane": "retrieval",
                    "record": {
                        "record_id": "rec-omega-999",
                        "title": "Omega Risk Mitigation Policy",
                        "text": "Risk assessment for cluster omega in eu-west-1",
                    },
                }
            ]
        }

        req_a = L2ProviderRequest(
            purpose=CallPurpose.PRIMARY,
            question="What is the alpha deployment status?",
            prompt={"template_version": "v1"},
            plan={"query_type": "strategic_analysis"},
            evidence_packet=packet_a,
        )
        req_b = L2ProviderRequest(
            purpose=CallPurpose.PRIMARY,
            question="What is the omega risk profile?",
            prompt={"template_version": "v1"},
            plan={"query_type": "strategic_analysis"},
            evidence_packet=packet_b,
        )

        mock_config = type(
            "Config",
            (),
            {
                "output": type("Output", (), {"maximum_output_tokens": 4096}),
                "prompt_cache_enabled": True,
            },
        )()
        mock_policy = MagicMock(model="gpt-5.6-sol", reasoning_effort="high")

        # When use_static_schema=True (Wave 12), the schema format is completely invariant
        body_a = build_l2_provider_body(
            config=mock_config,
            model_policy=mock_policy,
            request=req_a,
            use_static_schema=True,
        )
        body_b = build_l2_provider_body(
            config=mock_config,
            model_policy=mock_policy,
            request=req_b,
            use_static_schema=True,
        )

        schema_a = body_a["text"]["format"]["schema"]
        schema_b = body_b["text"]["format"]["schema"]

        self.assertEqual(schema_a, schema_b, "Static base schema must not mutate between distinct packets")
        self.assertEqual(schema_a, DECISION_MODEL_OUTPUT_SCHEMA)
        # Furthermore, system instructions and cache keys must remain strictly equal
        self.assertEqual(body_a["input"][0], body_b["input"][0])
        self.assertEqual(body_a.get("prompt_cache_key"), body_b.get("prompt_cache_key"))

    def test_adv_context_budget_overflow_truncation(self):
        """Adversarial: Massive context injection is strictly bounded to <= 4,000 tokens."""
        adversarial_payload = "ATTACK_PAYLOAD_EXHAUSTION: " * 5000  # ~125,000 characters (~35,000 tokens)

        contract = {
            "task_id": "adversarial-overflow-task",
            "parent_task_id": "parent-task-sec",
            "objective": "Verify system boundary robustness",
            "definition_of_done": "Pass all boundary audits",
            "allowed_tools": ["view_file"],
            "scope_in": "tests/",
            "scope_out": "production/",
            "escalation_rule": "Halt on failure",
        }

        profile = SubagentContextProfile.from_contract(contract, delegated_context=adversarial_payload)
        compiled = profile.compile_context()
        token_count = estimate_token_count(compiled)

        self.assertLessEqual(token_count, MAX_SUBAGENT_CONTEXT_TOKENS)
        self.assertIn("... [Context truncated to fit 4k token budget]", compiled)
        # Critical invariants must never be dropped by truncation
        self.assertIn("## Objective\nVerify system boundary robustness", compiled)
        self.assertIn("## Definition of Done\nPass all boundary audits", compiled)

    def test_adv_delegation_budget_ceiling_breach(self):
        """Adversarial: Delegations exceeding authorized max_cost_usd must fail closed with BudgetExhaustionError."""
        ledger = DelegationCostLedger(max_cost_usd=0.005)

        # First delegation is within budget ($0.003 <= $0.005)
        ledger.record_delegation(
            task_id="subtask-1",
            parent_task_id="root",
            subagent_role="auditor",
            model="gemini-3.8-flash",
            cost_usd=0.003,
        )

        # Second delegation breaches budget ($0.003 + $0.004 = $0.007 > $0.005)
        with self.assertRaises(BudgetExhaustionError) as ctx:
            ledger.record_delegation(
                task_id="subtask-2",
                parent_task_id="root",
                subagent_role="auditor",
                model="gemini-3.8-flash",
                cost_usd=0.004,
            )
        self.assertIn("DELEGATION_BUDGET_EXHAUSTED", str(ctx.exception))

    def test_adv_anthropic_breakpoint_caching_isolation(self):
        """Adversarial: Anthropic breakpoint helper must isolate system cache control from user blocks."""
        sys_instruction = "AUTHORITATIVE SYSTEM INSTRUCTION: All assertions require provenance."
        user_content = "Query: Analyze risk posture."

        sys_blocks, user_blocks = build_anthropic_messages(
            system_instruction=sys_instruction,
            user_content=user_content,
            enable_cache=True,
        )

        # System block has cache_control
        self.assertEqual(len(sys_blocks), 1)
        self.assertEqual(sys_blocks[0]["type"], "text")
        self.assertEqual(sys_blocks[0]["text"], sys_instruction)
        self.assertIn("cache_control", sys_blocks[0])
        self.assertEqual(sys_blocks[0]["cache_control"]["type"], "ephemeral")

        # User block does NOT have cache_control
        self.assertEqual(len(user_blocks), 1)
        self.assertNotIn("cache_control", user_blocks[0])

        # When caching is disabled, system block does NOT have cache_control
        sys_blocks_off, _ = build_anthropic_messages(
            system_instruction=sys_instruction,
            user_content=user_content,
            enable_cache=False,
        )
        self.assertNotIn("cache_control", sys_blocks_off[0])


if __name__ == "__main__":
    unittest.main()
