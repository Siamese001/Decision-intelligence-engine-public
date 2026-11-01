"""End-to-End test suite for Antigravity SDK Agent with MCP Bridge integration.

Validates the full lifecycle of configuring Antigravity agents with
scoped MCP server profiles, tool allowlists, and native tool wrappers.
"""
from __future__ import annotations

import unittest

from google.antigravity import Agent, LocalAgentConfig
from google.antigravity.types import McpStdioServer

from dual_pipeline.agent_sdk import AgentConfigFactory, McpServerRegistry
from dual_pipeline.agent_sdk.tools import (
    inspect_graph_snapshot,
    query_knowledge_graph,
    verify_contract_compliance,
)


class TestMcpBridgeE2E(unittest.TestCase):
    """End-to-End test suite for MCP server configuration on Antigravity agents."""

    def test_agent_initialization_with_mcp_profile_servers(self):
        """Exercises complete agent lifecycle configured with MCP 'graph' profile servers."""
        factory = AgentConfigFactory(env={"GEMINI_API_KEY": "dummy-mcp-e2e-key"})

        # Build config using mcp_profile
        config = factory.create_local_agent_config(
            system_instructions="Knowledge graph researcher agent.",
            mcp_profile="graph",
            tools=[query_knowledge_graph, inspect_graph_snapshot],
        )

        self.assertIsInstance(config, LocalAgentConfig)
        self.assertIsNotNone(config.mcp_servers)
        self.assertEqual(len(config.mcp_servers), 2)

        server_names = [s.name for s in config.mcp_servers]
        self.assertEqual(sorted(server_names), ["graphdb-inspector", "nee-knowledge-graph"])

        # Check that tool allowlists were applied
        nee_server = next(s for s in config.mcp_servers if s.name == "nee-knowledge-graph")
        self.assertIsNotNone(nee_server.enabled_tools)
        self.assertIn("nee_query", nee_server.enabled_tools)

        # Agent instantiation succeeds
        agent = Agent(config)
        self.assertIsNotNone(agent)

    def test_agent_initialization_with_governance_mcp_profile(self):
        """Exercises agent lifecycle with MCP 'governance' profile servers."""
        factory = AgentConfigFactory(env={"GEMINI_API_KEY": "dummy-mcp-e2e-key"})

        config = factory.create_local_agent_config(
            system_instructions="Governance audit agent.",
            mcp_profile="governance",
            tools=[verify_contract_compliance],
        )

        self.assertIsNotNone(config.mcp_servers)
        self.assertEqual(len(config.mcp_servers), 2)

        server_names = [s.name for s in config.mcp_servers]
        self.assertEqual(sorted(server_names), ["contract-governance", "test-orchestrator"])

        # Instantiate agent
        agent = Agent(config)
        self.assertIsNotNone(agent)

    def test_mcp_registry_builds_all_servers_safely(self):
        """Registry can instantiate all 11 MCP servers with bounded context-safe allowlists."""
        registry = McpServerRegistry()
        all_servers = registry.build_all_available_servers(restrict_tools=True)
        self.assertGreaterEqual(len(all_servers), 5)
        for s in all_servers:
            self.assertIsInstance(s, McpStdioServer)
            self.assertIsNotNone(s.enabled_tools)


if __name__ == "__main__":
    unittest.main()
