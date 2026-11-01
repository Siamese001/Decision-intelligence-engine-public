"""Unit tests for multi-agent supervisor/specialist hierarchy and delegation contracts.

Validates:
- NeeAgentHierarchy defines 3 distinct Tier 1 specialist subagents
- Leaf specialists are strictly constrained from further delegation (enable_subagents=False)
- Tier 0 supervisor config correctly binds all subagents with budget ceilings
- Delegation contracts strictly adhere to required governance fields
"""
from __future__ import annotations

import unittest

from google.antigravity import LocalAgentConfig
from google.antigravity.types import SubagentConfig

from dual_pipeline.agent_sdk import (
    AgentConfigFactory,
    NeeAgentHierarchy,
)


class TestAgentOrchestration(unittest.TestCase):
    """Unit test suite for NeeAgentHierarchy and delegation contracts."""

    def test_specialist_specs_enumeration(self):
        """Hierarchy defines Ingestion, Retrieval, and Spark specialist specs."""
        specs = NeeAgentHierarchy.get_specialist_specs()
        self.assertIn("ingestion_specialist", specs)
        self.assertIn("retrieval_specialist", specs)
        self.assertIn("spark_analytics_specialist", specs)
        self.assertEqual(len(specs), 3)

    def test_subagent_configs_leaf_restrictions(self):
        """All Tier 1 subagent configs must have enable_subagents=False (leaf barrier)."""
        configs = NeeAgentHierarchy.build_subagent_configs()
        self.assertEqual(len(configs), 3)
        for cfg in configs:
            self.assertIsInstance(cfg, SubagentConfig)
            self.assertIsNotNone(cfg.capabilities)
            self.assertEqual(
                cfg.capabilities.allowed_subagents,
                [],
                f"Subagent {cfg.name} must be a leaf without subagent spawning capabilities.",
            )
            self.assertGreater(len(cfg.tools), 0)

    def test_supervisor_config_binds_all_subagents(self):
        """Supervisor config (Tier 0) binds all 3 specialists and enforces max depth."""
        factory = AgentConfigFactory(env={"GEMINI_API_KEY": "dummy-hier-key"})
        supervisor_cfg = NeeAgentHierarchy.build_supervisor_config(factory=factory)

        self.assertIsInstance(supervisor_cfg, LocalAgentConfig)
        self.assertIsNotNone(supervisor_cfg.subagents)
        self.assertEqual(len(supervisor_cfg.subagents), 3)

        subagent_names = [s.name for s in supervisor_cfg.subagents]
        self.assertEqual(
            sorted(subagent_names),
            ["ingestion_specialist", "retrieval_specialist", "spark_analytics_specialist"],
        )
        self.assertEqual(supervisor_cfg.capabilities.max_subagent_depth, 2)
        self.assertTrue(supervisor_cfg.capabilities.enable_subagents)

    def test_valid_delegation_contract_creation(self):
        """Formulates and validates contract-compliant delegation payload."""
        payload, is_valid, errors = NeeAgentHierarchy.create_delegation_contract(
            specialist_name="retrieval_specialist",
            task_id="task_retrieval_001",
            parent_task_id="parent_super_001",
            objective="Retrieve evidence on battery degradation",
            scope_in=["query_knowledge_graph", "evaluate_candidate_retrieval"],
            scope_out=["graph_mutation"],
            deliverable="retrieval_clearance_receipt",
            definition_of_done="11 release gates evaluated and sealed",
            budget={"max_model_calls": 10, "max_tool_calls": 20},
            escalation_rule="Escalate to HITL when ambiguity margin < 15%",
        )
        self.assertTrue(is_valid, f"Contract validation failed: {errors}")
        self.assertEqual(len(errors), 0)
        self.assertEqual(payload["task_id"], "task_retrieval_001")
        self.assertIn("query_knowledge_graph", payload["allowed_tools"])

    def test_delegation_contract_unknown_specialist_raises(self):
        """Attempting to create contract for unknown specialist raises KeyError."""
        with self.assertRaises(KeyError):
            NeeAgentHierarchy.create_delegation_contract(
                specialist_name="rogue_unapproved_agent",
                task_id="task_001",
                parent_task_id="parent_001",
                objective="Test",
                scope_in=[],
                scope_out=[],
                deliverable="receipt",
                definition_of_done="done",
                budget={},
                escalation_rule="rule",
            )


if __name__ == "__main__":
    unittest.main()
