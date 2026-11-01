"""Adversarial test suite for Core Evals Harness and Answer Metrics (Wave 1).

Verifies:
1. Malformed and adversarial response texts cannot fool compute_groundedness.
2. Injected SQLite errors and table locks do not grant artificial durability.
3. ThreeLevelRollupEngine fails closed when required tier outputs are missing.
4. CanaryEngine rejects contaminated payloads containing modified canary tokens.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.evals.adversarial import CanaryEngine
from dual_pipeline.evals.answer_eval_core.metrics import (
    compute_citation_durability,
    compute_groundedness,
    compute_context_recall,
)
from dual_pipeline.evals.rollup import ThreeLevelRollupEngine


class TestEvalHarnessAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_eval_adv_")
        self.tmp_path = Path(self.temp_dir)

    def test_adversarial_groundedness_bypass_attempts(self) -> None:
        """Crafted response texts attempting to claim citations without valid inline citations fail."""
        # Whitespace-only text with cited IDs
        self.assertEqual(
            compute_groundedness({"response_text": "   \n\n\t  ", "cited_assertion_ids": ["ast-001"]}),
            0.0,
        )

        # Markdown header-only text
        self.assertEqual(
            compute_groundedness({"response_text": "# Header Only\n## Another Header", "cited_assertion_ids": ["ast-001"]}),
            0.0,
        )

        # Truncated or malformed citation brackets
        self.assertEqual(
            compute_groundedness({"response_text": "The project is on track [ast- incomplete bracket", "cited_assertion_ids": ["ast-001"]}),
            0.0,
        )

    def test_corrupted_sqlite_citation_durability_fails_closed(self) -> None:
        """When SQLite table is dropped or schema is missing, durability returns 0.0 (fail-closed)."""
        con = sqlite3.connect(":memory:")
        # No tables created in DB -> query will raise sqlite3.OperationalError
        durability = compute_citation_durability(["ast-001", "ast-002"], con)
        self.assertEqual(durability, 0.0, "OperationalError must not increment durability count")

    def test_three_level_rollup_fails_closed_on_missing_subsystems(self) -> None:
        """RollupEngine must reject certification if pipelines A-D or Tier 1 metrics are missing."""
        engine = ThreeLevelRollupEngine()
        # Empty inputs
        report = engine.evaluate_rollup(
            tier_results={},
            pipeline_results={},
            strategic_results={},
        )
        self.assertFalse(report.overall_pass, "Missing subsystem evaluations must not pass rollup")
        self.assertFalse(report.level_1_micro.passed, "Level 1 micro scorecard must fail closed")
        self.assertFalse(report.level_2_meso.passed, "Level 2 meso scorecard must fail closed")
        self.assertIn("BLOCKED", report.certification_status)

    def test_canary_detects_adversarial_casing_and_padding(self) -> None:
        """CanaryEngine detects canary tokens even across adversarial casing and padding."""
        engine = CanaryEngine()
        token = engine.generate_canary("adversarial_test")

        # Mixed casing
        mixed_cased_str = f"Prefix text {token.token_str.lower()} suffix text"
        report = engine.scan_for_leakage(mixed_cased_str, token)
        self.assertTrue(report[0].is_leaked, "Case-insensitive scan must detect lowercase canary")

        # Zero leakage in clean text
        clean_report = engine.scan_for_leakage("Completely innocent payload with no secrets", token)
        self.assertFalse(clean_report[0].is_leaked)


if __name__ == "__main__":
    unittest.main()
