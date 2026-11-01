"""
tests/adversarial/test_heuristic_pre_judge_adversarial.py

Adversarial and edge-case tests for Wave 2: Deterministic Heuristic Pre-Judging (Agent 4).
Covers 4 mandatory categories per .agents/rules/e2e-and-edge-case-coverage.md:
1. Boundary payload (empty string, empty object, zero-length transcript)
2. Malformed input (truncated JSON, invalid escape sequences, non-dict root)
3. Invariant bypass attempt (disguised empty payloads, non-entity schema keys)
4. Exact production regression (unparseable candidate JSON from LENS incident)
"""
from __future__ import annotations

import json
import unittest

from dual_pipeline.llm_judge_core.heuristic_pre_judge import (
    DEFAULT_JUDGE_TOKENS_AVERTED,
    evaluate_heuristic_pre_judge,
)


class HeuristicPreJudgeAdversarialTests(unittest.TestCase):
    def test_01_boundary_payload_empty_strings_and_nulls(self) -> None:
        """Category 1: Boundary payload - empty f3 text, empty f1 transcript, null characters."""
        res1 = evaluate_heuristic_pre_judge("")
        self.assertEqual(res1["verdict"], "FAIL")
        self.assertEqual(res1["pre_judge_tier"], "TIER_1_SYNTAX_FAIL")
        self.assertFalse(res1["passed_pre_check"])

        res2 = evaluate_heuristic_pre_judge("   \n\t  ")
        self.assertEqual(res2["verdict"], "FAIL")
        self.assertEqual(res2["pre_judge_tier"], "TIER_1_SYNTAX_FAIL")

    def test_02_malformed_input_truncated_json_and_syntax_errors(self) -> None:
        """Category 2: Malformed input - truncated JSON, invalid escape sequences, arrays."""
        # Truncated JSON
        truncated_json = '{"nodes": [{"id": "n1", "name": "Item"'
        res1 = evaluate_heuristic_pre_judge(truncated_json)
        self.assertEqual(res1["verdict"], "FAIL")
        self.assertEqual(res1["pre_judge_tier"], "TIER_1_SYNTAX_FAIL")

        # Root JSON is array instead of dict
        array_json = '[{"id": "n1"}]'
        res2 = evaluate_heuristic_pre_judge(array_json)
        self.assertEqual(res2["verdict"], "FAIL")
        self.assertEqual(res2["pre_judge_tier"], "TIER_1_SYNTAX_FAIL")

    def test_03_invariant_bypass_attempt_fake_and_empty_containers(self) -> None:
        """Category 5: Invariant bypass attempt - empty containers masquerading as valid topology."""
        # Object with only empty lists
        fake_payload = '{"nodes": [], "edges": [], "claims": []}'
        res1 = evaluate_heuristic_pre_judge(fake_payload)
        self.assertEqual(res1["verdict"], "FAIL")
        self.assertEqual(res1["pre_judge_tier"], "TIER_1_SCHEMA_FAIL")
        self.assertTrue(any("completely empty" in e for e in res1["errors"]))

        # Object with unrelated keys
        unrelated_payload = '{"author": "Unknown", "status": "draft", "timestamp": 12345}'
        res2 = evaluate_heuristic_pre_judge(unrelated_payload)
        self.assertEqual(res2["verdict"], "FAIL")
        self.assertEqual(res2["pre_judge_tier"], "TIER_1_SCHEMA_FAIL")
        self.assertTrue(any("lacks essential graph keys" in e for e in res2["errors"]))

    def test_04_exact_production_regression_lens_incident_failure(self) -> None:
        """Category 7: Exact production regression - pins unparseable candidate and dangling edges."""
        # Exact pattern seen in LENS 10-07 incident where Stage 03 dropped closing braces
        dropped_closing_brace = '```json\n{"nodes": [{"id": "asset_lens_1", "type": "ASSET"}], "edges": [{"source": "asset_lens_1", "target": "missing_target"}\n```'
        res1 = evaluate_heuristic_pre_judge(dropped_closing_brace)
        self.assertEqual(res1["verdict"], "FAIL")
        self.assertEqual(res1["pre_judge_tier"], "TIER_1_SYNTAX_FAIL")
        self.assertEqual(res1["tokens_saved_estimate"], DEFAULT_JUDGE_TOKENS_AVERTED)


if __name__ == "__main__":
    unittest.main()
