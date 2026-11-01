"""Unit tests for dual_pipeline.l2_providers facade.

Verifies complete re-export coverage, policy path integrity, and enum/schema consistency.
"""
from __future__ import annotations

import unittest
from pathlib import Path

import dual_pipeline.l2_providers as l2_facade


class TestL2ProvidersFacade(unittest.TestCase):
    def test_provider_classes_and_factories_exported(self) -> None:
        self.assertTrue(hasattr(l2_facade, "OpenAIReasoningProvider"))
        self.assertTrue(hasattr(l2_facade, "OpenAIProviderFactory"))
        self.assertTrue(hasattr(l2_facade, "ReasoningProvider"))
        self.assertTrue(hasattr(l2_facade, "ReasoningProviderFactory"))

    def test_data_models_and_enums_exported(self) -> None:
        self.assertTrue(hasattr(l2_facade, "L2ExecutionDecision"))
        self.assertTrue(hasattr(l2_facade, "L2ExecutionResult"))
        self.assertTrue(hasattr(l2_facade, "L2Policy"))
        self.assertTrue(hasattr(l2_facade, "L2Config"))
        self.assertTrue(hasattr(l2_facade, "L2Disposition"))
        self.assertTrue(hasattr(l2_facade, "L2CallRecord"))
        self.assertTrue(hasattr(l2_facade, "L2ProviderRequest"))
        self.assertTrue(hasattr(l2_facade, "L2ProviderResponse"))
        self.assertTrue(hasattr(l2_facade, "L2ReasonCode"))

    def test_policy_path_and_schemas_exported(self) -> None:
        self.assertTrue(hasattr(l2_facade, "L2_POLICY_PATH"))
        self.assertIsInstance(l2_facade.L2_POLICY_PATH, Path)
        self.assertTrue(hasattr(l2_facade, "DECISION_MODEL_OUTPUT_SCHEMA"))
        self.assertIsInstance(l2_facade.DECISION_MODEL_OUTPUT_SCHEMA, dict)
        self.assertTrue(hasattr(l2_facade, "QUERY_TYPE_POLICY_MATRIX"))

    def test_analysis_functions_exported(self) -> None:
        self.assertTrue(callable(getattr(l2_facade, "analyze_request_intent", None)))
        self.assertTrue(callable(getattr(l2_facade, "aggregate_token_usage", None)))


if __name__ == "__main__":
    unittest.main()
