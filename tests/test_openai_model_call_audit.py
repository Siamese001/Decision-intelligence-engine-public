"""tests/test_openai_model_call_audit.py

Wave 1: Verification audit of OpenAI model resolution, model registry tier bindings,
and parameter immunity enforcement.
"""
from __future__ import annotations

import unittest
from typing import Any, Dict

from config.credentials import (
    OPENAI_APPROVED_MODELS,
    canonicalize_model_name,
    sanitize_model_call_kwargs,
)
from dual_pipeline.model_registry import (
    BANNED_REGISTRY_MODELS,
    ModelTier,
    get_tier_spec,
    resolve_copilot_tier_model,
)
from pipeline_a_intake.agents.multimodal_slide_agent import MultimodalSlideExtractor


class TestOpenAIModelCallAudit(unittest.TestCase):
    """Verifies that all OpenAI model calls strictly adhere to approved SSOT models and parameter immunity."""

    def test_approved_models_set(self) -> None:
        """Asserts approved OpenAI model family contains only sanctioned models."""
        expected = {
            "gpt-6.1-sol",
            "gpt-6-sol",
            "gpt-6-luna",
            "gpt-5.6-sol",
            "gpt-5.6-terra",
            "gpt-5.6-luna",
        }
        self.assertEqual(OPENAI_APPROVED_MODELS, expected)

    def test_banned_models_prohibited(self) -> None:
        """Asserts banned legacy and unapproved models are flagged in BANNED_REGISTRY_MODELS."""
        for banned in ("gpt-4o", "gpt-4o-mini", "o1", "o3", "gpt-4.1"):
            self.assertIn(banned, BANNED_REGISTRY_MODELS)
            self.assertNotIn(banned, OPENAI_APPROVED_MODELS)

    def test_copilot_tier_resolution(self) -> None:
        """Asserts COPILOT_FAST_SNAP resolves to an approved OpenAI model and reasoning effort."""
        model_id, effort = resolve_copilot_tier_model(ModelTier.COPILOT_FAST_SNAP)
        self.assertIn(model_id, OPENAI_APPROVED_MODELS)
        self.assertIn(effort, ("low", "medium", "high", "xhigh", "max"))

    def test_frontier_tiers_resolution(self) -> None:
        """Asserts frontier reasoning tiers resolve to approved OpenAI models."""
        for tier in (
            ModelTier.FRONTIER_HIGH_REASONING,
            ModelTier.FRONTIER_REASONING,
            ModelTier.SURGICAL_HEALER,
            ModelTier.DIALECTICAL_JUDGE,
        ):
            spec = get_tier_spec(tier)
            def_m = spec.get("default_model")
            self.assertIn(def_m, OPENAI_APPROVED_MODELS)
            self.assertEqual(spec.get("provider"), "openai")

    def test_multimodal_slide_agent_binding(self) -> None:
        """Asserts MultimodalSlideExtractor agent card binds an approved tier model."""
        agent = MultimodalSlideExtractor(mock_mode=True)
        self.assertIn(agent.card.model_binding.model_id, OPENAI_APPROVED_MODELS | {"mlx-community/Qwen2.5-VL-7B-Instruct-4bit"})

    def test_parameter_immunity_strips_forbidden_kwargs(self) -> None:
        """Asserts parameter immunity strips temperature, top_p, and penalty parameters for reasoning models."""
        for m in ("gpt-6-luna", "gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-terra"):
            dirty_kwargs: Dict[str, Any] = {
                "model": m,
                "temperature": 0.7,
                "top_p": 0.95,
                "presence_penalty": 0.5,
                "frequency_penalty": 0.5,
                "max_tokens": 1200,
                "reasoning_effort": "high",
            }
            cleaned = sanitize_model_call_kwargs(m, dirty_kwargs)
            self.assertNotIn("temperature", cleaned)
            self.assertNotIn("top_p", cleaned)
            self.assertNotIn("presence_penalty", cleaned)
            self.assertNotIn("frequency_penalty", cleaned)
            self.assertNotIn("max_tokens", cleaned)
            self.assertEqual(cleaned.get("max_completion_tokens"), 1200)
            self.assertEqual(cleaned.get("reasoning_effort"), "high")

    def test_canonicalize_model_name(self) -> None:
        """Asserts colloquial aliases map to canonical approved model identifiers."""
        self.assertEqual(canonicalize_model_name("gpt-6.1"), "gpt-6.1-sol")
        self.assertEqual(canonicalize_model_name("gpt-6-sol"), "gpt-6-sol")
        self.assertEqual(canonicalize_model_name("sol"), "gpt-6.1-sol")
        self.assertEqual(canonicalize_model_name("luna"), "gpt-6-luna")
        self.assertEqual(canonicalize_model_name("terra"), "gpt-5.6-terra")


if __name__ == "__main__":
    unittest.main()
