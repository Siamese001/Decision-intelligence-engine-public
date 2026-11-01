"""
tests/fakes/credentials_fake.py

Governed shared test fake for credentials and provider authentication.
Eliminates credential leakage, verifies safe dummy keys, and validates zero secret exposure.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Dict, Iterator, Optional


SAFE_MOCK_KEYS = {
    "ANTHROPIC_API_KEY": "sk_test_redacted_placeholder",
    "OPENAI_API_KEY": "sk_test_redacted_placeholder",
    "GEMINI_API_KEY": "AIzaSySafeMockGeminiTestKey_0000000000000000",
    "AWS_ACCESS_KEY_ID": "AKIA_REDACTED_EXAMPLE",
    "AWS_SECRET_ACCESS_KEY": "safemockawssecretkey00000000000000000000",
}


@dataclass
class FakeCredentials:
    """Safe test credentials holder."""

    provider: str = "mock_provider"
    api_key: str = "mock_key_safe_testing_only"
    is_mock: bool = True
    source: str = "tests.fakes.credentials_fake"

    def get_api_key(self) -> str:
        return self.api_key

    def validate_non_leaking(self, text: str) -> bool:
        """Verify that the real key (or any key) does not appear in text."""
        if not self.api_key:
            return True
        return self.api_key not in text


@contextmanager
def safe_credentials_environment(
    custom_keys: Optional[Dict[str, str]] = None,
) -> Iterator[Dict[str, str]]:
    """Context manager temporarily injecting safe mock keys into os.environ.

    Restores previous environment on exit, preventing state leakage between tests.
    """
    keys_to_set = dict(SAFE_MOCK_KEYS)
    if custom_keys:
        keys_to_set.update(custom_keys)

    original_env = {}
    for k in keys_to_set:
        if k in os.environ:
            original_env[k] = os.environ[k]

    try:
        for k, v in keys_to_set.items():
            os.environ[k] = v
        yield keys_to_set
    finally:
        for k in keys_to_set:
            if k in original_env:
                os.environ[k] = original_env[k]
            else:
                os.environ.pop(k, None)
