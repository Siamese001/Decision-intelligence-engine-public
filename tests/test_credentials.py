#!/usr/bin/env python3
"""Unit test suite for config.credentials, storage.credentials, and dual_pipeline.credentials."""
import os
import unittest
from unittest import mock

import config.credentials as canonical_creds
import dual_pipeline.credentials as dual_creds
import storage.credentials as storage_creds
from config.credentials import (
    GOVERNED_MODELS,
    CredentialManager,
    get_async_openai_client,
    mask_secret,
    resolve_openai_credentials,
)


class TestCredentials(unittest.TestCase):
    def test_mask_secret(self):
        self.assertEqual(mask_secret(None), "[NOT_CONFIGURED]")
        self.assertEqual(mask_secret(""), "[NOT_CONFIGURED]")
        self.assertEqual(mask_secret("short"), "[REDACTED]")
        self.assertEqual(
            mask_secret("sk_test_redacted_placeholder"),
            "sk-proj-...1234",
        )

    def test_resolve_credentials_mock_mode_when_key_absent(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            creds = resolve_openai_credentials(allow_mock=True, model="gpt-5.6-luna")
            self.assertTrue(creds.is_mock)
            self.assertIsNone(creds.api_key)
            self.assertEqual(creds.model, "gpt-5.6-luna")
            self.assertEqual(creds.masked_key, "[MOCK_MODE]")
            self.assertFalse(creds.has_live_key)
            self.assertTrue(creds.is_reasoning_model)

    def test_resolve_credentials_raises_when_mock_disallowed_and_key_absent(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            with self.assertRaises(ValueError):
                resolve_openai_credentials(allow_mock=False)
            # Verify default is allow_mock=False
            with self.assertRaises(ValueError):
                resolve_openai_credentials()

    def test_resolve_credentials_with_valid_key(self):
        fake_key = "sk_test_redacted_placeholder"
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": fake_key}):
            creds = resolve_openai_credentials(allow_mock=False, model="gpt-5.6-luna")
            self.assertFalse(creds.is_mock)
            self.assertEqual(creds.api_key, fake_key)
            self.assertEqual(creds.model, "gpt-5.6-luna")
            self.assertEqual(creds.masked_key, "sk-proj-...cdef")
            self.assertTrue(creds.has_live_key)
            self.assertTrue(creds.is_reasoning_model)

    def test_default_model_is_gpt56_sol_max(self):
        fake_key = "sk_test_redacted_placeholder"
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": fake_key}, clear=True):
            creds = resolve_openai_credentials(allow_mock=False)
            self.assertEqual(creds.model, "gpt-5.6-sol")
            self.assertEqual(creds.reasoning_effort, "max")
            self.assertTrue(creds.is_reasoning_model)

    def test_governed_models_contains_gpt56_and_gpt6_models(self):
        self.assertIn("gpt-6-luna", GOVERNED_MODELS)
        self.assertIn("gpt-6.1-sol", GOVERNED_MODELS)
        self.assertIn("gpt-6-sol", GOVERNED_MODELS)
        self.assertIn("gpt-6-luna", GOVERNED_MODELS)
        self.assertIn("gpt-5.6-luna", GOVERNED_MODELS)
        self.assertIn("gpt-5.6-sol", GOVERNED_MODELS)
        self.assertIn("gpt-5.6-terra", GOVERNED_MODELS)
        self.assertIn("bge-m3", GOVERNED_MODELS)

    def test_canonicalize_gpt6_aliases(self):
        from config.credentials import canonicalize_model_name
        self.assertEqual(canonicalize_model_name("6.1-sol"), "gpt-6.1-sol")
        self.assertEqual(canonicalize_model_name("sol-6.1"), "gpt-6.1-sol")
        self.assertEqual(canonicalize_model_name("gpt-6.1"), "gpt-6.1-sol")
        self.assertEqual(canonicalize_model_name("6-luna"), "gpt-6-luna")
        self.assertEqual(canonicalize_model_name("luna-6"), "gpt-6-luna")
        self.assertEqual(canonicalize_model_name("sol-6"), "gpt-6-sol")

    def test_gpt56_terra_reasoning_model_classification(self):
        fake_key = "sk_test_redacted_placeholder"
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": fake_key}):
            creds = resolve_openai_credentials(allow_mock=False, model="gpt-5.6-terra")
            self.assertTrue(creds.is_reasoning_model)

    def test_resolve_credentials_with_gpt56_sol_medium(self):
        fake_key = "sk_test_redacted_placeholder"
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": fake_key}):
            creds = resolve_openai_credentials(
                allow_mock=False,
                model="gpt-5.6-sol",
                reasoning_effort="high",
            )
            self.assertFalse(creds.is_mock)
            self.assertEqual(creds.api_key, fake_key)
            self.assertEqual(creds.model, "gpt-5.6-sol")
            self.assertEqual(creds.reasoning_effort, "high")
            self.assertEqual(creds.masked_key, "sk-proj-...cdef")
            self.assertTrue(creds.is_reasoning_model)

    def test_sanitize_model_call_kwargs_maps_max_to_xhigh(self):
        raw = {"temperature": 0.7, "top_p": 0.95, "reasoning_effort": "max"}
        clean = canonical_creds.sanitize_model_call_kwargs("gpt-5.6-sol", raw)
        self.assertNotIn("temperature", clean)
        self.assertNotIn("top_p", clean)
        self.assertEqual(clean["reasoning_effort"], "xhigh")

    def test_enterprise_gateway_proxy_parameters(self):
        fake_key = "sk_test_redacted_placeholder"
        custom_base = "https://gateway.ai.consulting.example.com/v1"
        custom_org = "org-partner_advisory-enterprise"
        custom_proj = "proj-decision-intel"

        with mock.patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": fake_key,
                "OPENAI_BASE_URL": custom_base,
                "OPENAI_ORG_ID": custom_org,
                "OPENAI_PROJECT_ID": custom_proj,
            },
        ):
            creds = resolve_openai_credentials(allow_mock=False)
            self.assertEqual(creds.base_url, custom_base)
            self.assertEqual(creds.organization, custom_org)
            self.assertEqual(creds.project, custom_proj)

    def test_async_client_factory_mock_mode(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            client, creds = get_async_openai_client(allow_mock=True, model="gpt-5.6-sol")
            self.assertIsNone(client)
            self.assertTrue(creds.is_mock)
            self.assertEqual(creds.model, "gpt-5.6-sol")

    def test_facade_reexport_parity(self):
        """Verifies storage.credentials and dual_pipeline.credentials cleanly re-export config.credentials."""
        self.assertIs(storage_creds.CredentialManager, canonical_creds.CredentialManager)
        self.assertIs(dual_creds.CredentialManager, canonical_creds.CredentialManager)
        self.assertIs(storage_creds.resolve_openai_credentials, canonical_creds.resolve_openai_credentials)
        self.assertIs(dual_creds.resolve_openai_credentials, canonical_creds.resolve_openai_credentials)
        self.assertIs(storage_creds.get_async_openai_client, canonical_creds.get_async_openai_client)
        self.assertIs(dual_creds.get_async_openai_client, canonical_creds.get_async_openai_client)

    def test_credential_manager_accessors(self):
        fake_key = "sk_test_redacted_placeholder"
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": fake_key, "CUSTOM_VAR": "governed_val"}):
            resolved_key = CredentialManager.get_openai_api_key(allow_mock=False)
            self.assertEqual(resolved_key, fake_key)
            self.assertEqual(CredentialManager.get_credential("CUSTOM_VAR"), "governed_val")
            self.assertEqual(CredentialManager.mask(fake_key), "sk-proj-...cdef")


if __name__ == "__main__":
    unittest.main()
