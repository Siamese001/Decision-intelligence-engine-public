"""Unit tests for L2 policy routing and provider selection.

Verifies:
1. Per-purpose model and provider policy dispatch via L2Config.model_for_purpose.
2. UnifiedL2ProviderFactory and select_l2_provider dispatch to OpenAI vs Google.
3. FORENSIC_TRANSCRIPT routes to OpenAI gpt-5.6-luna.
4. EXECUTIVE_RECAP routes to Google gemini-3.8-flash.
5. KG_SIGNAL_PROJECTION routes to Google gemini-3.8-flash.
6. Fallback to assurance profile when purpose is not in purpose_models.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.l2_policy import (
    CallPurpose,
    ModelPolicy,
    PricingPolicy,
    QueryAssuranceProfile,
    load_l2_config,
    query_model_policy_for_purpose,
)
from dual_pipeline.l2_providers import (
    GoogleReasoningProvider,
    OpenAIReasoningProvider,
    UnifiedL2ProviderFactory,
    select_l2_provider,
)


class TestL2PolicyRouting(unittest.TestCase):
    """Test suite for policy-driven multi-model routing across pipeline stages."""

    @classmethod
    def setUpClass(cls) -> None:
        from dual_pipeline.l2_providers_core.sentinel import authorize_provider_instantiation
        authorize_provider_instantiation()
        cls.config = load_l2_config()

    def test_purpose_models_loaded_from_ssot_config(self) -> None:
        """Verify purpose_models dictionary is populated from l2-policy.json."""
        self.assertIn("FORENSIC_TRANSCRIPT", self.config.purpose_models)
        self.assertIn("EXECUTIVE_RECAP", self.config.purpose_models)
        self.assertIn("KG_SIGNAL_PROJECTION", self.config.purpose_models)
        self.assertIn("AGENTIC_LEVERAGE", self.config.purpose_models)

    @patch("dual_pipeline.l2_providers_core.provider.resolve_openai_api_key", return_value="fake-openai-key")
    def test_forensic_transcript_routes_to_openai_luna(self, _mock_key) -> None:
        """Verify FORENSIC_TRANSCRIPT dispatches to OpenAI with gpt-5.6-luna."""
        policy, provider = select_l2_provider(self.config, CallPurpose.FORENSIC_TRANSCRIPT)
        self.assertEqual(policy.provider, "openai")
        self.assertEqual(policy.model, "gpt-5.6-luna")
        self.assertIsInstance(provider, OpenAIReasoningProvider)

    @patch("dual_pipeline.l2_providers_core.google_provider.resolve_google_api_key", return_value="fake-google-key")
    def test_executive_recap_routes_to_google_flash(self, _mock_key) -> None:
        """Verify EXECUTIVE_RECAP dispatches to Google with gemini-3.8-flash."""
        policy, provider = select_l2_provider(self.config, CallPurpose.EXECUTIVE_RECAP)
        self.assertEqual(policy.provider, "google")
        self.assertEqual(policy.model, "gemini-3.8-flash")
        self.assertIsInstance(provider, GoogleReasoningProvider)

    @patch("dual_pipeline.l2_providers_core.google_provider.resolve_google_api_key", return_value="fake-google-key")
    def test_kg_signal_projection_routes_to_google_flash(self, _mock_key) -> None:
        """Verify KG_SIGNAL_PROJECTION dispatches to Google with gemini-3.8-flash."""
        policy, provider = select_l2_provider(self.config, CallPurpose.KG_SIGNAL_PROJECTION)
        self.assertEqual(policy.provider, "google")
        self.assertEqual(policy.model, "gemini-3.8-flash")
        self.assertIsInstance(provider, GoogleReasoningProvider)

    @patch("dual_pipeline.l2_providers_core.google_provider.resolve_google_api_key", return_value="fake-google-key")
    def test_agentic_leverage_routes_to_google_flash(self, _mock_key) -> None:
        """Verify AGENTIC_LEVERAGE dispatches to Google with gemini-3.8-flash."""
        policy, provider = select_l2_provider(self.config, CallPurpose.AGENTIC_LEVERAGE)
        self.assertEqual(policy.provider, "google")
        self.assertEqual(policy.model, "gemini-3.8-flash")
        self.assertIsInstance(provider, GoogleReasoningProvider)

    @patch("dual_pipeline.l2_providers_core.provider.resolve_openai_api_key", return_value="fake-openai-key")
    def test_unspecified_purpose_falls_back_to_assurance_profile(self, _mock_key) -> None:
        """Verify unmapped CallPurpose falls back to standard model policy."""
        policy, provider = select_l2_provider(self.config, CallPurpose.PRIMARY)
        self.assertEqual(policy.model, self.config.standard.model)
        self.assertEqual(policy.provider, self.config.standard.provider)

    def test_query_model_policy_for_purpose_helper(self) -> None:
        """Verify query_model_policy_for_purpose functional entrypoint."""
        policy = query_model_policy_for_purpose(self.config, CallPurpose.EXECUTIVE_RECAP)
        self.assertEqual(policy.model, "gemini-3.8-flash")
        self.assertEqual(policy.provider, "google")

    @patch("dual_pipeline.l2_providers_core.provider.resolve_openai_api_key", return_value="fake-openai-key")
    @patch("dual_pipeline.l2_providers_core.google_provider.resolve_google_api_key", return_value="fake-google-key")
    def test_unified_factory_dispatches_by_provider_attribute(self, _mock_gkey, _mock_okey) -> None:
        """Verify UnifiedL2ProviderFactory dynamically selects provider based on policy."""
        factory = UnifiedL2ProviderFactory(self.config)
        pricing = PricingPolicy(input_per_million=1.0, cached_input_per_million=0.5, output_per_million=2.0)

        google_policy = ModelPolicy(
            provider="google",
            model_family="gemini-3.8-flash",
            model="gemini-3.8-flash",
            assurance_profile=QueryAssuranceProfile.STANDARD,
            reasoning_effort="high",
            maximum_primary_calls_per_run=1,
            maximum_repair_calls_per_run=0,
            maximum_escalation_calls_per_run=0,
            pricing=pricing,
        )
        self.assertIsInstance(factory(google_policy), GoogleReasoningProvider)

        openai_policy = ModelPolicy(
            provider="openai",
            model_family="gpt-5.6-luna",
            model="gpt-5.6-luna",
            assurance_profile=QueryAssuranceProfile.STANDARD,
            reasoning_effort="high",
            maximum_primary_calls_per_run=1,
            maximum_repair_calls_per_run=0,
            maximum_escalation_calls_per_run=0,
            pricing=pricing,
        )
        self.assertIsInstance(factory(openai_policy), OpenAIReasoningProvider)


if __name__ == "__main__":
    unittest.main()
