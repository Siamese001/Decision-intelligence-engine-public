"""End-to-End lifecycle test suite for Antigravity SDK Agent initialization and governance.

Validates the full lifecycle of Antigravity Agent creation, budget enforcement,
connection resolution, and subagent tree constraints within the Decision Intelligence Engine.
"""
from __future__ import annotations

import unittest

from google.antigravity import Agent, AgentBehavior, LocalAgentConfig
from google.antigravity.types import SubagentConfig

from dual_pipeline.agent_sdk import (
    AgentConfigFactory,
    ConnectionStrategy,
    GovernedAgentProfile,
    create_governed_agent,
)
from dual_pipeline.unified_agent_architecture import (
    validate_delegation_contract,
)


class TestAgentSDKE2E(unittest.TestCase):
    """End-to-End test suite exercising SDK agent lifecycle and governance."""

    def test_full_sdk_agent_lifecycle_creation_and_contract_validation(self):
        """Exercises complete agent creation lifecycle and verifies budget/contract invariants."""
        env = {
            "GEMINI_API_KEY": "dummy-sdk-test-key",
            "ANTIGRAVITY_CONNECTION_STRATEGY": "gemini_developer_api",
        }
        profile = GovernedAgentProfile(
            name="decision_supervisor",
            behavior=AgentBehavior.AUTONOMOUS,
            max_model_calls=40,
            max_tool_calls=80,
            token_compaction_threshold=60000,
            max_subagent_depth=2,
            enable_subagents=True,
        )

        subagent_spec = SubagentConfig(
            name="retrieval_specialist",
            description="Executes hybrid retrieval queries and 11-gate clearance",
            system_instructions="You are a bounded retrieval specialist.",
        )

        factory = AgentConfigFactory(env=env, default_profile=profile)
        config = factory.create_local_agent_config(
            system_instructions="Primary decision intelligence supervisor.",
            subagents=[subagent_spec],
            model_name="gemini-3.8-flash",
        )

        # 1. Config validation
        self.assertIsInstance(config, LocalAgentConfig)
        self.assertEqual(config.model, "gemini-3.8-flash")
        self.assertEqual(config.budget_config.max_model_calls, 40)
        self.assertEqual(config.budget_config.max_tool_calls, 80)
        self.assertEqual(config.compaction_config.token_threshold, 60000)
        self.assertEqual(config.capabilities.max_subagent_depth, 2)
        self.assertTrue(config.capabilities.enable_subagents)
        self.assertEqual(len(config.subagents), 1)
        self.assertEqual(config.subagents[0].name, "retrieval_specialist")

        # 2. Agent instantiation (checks schema & internal wire-up)
        agent = Agent(config)
        self.assertIsNotNone(agent)

        # 3. Cross-validate with unified_agent_architecture contracts
        contract_payload = {
            "task_id": "task_sdk_e2e_001",
            "parent_task_id": "parent_decision_eval",
            "objective": "Evaluate strategic decision options",
            "scope_in": ["query_retrieval", "graph_inspection"],
            "scope_out": ["unauthorized_graph_mutation"],
            "allowed_tools": ["nee_query", "graphdb_read_query"],
            "deliverable": "strategic_evidence_summary",
            "definition_of_done": "All gates cleared with receipts",
            "budget": {"max_model_calls": 40, "max_tool_calls": 80},
            "escalation_rule": "Escalate to HITL when ambiguity margin < 15%",
        }
        is_valid, validation_errors = validate_delegation_contract(contract_payload)
        self.assertTrue(is_valid, f"Delegation contract validation failed: {validation_errors}")

    def test_vertex_ai_enterprise_lifecycle(self):
        """Exercises Vertex AI Standard mode configuration lifecycle with regional routing."""
        env = {
            "VERTEX_PROJECT": "partner_advisory-apex_global-gcp-prod",
            "VERTEX_LOCATION": "us-central1",
            "VERTEX_USE_ADC": "true",
        }
        agent = create_governed_agent(
            system_instructions="Enterprise Vertex agent.",
            env=env,
            model_name="gemini-3.8-flash",
        )
        self.assertIsNotNone(agent)
        # Verify resolution
        factory = AgentConfigFactory(env=env)
        resolution = factory.resolve_connection()
        self.assertEqual(resolution.strategy, ConnectionStrategy.VERTEX_AI_STANDARD)
        self.assertTrue(resolution.vertex_enabled)
        self.assertEqual(resolution.project_id, "partner_advisory-apex_global-gcp-prod")
        self.assertEqual(resolution.location, "us-central1")


if __name__ == "__main__":
    unittest.main()
