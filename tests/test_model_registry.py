#!/usr/bin/env python3
"""Unit test suite for dual_pipeline.model_registry and multi-provider credentials."""
import os
import unittest
from unittest import mock

from config.credentials import (
    CredentialManager,
    resolve_anthropic_credentials,
    resolve_google_credentials,
    resolve_local_embedding_credentials,
    sanitize_model_call_kwargs,
)
from dual_pipeline.model_registry import (
    ModelBindingSpec,
    ModelTier,
    get_binding,
    get_tier_spec,
    prepare_call_kwargs,
    reload_registry,
    resolve_tier_model,
)
from pipeline_a_intake.agents.forensic_transcript_healer import get_forensic_transcript_healer_card
from pipeline_a_intake.agents.multimodal_slide_agent import get_multimodal_slide_card
from pipeline_a_intake.agents.roster_reconciler_agent import get_roster_reconciler_card
from pipeline_b_extraction.agents.strategic_commitments_agent import get_strategic_commitments_card
from pipeline_b_extraction.agents.tech_arch_agent import get_tech_arch_card


class TestModelRegistry(unittest.TestCase):
    def setUp(self):
        reload_registry()

    def test_tier_resolution_and_defaults(self):
        """Verify tier resolution defaults to governed GPT-6 models with decoupled Copilot and Query tiers."""
        with mock.patch.dict(os.environ, {"OPENAI_MODEL": "gpt-5.6-luna", "OPENAI_REASONING_EFFORT": "high"}):
            m_frontier, effort_frontier = resolve_tier_model(ModelTier.FRONTIER_HIGH_REASONING)
            self.assertEqual(m_frontier, "gpt-5.6-luna")
            self.assertEqual(effort_frontier, "high")

            m_healer, effort_healer = resolve_tier_model(ModelTier.SURGICAL_HEALER)
            self.assertEqual(m_healer, "gpt-5.6-luna")
            self.assertEqual(effort_healer, "high")

            m_judge, effort_judge = resolve_tier_model(ModelTier.DIALECTICAL_JUDGE)
            self.assertEqual(m_judge, "gpt-5.6-luna")
            self.assertEqual(effort_judge, "high")

        with mock.patch.dict(os.environ, {
            "FAST_ROUTING_MODEL": "", "FAST_ROUTING_REASONING_EFFORT": "",
            "COPILOT_MODEL": "", "COPILOT_REASONING_EFFORT": "",
            "QUERY_REASONING_MODEL": "", "QUERY_REASONING_REASONING_EFFORT": "",
            "OPENAI_MODEL": "", "OPENAI_REASONING_EFFORT": "",
        }):
            m_fast, effort_fast = resolve_tier_model(ModelTier.FAST_ROUTING)
            self.assertEqual(m_fast, "gpt-6-luna")
            self.assertEqual(effort_fast, "low")

            m_copilot, effort_copilot = resolve_tier_model(ModelTier.COPILOT_FAST_SNAP)
            self.assertEqual(m_copilot, "gpt-6-luna")
            self.assertEqual(effort_copilot, "medium")

            m_query, effort_query = resolve_tier_model(ModelTier.QUERY_REASONING)
            self.assertEqual(m_query, "gpt-6.1-sol")
            self.assertEqual(effort_query, "high")

            m_judge, effort_judge = resolve_tier_model(ModelTier.DIALECTICAL_JUDGE)
            self.assertEqual(m_judge, "gpt-6-luna")
            self.assertEqual(effort_judge, "max")

    def test_get_binding_spec(self):
        """Verify get_binding returns typed ModelBindingSpec."""
        binding = get_binding(ModelTier.DIALECTICAL_JUDGE)
        self.assertIsInstance(binding, ModelBindingSpec)
        self.assertEqual(binding.model_id, "gpt-6-luna")
        self.assertEqual(binding.reasoning_effort, "max")
        self.assertEqual(binding.provider, "openai")
        self.assertEqual(binding.timeout, 300.0)
        self.assertTrue(binding.strip_temperature)
        self.assertTrue(binding.strip_top_p)

    def test_tier_spec_structure(self):
        spec = get_tier_spec(ModelTier.FRONTIER_HIGH_REASONING)
        self.assertIn("default_model", spec)
        self.assertIn("provider", spec)
        self.assertEqual(spec["provider"], "openai")
        self.assertEqual(spec["default_model"], "gpt-6.1-sol")
        self.assertEqual(spec.get("reasoning_effort"), "max")

    def test_parameter_immunity_for_reasoning_model(self):
        raw_kwargs = {
            "temperature": 0.7,
            "top_p": 0.95,
            "presence_penalty": 0.5,
            "reasoning_effort": "high",
            "messages": [{"role": "user", "content": "hello"}],
        }
        sanitized = sanitize_model_call_kwargs("gpt-6-luna", raw_kwargs)
        self.assertNotIn("temperature", sanitized)
        self.assertNotIn("top_p", sanitized)
        self.assertNotIn("presence_penalty", sanitized)
        self.assertEqual(sanitized["reasoning_effort"], "high")
        self.assertIn("messages", sanitized)

    def test_parameter_immunity_for_standard_model(self):
        raw_kwargs = {
            "messages": [{"role": "user", "content": "hello"}],
        }
        sanitized = sanitize_model_call_kwargs("gpt-4o", raw_kwargs)
        self.assertEqual(sanitized["temperature"], 0.0)

    def test_prepare_call_kwargs(self):
        with mock.patch.dict(os.environ, {"OPENAI_MODEL": "", "DIALECTICAL_JUDGE_MODEL": ""}):
            kw = prepare_call_kwargs(ModelTier.DIALECTICAL_JUDGE, {"temperature": 0.5})
            self.assertEqual(kw["model"], "gpt-6-luna")
            self.assertEqual(kw["reasoning_effort"], "xhigh")
            self.assertNotIn("temperature", kw)

    def test_agent_card_bindings_dynamic(self):
        """Verify agent cards resolve dynamically from tier models without hardcoded literals."""
        card_roster = get_roster_reconciler_card()
        self.assertEqual(card_roster.model_binding.model_id, "gpt-6.1-sol")
        self.assertEqual(card_roster.model_binding.reasoning_effort, "max")

        card_healer = get_forensic_transcript_healer_card()
        self.assertEqual(card_healer.model_binding.model_id, "gpt-6-luna")
        self.assertEqual(card_healer.model_binding.reasoning_effort, "medium")

        card_slide = get_multimodal_slide_card()
        self.assertEqual(card_slide.model_binding.model_id, "gpt-6.1-sol")
        self.assertEqual(card_slide.model_binding.reasoning_effort, "max")

        card_tech = get_tech_arch_card()
        self.assertEqual(card_tech.model_binding.model_id, "gpt-6.1-sol")
        self.assertEqual(card_tech.model_binding.reasoning_effort, "max")

        card_commit = get_strategic_commitments_card()
        self.assertEqual(card_commit.model_binding.model_id, "gpt-6.1-sol")
        self.assertEqual(card_commit.model_binding.reasoning_effort, "max")

    def test_anthropic_credentials_resolution(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            creds = resolve_anthropic_credentials(allow_mock=True)
            self.assertTrue(creds.is_mock)
            self.assertEqual(creds.masked_key, "[MOCK_MODE]")

        fake_key = "sk_test_redacted_placeholder"
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": fake_key}):
            creds = resolve_anthropic_credentials(allow_mock=False)
            self.assertFalse(creds.is_mock)
            self.assertEqual(creds.api_key, fake_key)
            self.assertEqual(CredentialManager.get_anthropic_api_key(allow_mock=False), fake_key)

    def test_google_credentials_resolution(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}):
            creds = resolve_google_credentials(allow_mock=True)
            self.assertTrue(creds.is_mock)
            self.assertEqual(creds.masked_key, "[MOCK_MODE]")

        fake_key = "AIzaSyTestGoogleKey1234567890"
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": fake_key}):
            creds = resolve_google_credentials(allow_mock=False)
            self.assertFalse(creds.is_mock)
            self.assertEqual(creds.api_key, fake_key)
            self.assertEqual(CredentialManager.get_google_api_key(allow_mock=False), fake_key)

    def test_local_embedding_credentials(self):
        creds = resolve_local_embedding_credentials()
        self.assertIsNotNone(creds.device)
        self.assertIsInstance(creds.fp16, bool)

    def test_frontier_and_query_tiers_ssot(self):
        """Verify gpt-6-luna (max) for frontier judges, gpt-6.1-sol for query reasoning, and fallbacks."""
        from dual_pipeline.model_registry import (
            REGISTRY_JSON_PATH,
            get_tier_spec,
            resolve_copilot_tier_model,
            resolve_query_tier_model,
        )
        self.assertTrue(REGISTRY_JSON_PATH.is_file(), "model_registry.json must exist as SSOT")

        # Verify heavier reasoning uses gpt-6-luna (max)
        spec_frontier = get_tier_spec(ModelTier.FRONTIER_HIGH_REASONING)
        self.assertEqual(spec_frontier.get("default_model"), "gpt-6.1-sol")
        self.assertEqual(spec_frontier.get("reasoning_effort"), "max")
        self.assertIn("gpt-6.1-sol", spec_frontier.get("allowed_fallbacks", []))

        spec_judge = get_tier_spec(ModelTier.DIALECTICAL_JUDGE)
        self.assertEqual(spec_judge.get("default_model"), "gpt-6-luna")
        self.assertEqual(spec_judge.get("reasoning_effort"), "max")
        self.assertIn("gpt-6.1-sol", spec_judge.get("allowed_fallbacks", []))

        # Verify query reasoning uses gpt-6.1-sol (high)
        spec_query = get_tier_spec(ModelTier.QUERY_REASONING)
        self.assertEqual(spec_query.get("default_model"), "gpt-6.1-sol")
        self.assertEqual(spec_query.get("reasoning_effort"), "high")
        self.assertIn("gpt-6-luna", spec_query.get("allowed_fallbacks", []))

        # Verify helper functions resolve cleanly without ambient env_di overrides
        with mock.patch.dict(os.environ, {
            "OPENAI_MODEL": "", "OPENAI_REASONING_EFFORT": "",
            "QUERY_REASONING_MODEL": "", "QUERY_REASONING_REASONING_EFFORT": "",
            "COPILOT_MODEL": "", "COPILOT_FAST_SNAP_MODEL": "",
            "COPILOT_REASONING_EFFORT": "", "COPILOT_FAST_SNAP_REASONING_EFFORT": "",
        }):
            m_q, e_q = resolve_query_tier_model()
            self.assertEqual(m_q, "gpt-6.1-sol")
            self.assertEqual(e_q, "high")

            m_c, e_c = resolve_copilot_tier_model()
            self.assertEqual(m_c, "gpt-6-luna")
            self.assertEqual(e_c, "medium")

    def test_acoustic_and_transcription_tiers_ssot(self):
        """Verify acoustic and transcription tiers resolve from SSOT with env override support."""
        m_mlx, _ = resolve_tier_model(ModelTier.ACOUSTIC_MLX_WHISPER)
        self.assertEqual(m_mlx, "mlx-community/whisper-large-v3-mlx")

        m_fw, _ = resolve_tier_model(ModelTier.ACOUSTIC_FASTER_WHISPER)
        self.assertEqual(m_fw, "large-v3")

        m_wx, _ = resolve_tier_model(ModelTier.ACOUSTIC_WHISPERX)
        self.assertEqual(m_wx, "large-v3")

        m_diar, _ = resolve_tier_model(ModelTier.ACOUSTIC_DIARIZATION)
        self.assertEqual(m_diar, "pyannote/speaker-diarization-3.1")

        m_cloud, _ = resolve_tier_model(ModelTier.CLOUD_AUDIO_TRANSCRIBE)
        self.assertEqual(m_cloud, "blocked_by_security_policy")

        # Test environment overrides
        with mock.patch.dict(os.environ, {
            "ACOUSTIC_MLX_WHISPER_MODEL": "unallowed-model",
            "ACOUSTIC_DIARIZATION_MODEL": "pause_clustering",
        }):
            m_mlx_over, _ = resolve_tier_model(ModelTier.ACOUSTIC_MLX_WHISPER)
            self.assertEqual(m_mlx_over, "mlx-community/whisper-large-v3-mlx")

            m_diar_over, _ = resolve_tier_model(ModelTier.ACOUSTIC_DIARIZATION)
            self.assertEqual(m_diar_over, "pause_clustering")

    def test_copilot_fast_snap_fallbacks_strict_sla(self):
        """Verify COPILOT_FAST_SNAP allowed fallbacks strictly adhere to sub-7.0s latency SLA."""
        spec_copilot = get_tier_spec(ModelTier.COPILOT_FAST_SNAP)
        allowed = spec_copilot.get("allowed_fallbacks", [])
        self.assertEqual(allowed, ["gpt-5.6-luna"])
        # Deliberative reasoning models (Sol / Astra) must NEVER be in Copilot fallbacks
        for forbidden in ("gpt-6.1-sol", "gpt-5.6-sol", "gpt-6-luna", "gpt-6-sol"):
            self.assertNotIn(forbidden, allowed, f"{forbidden} violates Copilot 7.0s SLA")

    def test_gemini_flash_38_ssot_and_banned_25_flash(self):
        """Verify gemini-3.8-flash (high reasoning) is SSOT and banned models are rejected."""
        from config.credentials import (
            DEFAULT_GEMINI_FAILOVER_MODEL,
            DEFAULT_GEMINI_PRO_MODEL,
            GOOGLE_APPROVED_MODELS,
            GOVERNED_MODELS,
        )
        from dual_pipeline.model_registry import (
            BANNED_REGISTRY_MODELS,
            get_binding,
            resolve_tier_model,
        )

        # 1. Banned models must strictly include retired model
        banned_cand = "gemini-2.5-flash"  # allow-retired-model
        self.assertIn(banned_cand, BANNED_REGISTRY_MODELS)
        self.assertNotIn(banned_cand, GOOGLE_APPROVED_MODELS)
        self.assertNotIn(banned_cand, GOVERNED_MODELS)

        # 2. Approved Google models must contain gemini-3.8-flash
        self.assertIn("gemini-3.8-flash", GOOGLE_APPROVED_MODELS)
        self.assertEqual(DEFAULT_GEMINI_FAILOVER_MODEL, "gemini-3.8-flash")
        self.assertEqual(DEFAULT_GEMINI_PRO_MODEL, "gemini-3.8-flash")

        # 3. ANTIGRAVITY_AGENT_FLASH tier binding
        binding = get_binding(ModelTier.ANTIGRAVITY_AGENT_FLASH)
        self.assertEqual(binding.model_id, "gemini-3.8-flash")
        self.assertEqual(binding.provider, "google")
        self.assertEqual(binding.reasoning_effort, "high")
        self.assertTrue(binding.strip_temperature)
        self.assertTrue(binding.strip_top_p)
        self.assertEqual(binding.allowed_fallbacks, ["gemini-3.5-flash-lite"])

        # 4. Tier resolution with banned override falls back to gemini-3.8-flash
        with mock.patch.dict(os.environ, {"ANTIGRAVITY_AGENT_FLASH_MODEL": banned_cand}):
            m, eff = resolve_tier_model(ModelTier.ANTIGRAVITY_AGENT_FLASH)
            self.assertEqual(m, "gemini-3.8-flash")
            self.assertEqual(eff, "high")


if __name__ == "__main__":
    unittest.main()
