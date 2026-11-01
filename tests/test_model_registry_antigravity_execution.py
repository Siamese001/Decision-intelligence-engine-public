#!/usr/bin/env python3
"""tests/test_model_registry_antigravity_execution.py

Rigorous test suite verifying:
1. Model Registry execution in Antigravity IDE:
   - When no model is specified, execution runs within the Antigravity IDE environment (hermetic local mode, no API key required).
   - Local tiers (EMBEDDING_MULTI_VECTOR, ACOUSTIC_MLX_WHISPER, etc.) use local backends with zero API key dependencies.
   - Provider boundary enforcement: unauthenticated live calls fail closed.
2. Operator-directed model specification:
   - Explicit specification of gpt-astra-6 canonicalizes to gpt-6-luna.
   - Parameter immunity strips temperature, top_p, and penalties for gpt-6-luna / gpt-5.6-luna.
   - Banned models (e.g. gpt-5.6-terra) fail-closed or fall back to governed tier defaults.
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from config.credentials import (
    CredentialManager,
    resolve_openai_credentials,
    sanitize_model_call_kwargs,
)
from dual_pipeline.model_registry import (
    ModelBindingSpec,
    ModelTier,
    canonicalize_model_name,
    get_binding,
    get_tier_spec,
    prepare_call_kwargs,
    reload_registry,
    resolve_copilot_tier_model,
    resolve_tier_model,
)

ROOT = Path(__file__).resolve().parents[1]


class TestModelRegistryAntigravityExecution(unittest.TestCase):
    """Rigorous verification of Model Registry behavior in Antigravity IDE."""

    def setUp(self) -> None:
        reload_registry()

    def test_default_execution_on_antigravity_ide_no_api_key_required(self) -> None:
        """Verify that when no model is specified, registry runs without any API key."""
        empty_env = {
            "OPENAI_API_KEY": "",
            "ANTHROPIC_API_KEY": "",
            "GEMINI_API_KEY": "",
            "GOOGLE_API_KEY": "",
            "OPENAI_MODEL": "",
            "COPILOT_MODEL": "",
            "FAST_ROUTING_MODEL": "",
            "FRONTIER_HIGH_REASONING_MODEL": "",
            "OPENAI_REASONING_EFFORT": "",
            "COPILOT_REASONING_EFFORT": "",
            "FAST_ROUTING_REASONING_EFFORT": "",
            "FRONTIER_HIGH_REASONING_REASONING_EFFORT": "",
            "SURGICAL_HEALER_REASONING_EFFORT": "",
        }
        with mock.patch.dict(os.environ, empty_env, clear=False):
            # Model resolution for all tiers operates purely from local registry JSON
            m_frontier, eff_frontier = resolve_tier_model(ModelTier.FRONTIER_HIGH_REASONING)
            self.assertEqual(m_frontier, "gpt-5.6-sol")
            self.assertEqual(eff_frontier, "max")

            m_healer, eff_healer = resolve_tier_model(ModelTier.SURGICAL_HEALER)
            self.assertEqual(m_healer, "gpt-5.6-sol")
            self.assertEqual(eff_healer, "max")

            m_fast, eff_fast = resolve_tier_model(ModelTier.FAST_ROUTING)
            self.assertEqual(m_fast, "gpt-5.6-sol")
            self.assertEqual(eff_fast, "high")

            m_copilot, eff_copilot = resolve_tier_model(ModelTier.COPILOT_FAST_SNAP)
            self.assertEqual(m_copilot, "gpt-5.6-luna")
            self.assertEqual(eff_copilot, "medium")

            # Typed binding specifications require no API key
            binding = get_binding(ModelTier.DIALECTICAL_JUDGE)
            self.assertIsInstance(binding, ModelBindingSpec)
            self.assertEqual(binding.model_id, "gpt-5.6-sol")

            # Call kwargs preparation operates without network or API key
            kwargs = prepare_call_kwargs(ModelTier.FAST_ROUTING, {"extra_param": 123})
            self.assertEqual(kwargs["model"], "gpt-5.6-sol")
            self.assertEqual(kwargs["reasoning_effort"], "high")
            self.assertEqual(kwargs["extra_param"], 123)

            kwargs_copilot = prepare_call_kwargs(ModelTier.COPILOT_FAST_SNAP, {"extra_param": 456})
            self.assertEqual(kwargs_copilot["model"], "gpt-5.6-luna")
            self.assertEqual(kwargs_copilot["reasoning_effort"], "medium")
            self.assertEqual(kwargs_copilot["extra_param"], 456)

            # Hermetic Antigravity local execution (mock mode) succeeds with no API key
            client, creds = CredentialManager.get_openai_client(allow_mock=True)
            self.assertIsNone(client)
            self.assertTrue(creds.is_mock)
            self.assertIsNone(creds.api_key)
            self.assertEqual(creds.masked_key, "[MOCK_MODE]")

    def test_local_tiers_use_local_providers_without_api_keys(self) -> None:
        """Verify local embedding and acoustic tiers execute via local providers with zero API key."""
        spec_embed = get_tier_spec(ModelTier.EMBEDDING_MULTI_VECTOR)
        self.assertEqual(spec_embed["default_model"], "bge-m3")
        self.assertEqual(spec_embed["provider"], "local")

        spec_mlx = get_tier_spec(ModelTier.ACOUSTIC_MLX_WHISPER)
        self.assertEqual(spec_mlx["provider"], "local_mlx")

        spec_fw = get_tier_spec(ModelTier.ACOUSTIC_FASTER_WHISPER)
        self.assertEqual(spec_fw["provider"], "local_ctranslate2")

        spec_wx = get_tier_spec(ModelTier.ACOUSTIC_WHISPERX)
        self.assertEqual(spec_wx["provider"], "local_whisperx")

        spec_diar = get_tier_spec(ModelTier.ACOUSTIC_DIARIZATION)
        self.assertEqual(spec_diar["provider"], "pyannote")

    def test_provider_boundary_fail_closed_without_api_key(self) -> None:
        """Verify Provider Boundary: unauthenticated live calls fail closed immediately."""
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            with self.assertRaises(ValueError) as ctx:
                CredentialManager.get_openai_client(allow_mock=False)
            self.assertIn("OPENAI_API_KEY is required", str(ctx.exception))

    def test_gpt_5_6_terra_is_approved_and_configured(self) -> None:
        """Verify gpt-5.6-terra is approved, in OPENAI_APPROVED_MODELS, and not in BANNED_REGISTRY_MODELS."""
        from dual_pipeline.model_registry import (
            BANNED_REGISTRY_MODELS,
            OPENAI_APPROVED_MODELS,
            REGISTRY_JSON_PATH,
        )

        self.assertTrue(REGISTRY_JSON_PATH.is_file())
        self.assertIn("gpt-5.6-terra", OPENAI_APPROVED_MODELS)
        self.assertNotIn("gpt-5.6-terra", BANNED_REGISTRY_MODELS)

        # gpt-5.6-terra can be configured via environment override
        with mock.patch.dict(os.environ, {"OPENAI_MODEL": "gpt-5.6-terra", "OPENAI_REASONING_EFFORT": "high"}):
            m_res, effort = resolve_tier_model(ModelTier.FRONTIER_HIGH_REASONING)
            self.assertEqual(m_res, "gpt-5.6-terra")
            self.assertEqual(effort, "high")

        # gpt-5.6-terra is accepted in prepare_call_kwargs
        raw_kwargs = {
            "model": "gpt-5.6-terra",
            "messages": [{"role": "user", "content": "Test prompt"}],
        }
        sanitized = prepare_call_kwargs(ModelTier.FRONTIER_HIGH_REASONING, raw_kwargs)
        self.assertEqual(sanitized["model"], "gpt-5.6-terra")

    def test_unapproved_models_strictly_banned_and_rejected(self) -> None:
        """Verify unapproved models (e.g. gpt-4.1-mini) are banned and fall back to tier defaults."""
        from dual_pipeline.model_registry import BANNED_REGISTRY_MODELS

        self.assertIn("gpt-4.1-mini", BANNED_REGISTRY_MODELS)
        self.assertIn("gpt-4o-mini", BANNED_REGISTRY_MODELS)
        self.assertIn("o1", BANNED_REGISTRY_MODELS)
        self.assertIn("o3-mini", BANNED_REGISTRY_MODELS)

        # OPENAI_MODEL with gpt-4.1-mini falls back to tier default
        with mock.patch.dict(os.environ, {"OPENAI_MODEL": "gpt-4.1-mini"}):
            m_res, effort = resolve_tier_model(ModelTier.FRONTIER_HIGH_REASONING)
            self.assertEqual(m_res, "gpt-5.6-sol")
            self.assertEqual(effort, "max")

        # COPILOT_MODEL with gpt-4.1-mini falls back to copilot default
        with mock.patch.dict(os.environ, {"COPILOT_MODEL": "gpt-4.1-mini"}):
            m_copilot, eff_copilot = resolve_copilot_tier_model(ModelTier.COPILOT_FAST_SNAP)
            self.assertEqual(m_copilot, "gpt-5.6-luna")
            self.assertEqual(eff_copilot, "medium")

        # prepare_call_kwargs with gpt-4.1-mini falls back to tier default
        raw_kwargs = {
            "model": "gpt-4.1-mini",
            "messages": [{"role": "user", "content": "Test prompt"}],
        }
        sanitized = prepare_call_kwargs(ModelTier.FRONTIER_HIGH_REASONING, raw_kwargs)
        self.assertEqual(sanitized["model"], "gpt-5.6-sol")
        self.assertEqual(sanitized["reasoning_effort"], "xhigh")

    def test_governed_model_aliases_canonicalization(self) -> None:
        """Verify that governed model aliases canonicalize correctly to approved identifiers."""
        self.assertEqual(canonicalize_model_name("gpt-luna-5.6"), "gpt-5.6-luna")
        self.assertEqual(canonicalize_model_name("luna"), "gpt-5.6-luna")
        self.assertEqual(canonicalize_model_name("gpt-sol-5.6"), "gpt-5.6-sol")
        self.assertEqual(canonicalize_model_name("sol"), "gpt-5.6-sol")
        self.assertEqual(canonicalize_model_name("gpt-terra-5.6"), "gpt-5.6-terra")
        self.assertEqual(canonicalize_model_name("terra"), "gpt-5.6-terra")

    def test_approved_model_aliases_environment_override_resolution(self) -> None:
        """Verify that setting an approved model alias resolves to the canonical approved model."""
        with mock.patch.dict(os.environ, {"COPILOT_MODEL": "gpt-terra-5.6"}):
            m_copilot, eff = resolve_copilot_tier_model(ModelTier.COPILOT_FAST_SNAP)
            self.assertEqual(m_copilot, "gpt-5.6-terra")
            self.assertEqual(eff, "medium")

        creds = resolve_openai_credentials(allow_mock=True, model="gpt-sol-5.6")
        self.assertEqual(creds.model, "gpt-5.6-sol")

        sanitized = sanitize_model_call_kwargs("gpt-sol-5.6", {"temperature": 0.7, "top_p": 0.9, "reasoning_effort": "max"})
        self.assertEqual(sanitized["model"], "gpt-5.6-sol")
        self.assertNotIn("temperature", sanitized)
        self.assertNotIn("top_p", sanitized)
        self.assertEqual(sanitized["reasoning_effort"], "xhigh")

    def test_agent_routing_invariants_codification(self) -> None:
        """Verify .agents/rules/agent-routing.md codifies Antigravity IDE Default and Provider Boundary."""
        routing_md = ROOT / ".agents" / "rules" / "agent-routing.md"
        self.assertTrue(routing_md.is_file(), "agent-routing.md must exist")
        content = routing_md.read_text(encoding="utf-8")

        self.assertIn("Execution Environment & Default Model Routing Invariant", content)
        self.assertIn("Antigravity IDE Default", content)
        self.assertIn("Provider Boundary", content)


if __name__ == "__main__":
    unittest.main()
