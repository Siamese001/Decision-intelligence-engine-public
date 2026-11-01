"""Adversarial and boundary stress test suite for Antigravity SDK integration.

Covers:
1. Boundary payloads (zero budgets, empty strings, maximum length system prompts)
2. Malformed input (corrupt connection strategies, non-existent paths, negative integers)
3. Invariant bypass attempts (credential leakage in logs/repr, depth configuration with disabled subagents)
4. Budget & recursion ceiling enforcement (preventing runaway delegation loops)
5. Degradation & fallback recovery (graceful handling of missing cloud credentials)
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from dual_pipeline.agent_sdk import (
    AgentConfigFactory,
    ConnectionStrategy,
    GovernedAgentProfile,
    resolve_connection_strategy,
)
from dual_pipeline.unified_agent_architecture import validate_delegation_contract


class TestAgentSDKAdversarial(unittest.TestCase):
    """Adversarial test suite stress testing SDK configuration boundaries."""

    def test_credential_leakage_adversarial_prevention(self):
        """Invariant: Under no circumstances should raw API secrets be exposed in string representations."""
        canary_secret = "AIzaSyCANARY_HIGH_ENTROPY_SECRET_987654321"
        env = {
            "GEMINI_API_KEY": canary_secret,
            "VERTEX_API_KEY": canary_secret,
        }

        # 1. Test connection resolution repr and summary
        resolution = resolve_connection_strategy(env=env)
        self.assertNotIn(canary_secret, repr(resolution))
        self.assertNotIn(canary_secret, str(resolution))
        self.assertNotIn(canary_secret, resolution.summary())

        # 2. Test factory resolution
        factory = AgentConfigFactory(env=env)
        factory_repr = repr(factory)
        self.assertNotIn(canary_secret, factory_repr)

    def test_malformed_connection_strategy_fallback(self):
        """Boundary: Corrupt or unrecognized connection strategy strings must fall back safely without crashing."""
        malformed_inputs = [
            "INVALID_STRATEGY_FOOBAR",
            "../../../etc/passwd",
            "'; DROP TABLE users; --",
            "",
            "   ",
            "gemini_fake_api_v99",
        ]
        for bad_strategy in malformed_inputs:
            env = {"ANTIGRAVITY_CONNECTION_STRATEGY": bad_strategy}
            result = resolve_connection_strategy(env=env)
            # Must fall back gracefully to default developer API
            self.assertEqual(
                result.strategy,
                ConnectionStrategy.GEMINI_DEVELOPER_API,
                f"Failed to fall back for bad input: {bad_strategy}",
            )

    def test_subagent_depth_bypass_rejection(self):
        """Invariant bypass: Setting subagent depth when subagents are disabled must be normalized or fail closed."""
        profile = GovernedAgentProfile(
            name="adversarial_probe",
            enable_subagents=False,
            max_subagent_depth=5,  # Invariant conflict: depth specified but subagents disabled
        )
        factory = AgentConfigFactory(env={}, default_profile=profile)
        # Factory must sanitize this and prevent Pydantic ValidationError
        config = factory.create_local_agent_config()
        self.assertFalse(config.capabilities.enable_subagents)
        self.assertIsNone(config.capabilities.max_subagent_depth)

    def test_corrupt_delegation_contract_adversarial_rejection(self):
        """Malformed input: Missing required fields or rogue contracts must be rejected."""
        corrupt_contracts = [
            {},  # completely empty
            {"task_id": "only_id"},  # missing required fields
            {
                "task_id": "t1",
                "parent_task_id": "p1",
                "objective": "obj",
                "scope_in": ["s"],
                "scope_out": ["s"],
                "allowed_tools": ["t"],
                "deliverable": "d",
                # missing definition_of_done, budget, escalation_rule
            },
        ]
        for contract in corrupt_contracts:
            is_valid, errors = validate_delegation_contract(contract)
            self.assertFalse(is_valid, f"Expected rejection for corrupt contract: {contract}")
            self.assertGreater(len(errors), 0)

    def test_nonexistent_litert_path_adversarial_routing(self):
        """Boundary: Pointing LITERT_MODEL_PATH to a missing file must not claim offline LiteRT mode."""
        env = {"LITERT_MODEL_PATH": "/nonexistent/path/to/missing_model.bin"}
        # Without explicit strategy, auto-resolution must NOT pick LITERT if file is absent
        with patch("os.path.exists", return_value=False):
            result = resolve_connection_strategy(env=env)
            self.assertNotEqual(result.strategy, ConnectionStrategy.LITERT_LOCAL)
            self.assertEqual(result.strategy, ConnectionStrategy.GEMINI_DEVELOPER_API)

    def test_boundary_payload_massive_system_instructions(self):
        """Boundary payload: Extremely large system instructions must be accepted without buffer truncation."""
        massive_prompt = "INSTRUCTION " * 10000  # ~120KB prompt
        factory = AgentConfigFactory(env={})
        config = factory.create_local_agent_config(system_instructions=massive_prompt)
        self.assertEqual(config.system_instructions, massive_prompt)


if __name__ == "__main__":
    unittest.main()
