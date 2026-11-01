"""End-to-End test suite for 3-tier multi-agent hierarchy and lifecycle policies.

Validates the full lifecycle:
1. Instantiation of Tier 0 Decision Supervisor with 3 bound Tier 1 specialists
2. Application of governed security policies and lifecycle hooks
3. End-to-end delegation contract formulation and verification for each specialist
4. Stop hook validation ensuring completion criteria before conclusion
"""
from __future__ import annotations

import asyncio
import unittest

from google.antigravity import Agent, LocalAgentConfig
from google.antigravity.types import StopArgs, StopDecision

from dual_pipeline.agent_sdk import (
    AgentConfigFactory,
    NeeAgentHierarchy,
    NeeLifecycleHooks,
    NeeSecurityPolicy,
)


class TestAgentOrchestrationE2E(unittest.TestCase):
    """End-to-End test suite exercising multi-agent hierarchy and lifecycle policies."""

    def test_full_hierarchy_initialization_with_policies_and_hooks(self):
        """Exercises complete supervisor initialization with 3 bound specialists, policies, and hooks."""
        factory = AgentConfigFactory(env={"GEMINI_API_KEY": "dummy-hier-e2e-key"})

        # 1. Build supervisor config with specialists
        supervisor_config = NeeAgentHierarchy.build_supervisor_config(factory=factory)
        self.assertIsInstance(supervisor_config, LocalAgentConfig)
        self.assertEqual(len(supervisor_config.subagents), 3)

        # 2. Attach governed policies and lifecycle hooks
        policies = NeeSecurityPolicy.build_governed_policies()
        hooks = NeeLifecycleHooks(run_id="run_hier_e2e_001")

        # Manually bind policies
        configured_supervisor = LocalAgentConfig(
            system_instructions=supervisor_config.system_instructions,
            subagents=supervisor_config.subagents,
            policies=policies,
            budget_config=supervisor_config.budget_config,
            compaction_config=supervisor_config.compaction_config,
            capabilities=supervisor_config.capabilities,
            model=supervisor_config.model,
        )

        agent = Agent(configured_supervisor)
        self.assertIsNotNone(agent)

        # 3. Formulate and validate delegation contracts to each of the 3 specialists
        specialists = [
            (
                "ingestion_specialist",
                ["verify_contract_compliance"],
                "intake_verification_receipt",
            ),
            (
                "retrieval_specialist",
                ["query_knowledge_graph", "evaluate_candidate_retrieval"],
                "retrieval_11gates_clearance",
            ),
            (
                "spark_analytics_specialist",
                ["execute_spark_sql", "submit_spark_graph_batch"],
                "batch_analytics_report",
            ),
        ]

        for spec_name, scope_in, deliverable in specialists:
            payload, is_valid, errors = NeeAgentHierarchy.create_delegation_contract(
                specialist_name=spec_name,
                task_id=f"task_{spec_name}_001",
                parent_task_id="parent_super_e2e",
                objective=f"Execute {spec_name} operations",
                scope_in=scope_in,
                scope_out=["unauthorized_mutation"],
                deliverable=deliverable,
                definition_of_done="All acceptance criteria verified",
                budget={"max_model_calls": 20, "max_tool_calls": 40},
                escalation_rule="Escalate to supervisor on ambiguity margin < 15%",
            )
            self.assertTrue(is_valid, f"Contract for {spec_name} invalid: {errors}")
            self.assertEqual(payload["deliverable"], deliverable)

        # 4. Verify stop hook lifecycle with execution evidence
        stop_fn = hooks.build_stop_hook(enforce_receipt_verification=True)
        # Add simulated execution record
        hooks.tool_call_history.append({"name": "query_knowledge_graph", "status": "APPROVED"})
        stop_args = StopArgs(response_text="Synthesis complete.", continuation_count=0)
        stop_verdict = asyncio.run(stop_fn(stop_args))
        self.assertEqual(stop_verdict.decision, StopDecision.ALLOW_STOP)


if __name__ == "__main__":
    unittest.main()
