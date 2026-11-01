"""
tests/test_judge_tier_routing.py

Unit tests for Wave 2: Judge Tier Downgrade for Backtests (--judge-tier).
Validates:
  1. CLI argument parsing and validation (--judge-tier fast|standard|production).
  2. Metadata propagation of judge_tier.
  3. Model and reasoning effort resolution per tier.
  4. Receipt and report annotation (judge_tier, judge_model in judge_report and final_decision).
  5. Calibration reporting utility output and concordance calculations.
"""
from __future__ import annotations

import argparse
import unittest
from typing import Any, Dict

from dual_pipeline.cli.ingest import build_canonical_metadata
from dual_pipeline.cli.parser import build_parser
from dual_pipeline.transcript_qa_core.evaluator import (
    _resolve_judge_tier_and_model,
    evaluate_judge_and_release,
)
from tools.calibrate_judge_tiers import (
    TierEvaluationResult,
    compute_concordance,
    run_calibration,
)


class TestJudgeTierRouting(unittest.TestCase):
    """Unit test suite for Wave 2 judge tier routing and verification."""

    def setUp(self) -> None:
        self.parser = build_parser()
        self.sample_f1 = "# Forensic Transcript\n\n**Alice**: Let's discuss roadmap."
        self.sample_f2 = "# Executive Recap\n\n## Meeting Objective\nRoadmap review."
        self.sample_f3 = '{"entities": [{"id": "person:alice"}], "assertions": []}'

    def test_cli_parser_judge_tier_default(self) -> None:
        args = self.parser.parse_args(["ingest-meeting", "--input", "sample.vtt", "--project", "test_proj"])
        self.assertEqual(args.judge_tier, "production")

    def test_cli_parser_judge_tier_fast(self) -> None:
        args = self.parser.parse_args([
            "ingest-meeting",
            "--input", "sample.vtt",
            "--project", "test_proj",
            "--judge-tier", "fast",
        ])
        self.assertEqual(args.judge_tier, "fast")

    def test_cli_parser_judge_tier_standard(self) -> None:
        args = self.parser.parse_args([
            "ingest-meeting",
            "--input", "sample.vtt",
            "--project", "test_proj",
            "--judge-tier", "standard",
        ])
        self.assertEqual(args.judge_tier, "standard")

    def test_cli_parser_judge_tier_invalid(self) -> None:
        with self.assertRaises(SystemExit):
            self.parser.parse_args([
                "ingest-meeting",
                "--input", "sample.vtt",
                "--project", "test_proj",
                "--judge-tier", "invalid_tier",
            ])

    def test_build_canonical_metadata_ignores_judge_tier(self) -> None:
        args = argparse.Namespace(
            project="test_proj",
            meeting="test_meeting",
            date="10-01-2026",
            judge_tier="fast",
            metadata=None,
        )
        meta = build_canonical_metadata(args)
        self.assertIsNone(meta.get("judge_tier"))

    def test_resolve_judge_tier_model_fast(self) -> None:
        args = argparse.Namespace(judge_tier="fast", judge_model=None, reasoning_effort=None)
        tier, model, effort = _resolve_judge_tier_and_model(meta={}, model=args.judge_model, reasoning_effort=args.reasoning_effort, judge_tier=args.judge_tier, kwargs={})
        self.assertEqual(tier, "fast")
        self.assertEqual(model, "gpt-6-luna")
        self.assertEqual(effort, "low")

    def test_resolve_judge_tier_model_standard(self) -> None:
        args = argparse.Namespace(judge_tier="standard", judge_model=None, reasoning_effort=None)
        tier, model, effort = _resolve_judge_tier_and_model(meta={}, model=args.judge_model, reasoning_effort=args.reasoning_effort, judge_tier=args.judge_tier, kwargs={})
        self.assertEqual(tier, "standard")
        self.assertEqual(model, "gpt-6.1-sol")
        self.assertEqual(effort, "high")

    def test_resolve_judge_tier_model_production(self) -> None:
        args = argparse.Namespace(judge_tier="production", judge_model=None, reasoning_effort=None)
        tier, model, effort = _resolve_judge_tier_and_model(meta={}, model=args.judge_model, reasoning_effort=args.reasoning_effort, judge_tier=args.judge_tier, kwargs={})
        self.assertEqual(tier, "production")
        self.assertEqual(model, "gpt-6-luna")
        self.assertEqual(effort, "max")

    def test_resolve_judge_tier_and_model_evaluator_helper(self) -> None:
        # Fast tier
        tier, model, effort = _resolve_judge_tier_and_model({}, None, None, "fast", {})
        self.assertEqual(tier, "fast")
        self.assertEqual(model, "gpt-6-luna")
        self.assertEqual(effort, "low")

        # Standard tier
        tier, model, effort = _resolve_judge_tier_and_model({}, None, None, "standard", {})
        self.assertEqual(tier, "standard")
        self.assertEqual(model, "gpt-6.1-sol")
        self.assertEqual(effort, "high")

        # Production tier
        tier, model, effort = _resolve_judge_tier_and_model({}, None, None, "production", {})
        self.assertEqual(tier, "production")
        self.assertEqual(model, "gpt-6-luna")
        self.assertEqual(effort, "max")

    def test_evaluate_judge_and_release_annotations_fast(self) -> None:
        pinned: Dict[str, Any] = {
            "output_1_evaluation": {"status": "PASS", "composite_score": 95, "verdict": "PASS"},
            "output_2_evaluation": {"status": "PASS", "composite_score": 95, "verdict": "PASS"},
            "output_3_evaluation": {"status": "PASS", "composite_score": 95, "verdict": "PASS"},
        }
        _, judge_report, final_decision = evaluate_judge_and_release(
            f1_text=self.sample_f1,
            f2_text=self.sample_f2,
            f3_text=self.sample_f3,
            source_text=self.sample_f1,
            metadata={"judge_tier": "fast"},
            mock=True,
            pinned_evaluations=pinned,
        )

        self.assertEqual(judge_report.get("judge_tier"), "fast")
        self.assertEqual(judge_report.get("judge_model"), "gpt-6-luna")
        self.assertEqual(judge_report.get("reasoning_effort"), "low")
        self.assertEqual(final_decision.get("judge_tier"), "fast")
        self.assertEqual(final_decision.get("judge_model"), "gpt-6-luna")

    def test_compute_concordance_metrics(self) -> None:
        prod = TierEvaluationResult(
            tier="production", model="gpt-6-luna", effort="max",
            output_1_score=98, output_1_verdict="PASS",
            output_2_score=96, output_2_verdict="PASS",
            output_3_score=98, output_3_verdict="PASS",
            reconciliation_status="PASS", overall_status="PASS",
            estimated_cost=0.1190, input_tokens=38000, output_tokens=2400,
        )
        fast = TierEvaluationResult(
            tier="fast", model="gpt-6-luna", effort="low",
            output_1_score=96, output_1_verdict="PASS",
            output_2_score=94, output_2_verdict="PASS",
            output_3_score=95, output_3_verdict="PASS",
            reconciliation_status="PASS", overall_status="PASS",
            estimated_cost=0.0071, input_tokens=38000, output_tokens=2400,
        )

        c = compute_concordance(prod, fast)
        self.assertTrue(c["verdict_match"])
        self.assertGreater(c["mean_score_concordance_pct"], 95.0)
        self.assertGreater(c["cost_savings_pct"], 90.0)

    def test_run_calibration_smoke(self) -> None:
        report = run_calibration(meeting_arg="output/project_nexus/09-10-2026")
        self.assertIn("tiers", report)
        self.assertIn("production", report["tiers"])
        self.assertIn("fast", report["tiers"])
        self.assertIn("standard", report["tiers"])
        self.assertIn("concordance_matrix", report)
        self.assertTrue(report["concordance_matrix"]["fast_vs_production"]["verdict_match"])


if __name__ == "__main__":
    unittest.main()
