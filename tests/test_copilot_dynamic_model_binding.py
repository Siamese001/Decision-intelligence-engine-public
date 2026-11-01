#!/usr/bin/env python3
"""
tests/test_copilot_dynamic_model_binding.py

Unit test suite verifying:
1. Dynamic model resolution for Copilot real-time tasks (FAST_ROUTING and FRONTIER_REASONING)
   sourced from env_di SSOT without hardcoded model strings in code.
2. Parameter immunity enforcement (temperature=None, top_p stripped).
3. Dynamic model flexing via environment configuration.
4. MECE succinct briefing synthesis in CopilotFusionEngine.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from dual_pipeline.decision.context_fusion import (
    CopilotFusionEngine,
)
from dual_pipeline.model_registry import (
    ModelTier,
    prepare_copilot_call_kwargs,
    resolve_copilot_tier_model,
)


class MockRetrievalEngine:
    """Mock retrieval engine returning client-safe and internal-advisory facts."""
    def search(self, query: str, stealth_mode: bool = True):
        class MockFact:
            def __init__(self, node_id: str, name: str, namespace: str, snippet: str = ""):
                self.node_id = node_id
                self.name = name
                self.namespace = namespace
                self.type = "ArchitectureDecision"
                self.snippet = snippet

            def to_dict(self):
                return {
                    "node_id": self.node_id,
                    "name": self.name,
                    "namespace": self.namespace,
                    "snippet": self.snippet,
                }

        facts = [
            MockFact(
                node_id="canonical_1",
                name="LENS Nameplate Extraction Rule Architecture",
                namespace="CLIENT_SAFE",
                snippet="Rule-driven extraction targeting Serial Number, Model, and Manufacturer",
            ),
        ]
        if not stealth_mode:
            facts.append(
                MockFact(
                    node_id="advisory_1",
                    name="Atlas Executive Delivery Trajectory",
                    namespace="INTERNAL_ADVISORY",
                    snippet="Internal positioning on warehouse transformation ROI",
                )
            )
        return facts


class TestCopilotDynamicModelBinding(unittest.TestCase):
    """Verifies copilot real-time tasks bind dynamically to env_di without hardcoding."""

    def setUp(self):
        super().setUp()
        self._env_patch = mock.patch.dict(
            os.environ,
            {"COPILOT_MODEL": "gpt-6-luna", "COPILOT_REASONING_EFFORT": "medium"},
        )
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()
        super().tearDown()

    def test_copilot_resolves_gpt_6_astra_from_env_di(self):
        """Verifies COPILOT_MODEL from env_di resolves to gpt-6-luna for FAST_ROUTING and FRONTIER_REASONING."""
        model_fast, effort_fast = resolve_copilot_tier_model(ModelTier.FAST_ROUTING)
        self.assertEqual(model_fast, "gpt-6-luna")
        self.assertEqual(effort_fast, "medium")

        model_frontier, effort_frontier = resolve_copilot_tier_model(ModelTier.FRONTIER_REASONING)
        self.assertEqual(model_frontier, "gpt-6-luna")
        self.assertEqual(effort_frontier, "medium")

    def test_copilot_parameter_immunity_enforced(self):
        """Verifies parameter immunity strips temperature and top_p for reasoning models."""
        raw_kwargs = {
            "temperature": 0.7,
            "top_p": 0.9,
            "presence_penalty": 0.2,
        }
        kwargs_fast = prepare_copilot_call_kwargs(ModelTier.FAST_ROUTING, raw_kwargs)
        self.assertEqual(kwargs_fast["model"], "gpt-6-luna")
        self.assertEqual(kwargs_fast["reasoning_effort"], "medium")
        self.assertNotIn("temperature", kwargs_fast)
        self.assertNotIn("top_p", kwargs_fast)
        self.assertNotIn("presence_penalty", kwargs_fast)

        kwargs_frontier = prepare_copilot_call_kwargs(ModelTier.FRONTIER_REASONING, raw_kwargs)
        self.assertEqual(kwargs_frontier["model"], "gpt-6-luna")
        self.assertEqual(kwargs_frontier["reasoning_effort"], "medium")
        self.assertNotIn("temperature", kwargs_frontier)
        self.assertNotIn("top_p", kwargs_frontier)

    def test_dynamic_model_flexing_without_code_changes(self):
        """Verifies the operator can flex models dynamically via COPILOT_MODEL without modifying code."""
        # Flex to gpt-5.6-sol
        with mock.patch.dict(os.environ, {"COPILOT_MODEL": "gpt-5.6-sol", "COPILOT_REASONING_EFFORT": "high"}):
            model, effort = resolve_copilot_tier_model(ModelTier.FAST_ROUTING)
            self.assertEqual(model, "gpt-5.6-sol")
            self.assertEqual(effort, "high")

            kw = prepare_copilot_call_kwargs(ModelTier.FAST_ROUTING)
            self.assertEqual(kw["model"], "gpt-5.6-sol")
            self.assertEqual(kw["reasoning_effort"], "high")
            self.assertNotIn("temperature", kw)
            self.assertNotIn("top_p", kw)

        # Flex to gemini-3.8-flash (high reasoning model)
        with mock.patch.dict(os.environ, {"COPILOT_MODEL": "gemini-3.8-flash"}):
            model_flash, _ = resolve_copilot_tier_model(ModelTier.FAST_ROUTING)
            self.assertEqual(model_flash, "gemini-3.8-flash")

            kw_flash = prepare_copilot_call_kwargs(ModelTier.FAST_ROUTING)
            self.assertEqual(kw_flash["model"], "gemini-3.8-flash")
            self.assertNotIn("temperature", kw_flash)

    def test_copilot_briefing_synthesis_mece_and_parameter_immune(self):
        """Verifies CopilotFusionEngine synthesizes MECE bullets under dynamic model binding."""
        retrieval = MockRetrievalEngine()
        fusion = CopilotFusionEngine(retrieval_engine=retrieval)

        live_events = [
            {
                "speaker": "harper Hoffman",
                "text": "The rule creation tool had an issue earlier this week in Dev.",
                "timestamp": "04:40:00",
            }
        ]

        payload = fusion.fuse_context(
            query="Why is LENS Phase 1 not completed?",
            live_fast_path_events=live_events,
            stealth_mode=True,
        )

        briefing = fusion.synthesize_copilot_briefing(
            payload=payload,
            tier=ModelTier.FAST_ROUTING,
            max_bullets=6,
        )

        self.assertEqual(briefing["tier"], "FAST_ROUTING")
        self.assertEqual(briefing["resolved_model"], "gpt-6-luna")
        self.assertEqual(briefing["reasoning_effort"], "medium")
        self.assertTrue(briefing["is_parameter_immune"])
        self.assertLessEqual(len(briefing["mece_bullets"]), 6)
        self.assertIn("Settled Baseline", briefing["formatted_summary"])
        self.assertIn("Live Fast-Path", briefing["formatted_summary"])

    def test_copilot_briefing_frontier_reasoning_tier(self):
        """Verifies CopilotFusionEngine operates under FRONTIER_REASONING tier."""
        retrieval = MockRetrievalEngine()
        fusion = CopilotFusionEngine(retrieval_engine=retrieval)

        payload = fusion.fuse_context(
            query="Evaluate Phase II dependencies",
            live_fast_path_events=[],
            stealth_mode=True,
        )

        briefing = fusion.synthesize_copilot_briefing(
            payload=payload,
            tier=ModelTier.FRONTIER_REASONING,
        )

        self.assertEqual(briefing["tier"], "FRONTIER_REASONING")
        self.assertEqual(briefing["resolved_model"], "gpt-6-luna")
        self.assertTrue(briefing["is_parameter_immune"])


if __name__ == "__main__":
    unittest.main()
