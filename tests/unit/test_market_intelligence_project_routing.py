"""Unit tests for market_intelligence project routing and metadata resolution."""
from __future__ import annotations

import unittest
from pathlib import Path
import tempfile

from dual_pipeline.parameters import get_known_projects, resolve_project_name
from dual_pipeline.query_router_core.project_router import (
    determine_project_directory,
    _resolve_project_from_title,
)
from dual_pipeline.transcript_metadata_helper import scaffold_meeting_metadata


class TestMarketIntelligenceProjectRouting(unittest.TestCase):
    def test_known_projects_and_aliases(self):
        known = get_known_projects()
        self.assertIn("market_intelligence", known)
        self.assertEqual(resolve_project_name("market_intelligence"), "market_intelligence")
        self.assertEqual(resolve_project_name("energy utilities"), "market_intelligence")
        self.assertEqual(resolve_project_name("weekly trends"), "market_intelligence")
        self.assertEqual(resolve_project_name("energy and utilities trends"), "market_intelligence")
        self.assertEqual(resolve_project_name("energy & utilities trends"), "market_intelligence")

    def test_determine_project_directory_from_title(self):
        cases = [
            ("general energy and utilities trends", "market_intelligence"),
            ("general energy and utilitieds trends", "market_intelligence"),
            ("Weekly Energy & Utilities Market Trends - 10/13/2026", "market_intelligence"),
            ("Weekly Energy & Utilities Market Trends Update - 10/05/2026", "market_intelligence"),
            ("Weekly Market Trends", "market_intelligence"),
            ("Energy & Utilities Update", "market_intelligence"),
        ]
        for title, expected in cases:
            with self.subTest(title=title):
                res = determine_project_directory({"title": title}, allow_llm=False)
                self.assertEqual(res, expected, f"Failed for title: {title}")

    def test_determine_project_directory_from_input_path(self):
        res = determine_project_directory(
            {"input_path": "documents/market_intelligence/weekly_update_10-13-2026.md"},
            allow_llm=False,
        )
        self.assertEqual(res, "market_intelligence")

    def test_scaffold_meeting_metadata_for_market_intelligence(self):
        with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
            f.write("# Weekly Energy & Utilities Market Trends Update - 10/13/2026\n\nContent here.")
            f_path = f.name

        try:
            meta = scaffold_meeting_metadata(
                input_path=f_path,
                meeting_name="Weekly Energy & Utilities Market Trends - 10/13/2026",
            )
            self.assertEqual(meta.get("project"), "market_intelligence")
            self.assertNotEqual(meta.get("project"), "project_nexus")
        finally:
            Path(f_path).unlink(missing_ok=True)

    def test_nexus_regression_preservation(self):
        # Titles mentioning Nexus explicitly must still resolve to project_nexus
        res = determine_project_directory(
            {"title": "Project Nexus Scope & Charter"}, allow_llm=False
        )
        self.assertEqual(res, "project_nexus")

        res2 = determine_project_directory(
            {"title": "Nexus Weekly Trends Review"}, allow_llm=False
        )
        self.assertEqual(res2, "project_nexus")


if __name__ == "__main__":
    unittest.main()
