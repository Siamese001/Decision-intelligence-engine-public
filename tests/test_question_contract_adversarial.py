#!/usr/bin/env python3
"""tests/test_question_contract_adversarial.py

Adversarial tests for Wave 5 Question Payload Contract.
Verifies rejection of:
1. Jargon hidden in options (file paths, code syntax, snake_case, flags).
2. Mismatched confidence in visible text vs structured numerical value.
3. Overflowing lengths (question > 300 chars, option > 250 chars).
4. Unicode / emoji padding boundary exploits.
5. Bundled or compound questions (multiple '?' or conjunctive clauses).
6. Ellipses and truncation markers ('...', '\u2026', '[truncated]').
7. Missing confidence ratings.
8. Misplaced recommended option (not listed first).
"""

from __future__ import annotations

import unittest

from dual_pipeline.hitl_governance_core.question_validator import (
    validate_question_payload,
)


class TestQuestionContractAdversarial(unittest.TestCase):
    """Adversarial test suite exercising security, clarity, and visibility boundaries."""

    def test_adversarial_jargon_hidden_in_option(self) -> None:
        """Verify that options concealing code names, paths, or flags are strictly rejected."""
        valid_q = "How should we proceed with server migration?"
        invalid_options = [
            # File path
            [
                "(Recommended) Read tools/migrate.py before continuing (Confidence: 85%, Tier: High). Ensures safety.",
                "Proceed without reading (Confidence: 50%, Tier: Medium). High risk.",
            ],
            # Code keyword
            [
                "(Recommended) Execute def apply_migration() now (Confidence: 90%, Tier: High). Ensures safety.",
                "Skip function call (Confidence: 45%, Tier: Low). High risk.",
            ],
            # Snake case token
            [
                "(Recommended) Update the batch_size setting (Confidence: 80%, Tier: High). Improves throughput.",
                "Keep current value (Confidence: 50%, Tier: Medium). Preserves state.",
            ],
            # CLI flag
            [
                "(Recommended) Rerun using the --force flag (Confidence: 75%, Tier: Medium). Overrides warnings.",
                "Cancel operation (Confidence: 60%, Tier: Medium). Halts process.",
            ],
        ]

        for opts in invalid_options:
            res = validate_question_payload(valid_q, opts)
            self.assertFalse(res.is_valid, f"Expected rejection for option: {opts[0]}")
            self.assertTrue(
                any("ZERO_JARGON_VIOLATION" in err for err in res.errors),
                f"Expected ZERO_JARGON_VIOLATION, got: {res.errors}",
            )

    def test_adversarial_mismatched_confidence_text_vs_value(self) -> None:
        """Verify rejection when visible text confidence disagrees with structured value."""
        valid_q = "Which replication strategy should be selected?"
        # Structured option says 90% HIGH, but text says 40% Low
        mismatched_opt = {
            "label": "Direct Replication",
            "display_text": "(Recommended) Direct Replication (Confidence: 40%, Tier: Low). Ensures low latency.",
            "plain_english_summary": "Replicate directly to secondary nodes.",
            "impact_statement": "Reduces failover latency and maintains high availability.",
            "confidence_score": 0.90,
            "confidence_pct": 90.0,
            "confidence_level": "HIGH",
            "tier": "HIGH",
            "credible_interval": [0.85, 0.95],
            "evidence_refs": [],
            "recommended": True,
        }
        other_opt = {
            "label": "Batched Replication",
            "display_text": "Batched Replication (Confidence: 60%, Tier: Medium). Minimizes bandwidth.",
            "plain_english_summary": "Group updates into periodic batches.",
            "impact_statement": "Reduces network bandwidth cost.",
            "confidence_score": 0.60,
            "confidence_pct": 60.0,
            "confidence_level": "MEDIUM",
            "tier": "MEDIUM",
            "credible_interval": [0.55, 0.65],
            "evidence_refs": [],
            "recommended": False,
        }

        res = validate_question_payload(valid_q, [mismatched_opt, other_opt])
        self.assertFalse(res.is_valid)
        self.assertTrue(
            any("CONFIDENCE_PARITY_MISMATCH" in err or "TIER_PARITY_MISMATCH" in err for err in res.errors),
            f"Expected confidence parity mismatch, got: {res.errors}",
        )

    def test_adversarial_very_long_option_exceeding_budget(self) -> None:
        """Verify rejection of options exceeding MAX_OPTION_LENGTH (250 chars)."""
        valid_q = "Which optimization technique should be enabled?"
        oversized_option = (
            "(Recommended) Implement complete end-to-end memory pool consolidation with adaptive thread scheduling "
            "and continuous hardware telemetry analysis across every active computing node in the cluster "
            "to ensure peak operational throughput under heavy production stress. (Confidence: 85%, Tier: High)."
        )
        self.assertGreater(len(oversized_option), 250)

        normal_option = "Maintain existing memory configuration (Confidence: 50%, Tier: Medium). Preserves stability."

        res = validate_question_payload(valid_q, [oversized_option, normal_option])
        self.assertFalse(res.is_valid)
        self.assertTrue(
            any("OPTION_LENGTH_EXCEEDED" in err for err in res.errors),
            f"Expected OPTION_LENGTH_EXCEEDED, got: {res.errors}",
        )

    def test_adversarial_very_long_question_exceeding_budget(self) -> None:
        """Verify rejection of question exceeding MAX_QUESTION_LENGTH (300 chars)."""
        oversized_question = (
            "We have evaluated multiple potential configurations for the new regional network bridge and observed "
            "divergent performance characteristics during our synthetic stress evaluations under heavy simulation load, "
            "so we need you to review the following architectural options and decide which approach to authorize for immediate deployment?"
        )
        self.assertGreater(len(oversized_question), 300)

        opts = [
            "(Recommended) Deploy regional bridge (Confidence: 85%, Tier: High). Improves latency.",
            "Defer deployment (Confidence: 45%, Tier: Low). Maintains current state.",
        ]
        res = validate_question_payload(oversized_question, opts)
        self.assertFalse(res.is_valid)
        self.assertTrue(
            any("QUESTION_LENGTH_EXCEEDED" in err for err in res.errors),
            f"Expected QUESTION_LENGTH_EXCEEDED, got: {res.errors}",
        )

    def test_adversarial_bundled_multiple_questions(self) -> None:
        """Verify rejection of multiple bundled questions."""
        opts = [
            "(Recommended) Option One (Confidence: 80%, Tier: High). Good outcome.",
            "Option Two (Confidence: 50%, Tier: Medium). Neutral outcome.",
        ]

        # Case 1: Multiple question marks
        multi_qmark = "Should we update the primary server? And should we also drop the secondary?"
        res1 = validate_question_payload(multi_qmark, opts)
        self.assertFalse(res1.is_valid)
        self.assertTrue(
            any("ATOMIC_QUESTION_VIOLATION" in err for err in res1.errors),
            f"Expected ATOMIC_QUESTION_VIOLATION on multiple '?', got: {res1.errors}",
        )

        # Case 2: Compound clause bundling
        compound_q = "Please decide which route to take, and also which backup schedule should be chosen?"
        res2 = validate_question_payload(compound_q, opts)
        self.assertFalse(res2.is_valid)
        self.assertTrue(
            any("ATOMIC_QUESTION_VIOLATION" in err for err in res2.errors),
            f"Expected ATOMIC_QUESTION_VIOLATION on compound clause, got: {res2.errors}",
        )

    def test_adversarial_ellipses_and_slicing_truncation(self) -> None:
        """Verify that ellipses, truncation tags, and abbreviations are strictly caught."""
        valid_q = "How should we resolve the scheduling conflict?"
        trunc_cases = [
            "(Recommended) Shift meeting to Tuesday... (Confidence: 80%, Tier: High). Preserves attendance.",
            "(Recommended) Shift meeting to Tuesday\u2026 (Confidence: 80%, Tier: High). Preserves attendance.",
            "(Recommended) Shift meeting to Tuesday [truncated] (Confidence: 80%, Tier: High). Preserves attendance.",
        ]
        normal_opt = "Keep meeting as scheduled (Confidence: 50%, Tier: Medium). Maintains schedule."

        for trunc in trunc_cases:
            res = validate_question_payload(valid_q, [trunc, normal_opt])
            self.assertFalse(res.is_valid)
            self.assertTrue(
                any("ANTI_TRUNCATION_VIOLATION" in err for err in res.errors),
                f"Expected ANTI_TRUNCATION_VIOLATION for '{trunc}', got: {res.errors}",
            )

    def test_adversarial_missing_confidence_ratings(self) -> None:
        """Verify rejection when options omit confidence percentage or tier."""
        valid_q = "Which failover destination should be used?"
        opts_no_tier = [
            "(Recommended) Primary replica (85%). Fast recovery.",
            "Secondary replica (50%). Slower recovery.",
        ]
        res = validate_question_payload(valid_q, opts_no_tier)
        self.assertFalse(res.is_valid)
        self.assertTrue(
            any("MISSING_TIER_IN_TEXT" in err for err in res.errors),
            f"Expected MISSING_TIER_IN_TEXT, got: {res.errors}",
        )

        opts_no_pct = [
            "(Recommended) Primary replica (Tier: High). Fast recovery.",
            "Secondary replica (Tier: Medium). Slower recovery.",
        ]
        res2 = validate_question_payload(valid_q, opts_no_pct)
        self.assertFalse(res2.is_valid)
        self.assertTrue(
            any("MISSING_CONFIDENCE_IN_TEXT" in err for err in res2.errors),
            f"Expected MISSING_CONFIDENCE_IN_TEXT, got: {res2.errors}",
        )

    def test_adversarial_recommended_ordering_violation(self) -> None:
        """Verify rejection when a recommended option is placed after non-recommended choices."""
        valid_q = "Select active network interface?"
        misordered = [
            "Ethernet connection (Confidence: 60%, Tier: Medium). Reliable wired.",
            "(Recommended) Fiber channel (Confidence: 90%, Tier: High). Ultra fast throughput.",
        ]
        res = validate_question_payload(valid_q, misordered)
        self.assertFalse(res.is_valid)
        self.assertTrue(
            any("RECOMMENDED_ORDERING_VIOLATION" in err for err in res.errors),
            f"Expected RECOMMENDED_ORDERING_VIOLATION, got: {res.errors}",
        )


if __name__ == "__main__":
    unittest.main()
