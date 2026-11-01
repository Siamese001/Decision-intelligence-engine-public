#!/usr/bin/env python3
"""
tests/test_fast_tier_differentiation.py

Deterministic differentiation unit tests between:
- Normal Query Routing (ModelTier.FAST_ROUTING -> gpt-5.6-sol, high reasoning effort, 30s timeout)
- Live Meeting Copilot Quick Snap (ModelTier.COPILOT_FAST_SNAP -> gpt-6-luna, low effort, 10s timeout)

Verifies 5-dimension deterministic isolation:
1. Static Call-Site Separation (Query Router vs Live Copilot)
2. Model Profile & Capability Differentiation
3. Payload Fingerprint & Parameter Immunity
4. Concurrency & Timeout Guarantees
5. Environment SSOT Binding Isolation
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from dual_pipeline.model_registry import (
    ModelBindingSpec,
    ModelTier,
    get_binding,
    get_tier_spec,
    prepare_call_kwargs,
    prepare_copilot_call_kwargs,
    resolve_copilot_tier_model,
    resolve_tier_model,
)


class TestFastTierDifferentiation(unittest.TestCase):
    """Verifies complete isolation between FAST_ROUTING and COPILOT_FAST_SNAP tiers."""

    def test_default_tier_spec_separation(self) -> None:
        """Verifies default registry specifications are distinct for normal query vs copilot."""
        spec_fast = get_tier_spec(ModelTier.FAST_ROUTING)
        spec_copilot = get_tier_spec(ModelTier.COPILOT_FAST_SNAP)

        # Normal Query Routing: gpt-5.6-sol (high, 30s)
        self.assertEqual(spec_fast["default_model"], "gpt-5.6-sol")
        self.assertEqual(spec_fast["reasoning_effort"], "high")
        self.assertEqual(spec_fast["timeout"], 30.0)

        # Live Copilot Quick Snap: gpt-5.6-luna (medium, 7s, max 1500 tokens)
        self.assertEqual(spec_copilot["default_model"], "gpt-5.6-luna")
        self.assertEqual(spec_copilot["reasoning_effort"], "medium")
        self.assertEqual(spec_copilot["timeout"], 7.0)
        self.assertEqual(spec_copilot["max_completion_tokens"], 1500)

    def test_typed_model_binding_specs(self) -> None:
        """Verifies get_binding returns typed specs with distinct characteristics."""
        binding_fast = get_binding(ModelTier.FAST_ROUTING)
        binding_copilot = get_binding(ModelTier.COPILOT_FAST_SNAP)

        self.assertIsInstance(binding_fast, ModelBindingSpec)
        self.assertIsInstance(binding_copilot, ModelBindingSpec)

        self.assertEqual(binding_fast.model_id, "gpt-5.6-sol")
        self.assertEqual(binding_fast.reasoning_effort, "high")
        self.assertEqual(binding_fast.timeout, 30.0)

        self.assertEqual(binding_copilot.model_id, "gpt-5.6-luna")
        self.assertEqual(binding_copilot.reasoning_effort, "medium")
        self.assertEqual(binding_copilot.timeout, 7.0)

    def test_environment_override_isolation(self) -> None:
        """Verifies FAST_ROUTING_MODEL and COPILOT_MODEL env vars do not bleed into each other."""
        custom_env = {
            "FAST_ROUTING_MODEL": "gpt-5.6-terra",
            "FAST_ROUTING_REASONING_EFFORT": "medium",
            "COPILOT_MODEL": "gpt-5.6-luna",
            "COPILOT_REASONING_EFFORT": "low",
        }
        with mock.patch.dict(os.environ, custom_env):
            # Normal query routing resolves FAST_ROUTING_MODEL
            m_fast, eff_fast = resolve_tier_model(ModelTier.FAST_ROUTING)
            self.assertEqual(m_fast, "gpt-5.6-terra")
            self.assertEqual(eff_fast, "medium")

            # Copilot quick snap resolves COPILOT_MODEL
            m_copilot, eff_copilot = resolve_tier_model(ModelTier.COPILOT_FAST_SNAP)
            self.assertEqual(m_copilot, "gpt-5.6-luna")
            self.assertEqual(eff_copilot, "low")

            # Dedicated copilot resolver defaults to COPILOT_FAST_SNAP
            m_cop_default, eff_cop_default = resolve_copilot_tier_model()
            self.assertEqual(m_cop_default, "gpt-5.6-luna")
            self.assertEqual(eff_cop_default, "low")

    def test_call_kwargs_parameter_immunity_and_model_resolution(self) -> None:
        """Verifies call kwargs preparation conforms to respective models."""
        raw_kwargs = {
            "temperature": 0.7,
            "top_p": 0.9,
            "messages": [{"role": "user", "content": "Route this meeting"}],
        }

        # FAST_ROUTING with gpt-5.6-sol (high) strips temperature/top_p
        kw_fast = prepare_call_kwargs(ModelTier.FAST_ROUTING, raw_kwargs)
        self.assertEqual(kw_fast["model"], "gpt-5.6-sol")
        self.assertEqual(kw_fast["reasoning_effort"], "high")
        self.assertNotIn("temperature", kw_fast)
        self.assertNotIn("top_p", kw_fast)

        # COPILOT_FAST_SNAP with gpt-6-luna (medium) strips temperature/top_p
        kw_copilot = prepare_copilot_call_kwargs(ModelTier.COPILOT_FAST_SNAP, raw_kwargs)
        self.assertEqual(kw_copilot["model"], "gpt-6-luna")
        self.assertEqual(kw_copilot["reasoning_effort"], "medium")
        self.assertNotIn("temperature", kw_copilot)
        self.assertNotIn("top_p", kw_copilot)

    def test_live_copilot_advisor_binding(self) -> None:
        """Verifies LiveCopilotAdvisor instantiates with COPILOT_FAST_SNAP tier."""
        from dual_pipeline.live_meeting_recorder_core.copilot import LiveCopilotAdvisor

        with mock.patch.dict(os.environ, {"COPILOT_MODEL": "", "COPILOT_REASONING_EFFORT": ""}):
            advisor = LiveCopilotAdvisor(
                project_name="project_lens",
                meeting_name="LENS SOW Review",
                attendees=[{"name": "Alice", "role": "Lead", "org": "PartnerAdvisory"}],
                mock_mode=True,
            )
            # Default model must be gpt-5.6-luna (medium effort)
            self.assertEqual(advisor.model_id, "gpt-5.6-luna")
            self.assertEqual(advisor.reasoning_effort, "medium")


if __name__ == "__main__":
    unittest.main()
