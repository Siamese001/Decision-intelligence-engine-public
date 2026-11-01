"""
tests/unit/test_market_intelligence_adversarial.py

Adversarial and edge-case test suite for market_intelligence project routing.
Covers 4 mandatory categories under .agents/rules/e2e-and-edge-case-coverage.md:
1. Boundary payload: empty title, whitespace, 0-byte document, missing metadata fields.
2. Malformed input: typos ('utilitieds'), extreme symbol padding, unicode corruption.
3. Invariant bypass attempt: namespace confusion ('nexus market trends'), project fallback injection.
4. Exact production regression: 'general energy and utilitieds trends' resolves to market_intelligence, never project_nexus.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.parameters import resolve_project_name
from dual_pipeline.query_router_core.project_router import (
    determine_project_directory,
    _resolve_project_from_title,
)
from dual_pipeline.transcript_metadata_helper import scaffold_meeting_metadata


class TestMarketIntelligenceAdversarial(unittest.TestCase):
    # Category 1: Boundary payload
    def test_boundary_empty_and_whitespace_title(self):
        """Empty or whitespace-only inputs should not crash or erroneously resolve to project_nexus."""
        for empty_val in ["", "   ", "\t\n", None]:
            metadata = {"title": empty_val} if empty_val is not None else {}
            res = determine_project_directory(metadata, allow_llm=False)
            self.assertNotEqual(
                res,
                "market_intelligence",
                "Empty title must not match market intelligence",
            )
            # Must safely return None when LLM is disallowed without exception
            self.assertIsNone(res)

    def test_boundary_zero_byte_document_metadata_scaffold(self):
        """A 0-byte document with market trends name correctly resolves market_intelligence."""
        with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
            f_path = Path(f.name)
        try:
            meta = scaffold_meeting_metadata(
                input_path=f_path,
                meeting_name="Weekly Energy & Utilities Market Trends",
            )
            self.assertEqual(meta.get("project"), "market_intelligence")
            self.assertNotEqual(meta.get("project"), "project_nexus")
        finally:
            f_path.unlink(missing_ok=True)

    # Category 2: Malformed input
    def test_malformed_input_typo_resilience(self):
        """Misspelling 'utilities' as 'utilitieds' (as in operator prompt) must resolve correctly."""
        typo_titles = [
            "general energy and utilitieds trends",
            "ENERGY & UTILITIEDS TRENDS",
            "weekly energy and utilitieds update",
            "### !!! [URGENT] general energy and utilitieds trends ??? ***",
        ]
        for title in typo_titles:
            with self.subTest(title=title):
                res = determine_project_directory({"title": title}, allow_llm=False)
                self.assertEqual(
                    res,
                    "market_intelligence",
                    f"Typo resilience failed for: {title}",
                )

    def test_malformed_unicode_and_excessive_length(self):
        """Handles unicode symbols, zero-width spaces, and large payload titles gracefully."""
        unicode_title = "⚡️ Weekly Energy & Utilities Market Trends 🔋 \u200b\u200c - 2026"
        res = determine_project_directory({"title": unicode_title}, allow_llm=False)
        self.assertEqual(res, "market_intelligence")

        long_title = "General Energy Trends " + ("Analysis " * 200) + "Utilities"
        res_long = determine_project_directory({"title": long_title}, allow_llm=False)
        self.assertEqual(res_long, "market_intelligence")

    # Category 3: Invariant bypass attempt
    def test_invariant_bypass_nexus_precedence_and_isolation(self):
        """Explicit Nexus titles must remain project_nexus, while pure market titles isolate to market_intelligence."""
        # Nexus explicitly named takes precedence for project_nexus
        nexus_attempt = determine_project_directory(
            {"title": "Project Nexus General Trends"},
            allow_llm=False,
        )
        self.assertEqual(nexus_attempt, "project_nexus")

        # Market trends without nexus cannot leak into project_nexus
        market_attempt = determine_project_directory(
            {"title": "Global Energy Transition & Utility Markets"},
            allow_llm=False,
        )
        self.assertEqual(market_attempt, "market_intelligence")
        self.assertNotEqual(market_attempt, "project_nexus")

    def test_invariant_bypass_unknown_project_never_falls_back_to_nexus(self):
        """Unknown project strings or unrelated titles must never fall back to project_nexus."""
        unrelated_titles = [
            "Random Unrelated Kitchen Recipe",
            "Quarterly Accounting Audit",
            "Office Furniture Requisition",
        ]
        for title in unrelated_titles:
            with self.subTest(title=title):
                res = determine_project_directory({"title": title}, allow_llm=False)
                self.assertNotEqual(
                    res,
                    "project_nexus",
                    f"Unrelated title '{title}' must not fall back to project_nexus",
                )

    # Category 4: Exact production regression
    def test_exact_production_regression_operator_prompt(self):
        """Replay exact prompt context that triggered the incident."""
        prompt_input = "general energy and utilitieds trends"
        resolved = resolve_project_name(prompt_input)
        self.assertEqual(resolved, "market_intelligence")

        directory = determine_project_directory({"title": prompt_input}, allow_llm=False)
        self.assertEqual(directory, "market_intelligence")
        self.assertNotEqual(directory, "project_nexus")


if __name__ == "__main__":
    unittest.main()
