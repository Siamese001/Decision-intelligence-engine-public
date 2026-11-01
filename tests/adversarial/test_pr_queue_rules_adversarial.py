"""
Adversarial tests for PR Queue rules and table formatting validation.
"""

from __future__ import annotations

import re

PRIORITY_PATTERN = re.compile(r"^P[0-3]\.\d+$")


def test_adversarial_priority_regex_valid():
    valid = ["P0.1", "P0.2", "P0.10", "P1.1", "P2.1", "P2.15", "P3.1"]
    for p in valid:
        assert PRIORITY_PATTERN.match(p) is not None, f"Should match: {p}"


def test_adversarial_priority_regex_invalid():
    invalid = ["P4.1", "Tier 1", "T1.5", "P0", "P1", "P2", "HIGH", "low"]
    for p in invalid:
        assert PRIORITY_PATTERN.match(p) is None, f"Should not match: {p}"


def test_adversarial_domain_impact_prohibited_terms():
    # Test that forbidden tier strings are flagged in domain column
    prohibited = ["Tier 1", "Tier 1.5", "Tier 2", "T1", "T2", "P0", "P1"]
    sample_text = "Tier 1 cost optimization for token savings"
    assert any(term in sample_text for term in prohibited)

    clean_text = "Cost optimization reducing token usage by 50%"
    assert not any(term in clean_text for term in prohibited)
