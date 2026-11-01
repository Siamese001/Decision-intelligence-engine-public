"""Unit tests for LocalPreEgressSanitizer and dispatch integration."""
import unittest
from unittest.mock import MagicMock

from dual_pipeline.sanitizer import (
    EgressSanitizationError,
    LocalPreEgressSanitizer,
)


class TestLocalPreEgressSanitizer(unittest.TestCase):
    def test_blocks_modern_openai_project_keys(self):
        msg = [{"role": "user", "content": "My key is sk_test_redacted_placeholder"}]
        with self.assertRaises(EgressSanitizationError):
            LocalPreEgressSanitizer.sanitize_messages(msg)

    def test_blocks_anthropic_api_keys(self):
        msg = [{"role": "user", "content": "My key is sk_test_redacted_placeholder"}]
        with self.assertRaises(EgressSanitizationError):
            LocalPreEgressSanitizer.sanitize_messages(msg)

    def test_blocks_aws_access_key(self):
        msg = [{"role": "user", "content": "AWS key: AKIA_REDACTED_EXAMPLE"}]
        with self.assertRaises(EgressSanitizationError):
            LocalPreEgressSanitizer.sanitize_messages(msg)

    def test_blocks_github_pat(self):
        msg = [{"role": "user", "content": "Token: ghp_redacted_placeholder"}]
        with self.assertRaises(EgressSanitizationError):
            LocalPreEgressSanitizer.sanitize_messages(msg)

    def test_blocks_private_key_header(self):
        msg = [{"role": "user", "content": "REDACTED_PRIVATE_KEY_HEADER\nMIIEogIBAAKCAQEA..."}]
        with self.assertRaises(EgressSanitizationError):
            LocalPreEgressSanitizer.sanitize_messages(msg)

    def test_blocks_google_oauth_token(self):
        msg = [{"role": "user", "content": "Bearer ya29.a0AfH6SMD...sample_token"}]
        with self.assertRaises(EgressSanitizationError):
            LocalPreEgressSanitizer.sanitize_messages(msg)

    def test_blocks_billing_rate(self):
        msg = [{"role": "user", "content": "The billing rate is $250/hr for engineering."}]
        with self.assertRaises(EgressSanitizationError):
            LocalPreEgressSanitizer.sanitize_messages(msg)

    def test_blocks_c19_career_leverage(self):
        msg = [{"role": "user", "content": "We need to preserve our executive compensation band and political power map."}]
        with self.assertRaises(EgressSanitizationError):
            LocalPreEgressSanitizer.sanitize_messages(msg)

    def test_allows_clean_messages(self):
        msg = [
            {"role": "system", "content": "You are a professional decision intelligence assistant."},
            {"role": "user", "content": "Can we review the technical roadmap and milestones for Q4?"},
        ]
        LocalPreEgressSanitizer.sanitize_messages(msg)

    def test_completion_call_one_call_fails_closed_on_secret(self):
        from dual_pipeline.transcript_stages_core.completion_call import _one_call

        provider = MagicMock()
        recorder = MagicMock()
        record_usage = MagicMock()

        kwargs = {
            "model": "gpt-5.6-sol",
            "messages": [{"role": "user", "content": "Secret leak: AKIA_REDACTED_EXAMPLE"}],
        }

        with self.assertRaises(EgressSanitizationError):
            _one_call(
                provider=provider,
                effort="medium",
                kwargs=kwargs,
                deadline=60.0,
                recorder=recorder,
                model="gpt-5.6-sol",
                batch=False,
                started=0.0,
                record_usage=record_usage,
            )

    def test_external_synthesizer_fails_closed_on_secret(self):
        from dual_pipeline.transcript_stages_core.external_synthesizer import call_gemini_generate_content

        with self.assertRaises(EgressSanitizationError):
            call_gemini_generate_content(
                model="gemini-3.8-flash",
                system_prompt="Clean prompt",
                user_content="Secret payload: sk_test_redacted_placeholder",
            )


if __name__ == "__main__":
    unittest.main()
