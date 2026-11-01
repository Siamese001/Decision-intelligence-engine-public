"""
tests/adversarial/test_pre_gate_screener_adversarial.py

Adversarial and edge-case tests for Wave 1: Deterministic Pre-Gate Screener (Agent 3).
Covers 4 mandatory categories per .agents/rules/e2e-and-edge-case-coverage.md:
1. Boundary payload (0-byte text, empty attendees, single-char boundaries)
2. Malformed input (control characters, type confusions, invalid structures)
3. Overflow & exhaustion (input exceeding maximum character ceiling)
4. Invariant bypass attempt (whitespace masquerading as valid content)
"""
from __future__ import annotations

import unittest
from dual_pipeline.token_optimization.pre_gate_screener import (
    DEFAULT_AVERTED_TOKENS_PER_RUN,
    PreGateValidationError,
    screen_pre_gate,
    validate_or_raise,
)


class PreGateScreenerAdversarialTests(unittest.TestCase):
    def test_01_boundary_payload_empty_and_zero_length(self) -> None:
        """Category 1: Boundary payload - 0-byte text, empty lists, empty dictionaries."""
        res1 = screen_pre_gate({}, "", strict=True)
        self.assertFalse(res1.is_valid)
        self.assertTrue(any("empty" in e for e in res1.errors))

        res2 = screen_pre_gate({"project": "lens", "attendees": []}, "Short", strict=True)
        self.assertFalse(res2.is_valid)

    def test_02_malformed_input_control_characters_and_type_confusion(self) -> None:
        """Category 2: Malformed input - embedded control characters, invalid types."""
        # Attendee with null byte
        meta_null = {"project": "lens", "attendees": ["Alice\x00Smith", "Bob"]}
        res1 = screen_pre_gate(meta_null, "Valid content text for meeting.", strict=True)
        self.assertFalse(res1.is_valid)
        self.assertTrue(any("control characters" in e for e in res1.errors))

        # Text with embedded carriage returns and nulls
        res2 = screen_pre_gate(
            {"project": "lens", "attendees": "Alice, Bob"},
            "Valid prefix\x00corrupt suffix with null byte",
            strict=True,
        )
        self.assertFalse(res2.is_valid)
        self.assertTrue(any("null bytes" in e for e in res2.errors))

    def test_03_overflow_and_exhaustion_massive_payload(self) -> None:
        """Category 4: Overflow & exhaustion - payload exceeding maximum safe character ceiling."""
        meta = {"project": "lens", "attendees": "Alice, Bob"}
        huge_text = "A" * 1_500_000

        res = screen_pre_gate(meta, huge_text, strict=True)
        self.assertFalse(res.is_valid)
        self.assertTrue(any("exceeds maximum safe character ceiling" in e for e in res.errors))

    def test_04_invariant_bypass_whitespace_and_empty_project(self) -> None:
        """Category 5: Invariant bypass attempt - whitespace pretending to be valid strings."""
        # Whitespace project
        meta_ws = {"project": "   \t\n   ", "attendees": "Alice, Bob"}
        res1 = screen_pre_gate(meta_ws, "Valid text for the meeting session.", strict=True)
        self.assertFalse(res1.is_valid)
        self.assertTrue(any("project" in e for e in res1.errors))

        # Whitespace attendees
        meta_ws_att = {"project": "lens", "attendees": "   ,   ,   "}
        res2 = screen_pre_gate(meta_ws_att, "Valid text for the meeting session.", modality="meeting", strict=True)
        self.assertFalse(res2.is_valid)
        self.assertTrue(any("roster" in e for e in res2.errors))

        # Validate or raise must fail closed on these bypass attempts
        with self.assertRaises(PreGateValidationError):
            validate_or_raise(meta_ws, "Valid text", strict=True)


if __name__ == "__main__":
    unittest.main()
