"""
tests/test_backtest_mode.py

Unit tests for Wave 3: Backtest Composite Flag (--backtest) and run-over-run comparison reporting.
Compatible with pytest and run_tests_with_progress.py.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.backtest_core.comparison import (
    diff_entities_and_assertions,
    diff_output2_sections,
    generate_backtest_comparison_report,
)
from dual_pipeline.cli.ingest import build_canonical_metadata
from dual_pipeline.cli.parser import build_parser
from tools.validate_output_hierarchy_core.constants import CONTROLLED_SUPPORTING_FILES


class TestBacktestMode(unittest.TestCase):
    """Unit test suite for Wave 3 --backtest flag and comparison report generator."""

    def setUp(self) -> None:
        self.parser = build_parser()

    def test_cli_backtest_flag_defaults(self) -> None:
        """Verify --backtest expands to force_rerun, fast judge tier, and regenerate 02,03."""
        args = self.parser.parse_args([
            "ingest-meeting",
            "--input", "sample.vtt",
            "--project", "test_proj",
            "--backtest",
        ])
        self.assertTrue(args.backtest)
        self.assertTrue(args.force_rerun)
        self.assertEqual(args.judge_tier, "fast")
        self.assertEqual(args.regenerate, "02,03")

    def test_cli_backtest_judge_tier_override(self) -> None:
        """Verify explicit --judge-tier overrides the default fast tier implied by --backtest."""
        # Flag after --backtest
        args1 = self.parser.parse_args([
            "ingest-meeting",
            "--input", "sample.vtt",
            "--project", "test_proj",
            "--backtest",
            "--judge-tier", "standard",
        ])
        self.assertTrue(args1.backtest)
        self.assertEqual(args1.judge_tier, "standard")

        # Flag before --backtest
        args2 = self.parser.parse_args([
            "ingest-meeting",
            "--input", "sample.vtt",
            "--project", "test_proj",
            "--judge-tier", "standard",
            "--backtest",
        ])
        self.assertTrue(args2.backtest)
        self.assertEqual(args2.judge_tier, "standard")

    def test_cli_backtest_regenerate_override(self) -> None:
        """Verify explicit --regenerate overrides the 02,03 default implied by --backtest."""
        args = self.parser.parse_args([
            "ingest-meeting",
            "--input", "sample.vtt",
            "--project", "test_proj",
            "--backtest",
            "--regenerate", "03",
        ])
        self.assertTrue(args.backtest)
        self.assertEqual(args.regenerate, "03")

    def test_cli_backtest_ingest_document(self) -> None:
        """Verify --backtest on ingest-document implies force_rerun and fast judge tier."""
        args = self.parser.parse_args([
            "ingest-document",
            "--input", "doc.txt",
            "--project", "test_proj",
            "--backtest",
        ])
        self.assertTrue(args.backtest)
        self.assertTrue(args.force_rerun)
        self.assertEqual(args.judge_tier, "fast")

    def test_build_canonical_metadata_backtest(self) -> None:
        """Verify build_canonical_metadata tags run_mode: backtest and expands defaults."""
        args = self.parser.parse_args([
            "ingest-meeting",
            "--input", "sample.vtt",
            "--project", "test_proj",
            "--date", "10/01/2026",
            "--backtest",
        ])
        meta = build_canonical_metadata(args)
        self.assertTrue(meta.get("backtest"))
        self.assertEqual(meta.get("run_mode"), "backtest")
        self.assertTrue(meta.get("force_rerun"))
        self.assertEqual(meta.get("judge_tier"), "fast")
        self.assertEqual(meta.get("regenerate"), "02,03")

    def test_build_canonical_metadata_backtest_overrides(self) -> None:
        """Verify build_canonical_metadata respects individual overrides when backtest active."""
        args = self.parser.parse_args([
            "ingest-meeting",
            "--input", "sample.vtt",
            "--project", "test_proj",
            "--date", "10/01/2026",
            "--backtest",
            "--judge-tier", "standard",
            "--regenerate", "03",
        ])
        meta = build_canonical_metadata(args)
        self.assertTrue(meta.get("backtest"))
        self.assertEqual(meta.get("run_mode"), "backtest")
        self.assertEqual(meta.get("judge_tier"), "standard")
        self.assertEqual(meta.get("regenerate"), "03")

    def test_diff_output2_sections(self) -> None:
        """Verify section-level diff correctly classifies IDENTICAL, MODIFIED, ADDED."""
        prior = (
            "# Executive Recap\n\n"
            "## Meeting Objective\nAlign on Q4 timeline.\n\n"
            "## Key Strategic Updates\nProgress made on Wave 1.\n"
        )
        current = (
            "# Executive Recap\n\n"
            "## Meeting Objective\nAlign on Q4 timeline.\n\n"
            "## Key Strategic Updates\nProgress made on Wave 1 and Wave 2 with cost optimization.\n\n"
            "## Action & Governance Ledger\n1. Deploy updates.\n"
        )
        diffs = diff_output2_sections(prior, current)
        sec_map = {d["section"]: d for d in diffs}

        self.assertEqual(sec_map["Meeting Objective"]["status"], "IDENTICAL")
        self.assertEqual(sec_map["Key Updates & Decisions"]["status"], "MODIFIED")
        self.assertGreater(sec_map["Key Updates & Decisions"]["delta_words"], 0)
        self.assertEqual(sec_map["Action & Governance Ledger"]["status"], "ADDED")
        self.assertEqual(sec_map["Persona Appendices"]["status"], "NOT_PRESENT")

    def test_diff_entities_and_assertions(self) -> None:
        """Verify graph deltas compute accurate entity and assertion counts."""
        prior_graph = {
            "entities": [{"id": "person:alice"}, {"id": "org:partner_advisory"}],
            "assertions": [{"id": "assert:1", "claim_text": "Alice joined PartnerAdvisory"}],
            "relationships": [{"source": "person:alice", "target": "org:partner_advisory"}],
        }
        current_graph = {
            "entities": [{"id": "person:alice"}, {"id": "org:apex_global"}],
            "assertions": [
                {"id": "assert:1", "claim_text": "Alice joined PartnerAdvisory"},
                {"id": "assert:2", "claim_text": "Alice works on Nexus"},
            ],
            "relationships": [
                {"source": "person:alice", "target": "org:apex_global"},
                {"source": "person:alice", "target": "project:nexus"},
            ],
        }
        delta = diff_entities_and_assertions(prior_graph, current_graph)

        self.assertEqual(delta["entities_prior_count"], 2)
        self.assertEqual(delta["entities_current_count"], 2)
        self.assertIn("org:apex_global", delta["entities_added"])
        self.assertIn("org:partner_advisory", delta["entities_removed"])
        self.assertEqual(delta["entities_retained_count"], 1)

        self.assertEqual(delta["assertions_prior_count"], 1)
        self.assertEqual(delta["assertions_current_count"], 2)
        self.assertEqual(delta["assertions_retained_count"], 1)
        self.assertEqual(delta["relationships_delta"], 1)

    def test_generate_backtest_comparison_report(self) -> None:
        """Verify report generation produces markdown artifact conforming to cost and schema invariants."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir)
            prior_f2 = "# Executive Recap\n\n## Meeting Objective\nPrior text."
            prior_f3 = json.dumps({"entities": [{"id": "person:bob"}], "assertions": []})

            (out_path / "02_test_recap.md").write_text(prior_f2, encoding="utf-8")
            (out_path / "03_test_signals.json").write_text(prior_f3, encoding="utf-8")

            curr_f2 = "# Executive Recap\n\n## Meeting Objective\nCurrent updated text.\n\n## Key Updates\nShipped backtest mode."
            curr_f3 = {"entities": [{"id": "person:bob"}, {"id": "project:backtest"}], "assertions": [{"id": "a1"}]}

            rep_path = generate_backtest_comparison_report(
                prior_dir=out_path,
                current_output_2=curr_f2,
                current_output_3=curr_f3,
                output_dir=out_path,
                metadata={
                    "run_id": "backtest-test-run-1",
                    "meeting_name": "Test Meeting",
                    "project": "project_nexus",
                    "date": "10/01/2026",
                    "backtest_cost": 0.0820,
                    "prior_cost": 1.0200,
                },
            )

            self.assertTrue(rep_path.is_file())
            content = rep_path.read_text(encoding="utf-8")
            self.assertIn("# Run-Over-Run Backtest Comparison Report", content)
            self.assertIn("## 1. Executive Cost & Throughput Delta", content)
            self.assertIn("## 2. Output 02: Executive Recap Section Diffs", content)
            self.assertIn("## 3. Output 03: Knowledge Graph Entity & Assertion Delta", content)
            self.assertIn("## 4. Stability & Quality Gate Verification", content)
            self.assertIn("$0.0820", content)
            self.assertIn("< $0.25", content)
            self.assertIn("-94.0% judge unit cost", content)
            self.assertIn("project:backtest", content)

    def test_output_hierarchy_recognizes_comparison_report(self) -> None:
        """Verify that backtest_comparison_report.md is in CONTROLLED_SUPPORTING_FILES."""
        self.assertIn("backtest_comparison_report.md", CONTROLLED_SUPPORTING_FILES)


if __name__ == "__main__":
    unittest.main()
