"""tests/test_rules_budget_and_dedup_governance.py

Unit, integration, and CLI governance test suite for rules context budget,
>= 90% pairwise rule deduplication, and proactive capacity cleanup.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.audit_rules_budget import (
    DEFAULT_CAPACITY_CLEANUP_THRESHOLD_PCT,
    DEFAULT_DUPLICATION_THRESHOLD_PCT,
    DEFAULT_MAX_BUDGET_TOKENS,
    DEFAULT_MAX_FILE_BYTES,
    DuplicateRulePair,
    RulesBudgetReport,
    audit_rules_budget,
    build_audit_receipt,
    count_tokens,
    deduplicate_and_compact_rules,
    extract_rule_blocks,
    main,
    parse_rule_frontmatter,
    scan_rule_pairwise_duplicates,
)


class TestRulesBudgetAndDedupGovernance(unittest.TestCase):
    """Test suite for rules budget, pairwise deduplication, and capacity cleanup."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parent.parent

    def test_01_workspace_rules_budget_live_compliance(self) -> None:
        """Verify that the active repository rules satisfy all budget and dedup invariants."""
        report = audit_rules_budget(
            rules_dir=self.repo_root / ".agents" / "rules",
            agents_md=self.repo_root / "AGENTS.md",
        )
        self.assertTrue(report.is_overall_compliant, f"Violations: {report.errors}")
        self.assertTrue(report.is_budget_compliant)
        self.assertTrue(report.is_bytes_compliant)
        self.assertTrue(report.is_dedup_compliant)
        self.assertFalse(report.is_at_or_above_capacity_threshold)
        self.assertLess(report.budget_utilization_pct, 90.0)
        self.assertLessEqual(report.total_always_on_tokens, 18000)
        self.assertEqual(len(report.duplicate_pairs_above_90pct), 0)

    def test_02_count_tokens_accuracy(self) -> None:
        """Verify token counting handles empty, short, and long texts."""
        self.assertEqual(count_tokens(""), 0)
        self.assertEqual(count_tokens("   \n\t  "), 0)
        tokens = count_tokens("Every human speaker-assignment review must display target transcript turns.")
        self.assertGreater(tokens, 5)
        self.assertLess(tokens, 30)

    def test_03_parse_rule_frontmatter(self) -> None:
        """Verify YAML frontmatter extraction and fallbacks."""
        # Clean frontmatter
        raw = "---\ntrigger: model_decision\nscope: workspace\n---\n# Rule Title\nContent line"
        fm, body = parse_rule_frontmatter(raw)
        self.assertEqual(fm.get("trigger"), "model_decision")
        self.assertEqual(fm.get("scope"), "workspace")
        self.assertIn("# Rule Title", body)

        # No frontmatter
        no_fm = "# Title without frontmatter\nBody content"
        fm_empty, body_clean = parse_rule_frontmatter(no_fm)
        self.assertEqual(fm_empty, {})
        self.assertEqual(body_clean, no_fm)

        # Frontmatter with comments and quotes
        quoted = '---\ntrigger: "glob"\n# comment\nglobs: "*.py"\n---\nBody'
        fm_q, _ = parse_rule_frontmatter(quoted)
        self.assertEqual(fm_q.get("trigger"), "glob")
        self.assertEqual(fm_q.get("globs"), "*.py")

    def test_04_extract_rule_blocks(self) -> None:
        """Verify filtering out headers, tables, separators, link pointers, and short lines."""
        text = (
            "# Top Heading\n"
            "## Sub Heading\n"
            "Short line\n"
            "| Column 1 | Column 2 |\n"
            "| --- | --- |\n"
            "--- \n"
            "- Item: [link text](file:///path/to/file.md)\n"
            "- This is a substantive rule block that must strictly be retained because it contains comprehensive operational instructions.\n"
        )
        blocks = extract_rule_blocks(text, min_chars=70)
        self.assertEqual(len(blocks), 1)
        self.assertIn("substantive rule block", blocks[0])

    def test_05_scan_rule_pairwise_duplicates(self) -> None:
        """Verify detection of near-verbatim duplicate blocks and ignoring distinct text."""
        long_block_1 = "This is a mandatory governance requirement that enforces cryptographic receipts and verifiable state changes across storage."
        long_block_2 = "This is a mandatory governance requirement that enforces cryptographic receipts and verifiable state changes across storage!"
        distinct_block = "Completely distinct content discussing BGE-M3 hybrid retrieval parameters and Colbert index checkpoints."

        rules = {
            "rule_alpha.md": f"# Alpha\n\n{long_block_1}\n",
            "rule_beta.md": f"# Beta\n\n{long_block_2}\n",
            "rule_gamma.md": f"# Gamma\n\n{distinct_block}\n",
        }
        dups = scan_rule_pairwise_duplicates(rules, threshold_pct=90.0)
        self.assertEqual(len(dups), 1)
        self.assertIn(dups[0].file_a, ("rule_alpha.md", "rule_beta.md"))
        self.assertIn(dups[0].file_b, ("rule_alpha.md", "rule_beta.md"))
        self.assertGreaterEqual(dups[0].similarity_pct, 90.0)

    def test_06_deduplicate_and_compact_rules_lifecycle(self) -> None:
        """Verify that deduplicate_and_compact_rules replaces duplicates with SSOT pointers."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rules_dir = root / ".agents" / "rules"
            rules_dir.mkdir(parents=True)
            agents_md = root / "AGENTS.md"
            agents_md.write_text("# Agents Index\n", encoding="utf-8")

            block = "Every human speaker-assignment review must display target transcript turns per the formatting and context rules in the SSOT."
            rule_a = rules_dir / "rule_a.md"
            rule_a.write_text(f"---\ntrigger: always_on\n---\n# Rule A\n\n{block}\n", encoding="utf-8")
            rule_b = rules_dir / "rule_b.md"
            rule_b.write_text(f"---\ntrigger: always_on\n---\n# Rule B\n\n{block}\n", encoding="utf-8")

            res = deduplicate_and_compact_rules(
                rules_dir=rules_dir,
                agents_md=agents_md,
                global_agents_md=None,
            )
            self.assertTrue(res["triggered"])
            self.assertGreaterEqual(len(res["actions"]), 1)
            content_b = rule_b.read_text(encoding="utf-8")
            self.assertIn("See [rule_a.md]", content_b)
            self.assertNotIn(block, content_b)

            # Re-auditing confirms zero duplicates remain
            after = audit_rules_budget(rules_dir=rules_dir, agents_md=agents_md, global_agents_md=None)
            self.assertEqual(len(after.duplicate_pairs_above_90pct), 0)

    def test_07_build_audit_receipt(self) -> None:
        """Verify structured JSON execution receipt contains required fields and canonical digest."""
        report = audit_rules_budget(
            rules_dir=self.repo_root / ".agents" / "rules",
            agents_md=self.repo_root / "AGENTS.md",
        )
        receipt = build_audit_receipt(report, command="tools/audit_rules_budget.py --check")
        self.assertEqual(receipt["$schema"], "urn:nee:rules-budget-receipt:1.0.0")
        self.assertEqual(receipt["status"], "PASSED")
        self.assertIn("canonical_digest", receipt)
        self.assertIn("metrics", receipt)
        self.assertIn("budget_utilization_pct", receipt["metrics"])
        self.assertIn("duplicate_pairs_count", receipt["metrics"])

    def test_08_cli_check_flag(self) -> None:
        """Verify CLI entry point with --check and --json flags."""
        with tempfile.TemporaryDirectory() as td:
            receipt_path = Path(td) / "receipt.json"
            exit_code = main(["--check", "--receipt", str(receipt_path), "--json"])
            self.assertEqual(exit_code, 0)
            self.assertTrue(receipt_path.is_file())
            receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt_data["status"], "PASSED")


if __name__ == "__main__":
    unittest.main()
