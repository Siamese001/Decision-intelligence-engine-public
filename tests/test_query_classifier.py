#!/usr/bin/env python3
"""Deprecated classifier boundary and assurance-policy regression tests."""
from __future__ import annotations

import unittest

from dual_pipeline.l2 import load_l2_config, query_model_policy_for_assurance
from dual_pipeline.query_router import QueryDestination, route_query


class QueryClassifierRemovalTests(unittest.TestCase):
    def test_compatibility_projection_uses_provider_free_router(self) -> None:
        result = route_query(
            "Explain the governed relationship between Project Beacon and PDLC."
        )
        self.assertEqual("relationship_explanation", result.query_type)
        self.assertEqual("NEE_DECISION_INTELLIGENCE", result.destination.value)

    def test_non_graph_work_has_no_classifier_fallback(self) -> None:
        decision = route_query("Summarize these meeting notes into actions.")
        self.assertIsNone(decision.query_type)
        self.assertEqual(QueryDestination.GENERAL_ASSISTANT_HANDOFF, decision.destination)

    def test_query_classifier_module_is_purged(self) -> None:
        """Verify dual_pipeline.query_classifier is purged and cannot be imported."""
        with self.assertRaises(ImportError):
            pass  # type: ignore[import-not-found]

    def test_query_policy_exposes_only_assurance_profiles(self) -> None:
        config = load_l2_config()
        standard = query_model_policy_for_assurance(config, "standard")
        enhanced = query_model_policy_for_assurance(config, "enhanced")
        self.assertEqual("standard", standard.assurance_profile.value)
        self.assertEqual(config.standard.reasoning_effort, standard.reasoning_effort)
        self.assertEqual("enhanced", enhanced.assurance_profile.value)
        self.assertEqual(config.enhanced.reasoning_effort, enhanced.reasoning_effort)
        self.assertFalse(hasattr(config, "normal"))
        self.assertEqual(256000, config.request_budget.maximum_provider_request_chars)


if __name__ == "__main__":
    unittest.main()
