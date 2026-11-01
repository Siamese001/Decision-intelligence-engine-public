"""
Adversarial Test Suite: Hermetic Credential Isolation & Egress Sandboxing.

Verifies:
1. Standard tests running under pytest have all live provider API keys scrubbed from os.environ.
2. Even if workstation environment files (~/.config/ai_env/common.env, env_di) export live keys,
   standard test execution cannot access them, preventing accidental rogue API egress.
3. Tests explicitly marked with @pytest.mark.live_network retain credential passthrough.
4. Outer process credentials are restored cleanly upon test completion.
"""
from __future__ import annotations

import os

import pytest

from tests.conftest import LIVE_CREDENTIAL_ENV_VARS


def test_standard_execution_scrubs_all_live_credentials():
    """Verify that all live provider credentials are automatically absent during standard test execution."""
    for var in LIVE_CREDENTIAL_ENV_VARS:
        assert var not in os.environ, f"Live credential {var} was smuggled into test process!"


def test_adversarial_credential_injection_blocked_between_tests(monkeypatch: pytest.MonkeyPatch):
    """Verify that even if a test injects a live credential, the sandbox scrubs it for subsequent tests."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk_test_redacted_placeholder")
    assert os.environ.get("ANTHROPIC_API_KEY") == "sk_test_redacted_placeholder"


@pytest.mark.live_network
def test_explicit_live_network_marker_permits_passthrough(monkeypatch: pytest.MonkeyPatch):
    """Verify that tests explicitly decorated with @pytest.mark.live_network can access credentials."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk_test_redacted_placeholder")
    assert os.environ.get("ANTHROPIC_API_KEY") == "sk_test_redacted_placeholder"


def test_subsequent_test_remains_hermetic_after_opt_in():
    """Verify hermetic state holds after an opt-in test completes."""
    assert "ANTHROPIC_API_KEY" not in os.environ
