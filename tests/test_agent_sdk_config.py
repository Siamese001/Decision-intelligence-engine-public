"""Unit tests for Antigravity SDK agent configuration and connection resolution.

Validates:
- AgentConfigFactory produces contract-valid configurations
- Connection resolution correctly routes across Gemini, Vertex AI, and LiteRT
- Governed budget and compaction defaults are applied
- Credential isolation guarantees (no raw keys in summaries or representations)
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from google.antigravity import AgentBehavior, LiteRTAgentConfig, LocalAgentConfig

from dual_pipeline.agent_sdk.config import (
    DEFAULT_COMPACTION_TOKEN_THRESHOLD,
    DEFAULT_MAX_MODEL_CALLS,
    DEFAULT_MAX_SUBAGENT_DEPTH,
    DEFAULT_MAX_TOOL_CALLS,
    AgentConfigFactory,
    GovernedAgentProfile,
)
from dual_pipeline.agent_sdk.connection import (
    ConnectionStrategy,
    resolve_connection_strategy,
)


class TestAgentSDKConfig(unittest.TestCase):
    """Unit test suite for AgentConfigFactory and connection strategies."""

    def test_default_connection_resolution_gemini_api(self):
        """Resolves to Gemini Developer API by default when cloud credentials absent."""
        env = {}
        result = resolve_connection_strategy(env=env)
        self.assertEqual(result.strategy, ConnectionStrategy.GEMINI_DEVELOPER_API)
        self.assertTrue(result.is_cloud)
        self.assertFalse(result.is_offline)
        self.assertFalse(result.vertex_enabled)
        self.assertFalse(result.has_api_key)
        self.assertEqual(result.model_name, "gemini-3.8-flash")

    def test_connection_resolution_with_gemini_key(self):
        """Resolves Gemini Developer API with has_api_key=True without leaking key."""
        secret_key = "AIzaSyDUMMYSECRETKEY123456789"
        env = {"GEMINI_API_KEY": secret_key}
        result = resolve_connection_strategy(env=env)
        self.assertEqual(result.strategy, ConnectionStrategy.GEMINI_DEVELOPER_API)
        self.assertTrue(result.has_api_key)
        # Verify credential isolation invariant: secret is not in summary or repr
        self.assertNotIn(secret_key, result.summary())
        self.assertNotIn(secret_key, repr(result))

    def test_connection_resolution_vertex_standard(self):
        """Resolves to Vertex AI Standard Mode when project and ADC flag are set."""
        env = {
            "VERTEX_PROJECT": "test-gcp-project",
            "VERTEX_LOCATION": "us-east4",
            "VERTEX_USE_ADC": "true",
        }
        result = resolve_connection_strategy(env=env)
        self.assertEqual(result.strategy, ConnectionStrategy.VERTEX_AI_STANDARD)
        self.assertTrue(result.is_cloud)
        self.assertTrue(result.vertex_enabled)
        self.assertEqual(result.project_id, "test-gcp-project")
        self.assertEqual(result.location, "us-east4")

    def test_connection_resolution_vertex_express(self):
        """Resolves to Vertex AI Express Mode when project and API key are set."""
        env = {
            "VERTEX_PROJECT": "test-gcp-project",
            "VERTEX_LOCATION": "us-central1",
            "VERTEX_API_KEY": "dummy-vertex-key",
        }
        result = resolve_connection_strategy(env=env)
        self.assertEqual(result.strategy, ConnectionStrategy.VERTEX_AI_EXPRESS)
        self.assertTrue(result.vertex_enabled)
        self.assertTrue(result.has_api_key)

    def test_connection_resolution_litert_offline(self):
        """Resolves to LiteRT offline local mode when model file exists."""
        with patch("os.path.exists", return_value=True):
            env = {"LITERT_MODEL_PATH": "/models/gemma-4-26b.bin"}
            result = resolve_connection_strategy(env=env)
            self.assertEqual(result.strategy, ConnectionStrategy.LITERT_LOCAL)
            self.assertFalse(result.is_cloud)
            self.assertTrue(result.is_offline)
            self.assertFalse(result.vertex_enabled)
            self.assertEqual(result.model_name, "gemma-4-26b.bin")

    def test_factory_creates_local_agent_config_with_governed_defaults(self):
        """Factory applies default budget, compaction, and capabilities."""
        env = {"GEMINI_API_KEY": "dummy-key"}
        factory = AgentConfigFactory(env=env)

        config = factory.create_local_agent_config(
            system_instructions="You are a governed Decision Intelligence agent.",
        )
        self.assertIsInstance(config, LocalAgentConfig)
        self.assertEqual(config.model, "gemini-3.8-flash")
        self.assertEqual(config.system_instructions, "You are a governed Decision Intelligence agent.")

        # Budget assertions
        self.assertIsNotNone(config.budget_config)
        self.assertEqual(config.budget_config.max_model_calls, DEFAULT_MAX_MODEL_CALLS)
        self.assertEqual(config.budget_config.max_tool_calls, DEFAULT_MAX_TOOL_CALLS)

        # Compaction assertions
        self.assertIsNotNone(config.compaction_config)
        self.assertEqual(config.compaction_config.token_threshold, DEFAULT_COMPACTION_TOKEN_THRESHOLD)

        # Capabilities assertions
        self.assertIsNotNone(config.capabilities)
        self.assertEqual(config.capabilities.max_subagent_depth, DEFAULT_MAX_SUBAGENT_DEPTH)
        self.assertEqual(config.capabilities.agent_behavior, AgentBehavior.AUTONOMOUS)

    def test_factory_custom_governed_profile(self):
        """Custom profile overrides defaults cleanly."""
        custom_profile = GovernedAgentProfile(
            name="strict_evaluator",
            behavior=AgentBehavior.INTERACTIVE,
            max_model_calls=15,
            max_tool_calls=30,
            token_compaction_threshold=40000,
            max_subagent_depth=1,
            enable_subagents=False,
        )
        factory = AgentConfigFactory(env={}, default_profile=custom_profile)
        config = factory.create_local_agent_config()

        self.assertEqual(config.budget_config.max_model_calls, 15)
        self.assertEqual(config.budget_config.max_tool_calls, 30)
        self.assertEqual(config.compaction_config.token_threshold, 40000)
        self.assertIsNone(config.capabilities.max_subagent_depth)
        self.assertEqual(config.capabilities.agent_behavior, AgentBehavior.INTERACTIVE)
        self.assertFalse(config.capabilities.enable_subagents)

    def test_factory_creates_litert_config(self):
        """Factory instantiates LiteRTAgentConfig with on-device settings."""
        factory = AgentConfigFactory(env={"LITERT_MODEL_PATH": "/models/gemma-4-26b.bin"})
        config = factory.create_litert_agent_config()
        self.assertIsInstance(config, LiteRTAgentConfig)
        self.assertEqual(config.model_path, "/models/gemma-4-26b.bin")
        self.assertEqual(config.backend, "cpu")


if __name__ == "__main__":
    unittest.main()
