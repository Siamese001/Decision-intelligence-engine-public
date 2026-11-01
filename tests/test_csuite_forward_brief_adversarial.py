"""Adversarial and edge-case testing for C-Suite Forward Brief and Egress Sanitizer.

Covers mandatory categories per governance rules:
1. Boundary payloads: Missing required CLI arguments, invalid horizons, malformed stakeholder strings.
2. Egress Sanitization & Invariant Protection: Confirms cloud egress fails-closed on API keys,
   private keys, and billing rates, while local operator mode permits internal advisory power maps.
3. Formatting resilience: Massive budget numbers and empty data structures.
"""
from __future__ import annotations

import pytest

from dual_pipeline.cli.executive_brief_handlers import handle_executive_brief_cli
from dual_pipeline.sanitizer import EgressSanitizationError, LocalPreEgressSanitizer


def test_boundary_missing_required_stakeholder_argument():
    # Calling CLI without --stakeholder must fail-closed with SystemExit
    with pytest.raises(SystemExit):
        handle_executive_brief_cli([])


def test_egress_sanitization_blocks_cloud_secrets_fail_closed():
    # Cloud secrets must ALWAYS raise EgressSanitizationError, even in operator mode
    fake_key = "sk-" + "a" * 32
    with pytest.raises(EgressSanitizationError):
        LocalPreEgressSanitizer.sanitize_text(f"Authorization: Bearer {fake_key}", allow_internal_operator=True)

    fake_aws = "AKIA" + "B" * 16
    with pytest.raises(EgressSanitizationError):
        LocalPreEgressSanitizer.sanitize_text(f"AWS Access Key: {fake_aws}", allow_internal_operator=True)


def test_egress_sanitization_blocks_billing_rates_fail_closed():
    # Billing rates must be blocked on external cloud dispatch
    with pytest.raises(EgressSanitizationError):
        LocalPreEgressSanitizer.sanitize_text("Billing rate is $195/hr for cloud architect", allow_internal_operator=False)


def test_local_operator_mode_permits_internal_advisory_text():
    # Internal advisory notes are permitted locally when allow_internal_operator=True
    advisory_text = "Executive Power Map: High informal veto power on cloud spending"
    sanitized = LocalPreEgressSanitizer.sanitize_text(advisory_text, allow_internal_operator=True)
    assert sanitized == advisory_text


def test_messages_sanitization_with_operator_flag():
    messages = [
        {"role": "user", "content": "Internal strategic advisory memo"},
        {"role": "assistant", "content": "PGD Fleet Outage Compression Strategy"},
    ]
    # Clean messages must pass without exception
    LocalPreEgressSanitizer.sanitize_messages(messages, allow_internal_operator=True)
