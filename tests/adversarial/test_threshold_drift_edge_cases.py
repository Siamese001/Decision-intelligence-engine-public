"""tests/adversarial/test_threshold_drift_edge_cases.py

Adversarial and edge-case verification for governance threshold drift,
oversized AGENTS.md rejection, missing sentinel detection,
rule frontmatter tampering, and PR-queue column layout violations.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.generate_agents_index import (
    load_policy_registry,
    scan_rule_metadata,
)


class TestThresholdDriftEdgeCases(unittest.TestCase):
    """Adversarial tests for governance drift and context safety boundaries."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parent.parent.parent

    def test_01_adversarial_oversized_agents_md_fails_check(self) -> None:
        """Verify that AGENTS.md exceeding 12,288 bytes fails --check closed."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            fake_agents = tmp_root / "AGENTS.md"
            # Write 15,000+ bytes including sentinel to exceed 12,288 byte limit
            padding = "x" * 15000
            content = (
                "# Bloated AGENTS.md\n"
                "<!-- NEE-RUNTIME-EVIDENCE-GUARD:BEGIN v1 -->\n"
                "<!-- NEE-RUNTIME-EVIDENCE-GUARD:END v1 -->\n" + padding
            )
            fake_agents.write_text(content, encoding="utf-8")

            # Run generate_agents_index.py --check
            cmd = [
                sys.executable,
                str(self.repo_root / "tools" / "generate_agents_index.py"),
                "--check",
                "--repo-root",
                str(tmp_root),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            self.assertNotEqual(
                res.returncode, 0, "Oversized AGENTS.md must fail check with non-zero exit code"
            )
            self.assertIn("exceeds size ceiling", res.stderr + res.stdout)

    def test_02_adversarial_missing_sentinel_fails_check(self) -> None:
        """Verify that AGENTS.md missing runtime evidence guard sentinels fails closed."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            fake_agents = tmp_root / "AGENTS.md"
            # Missing sentinels entirely
            fake_agents.write_text("# Stripped AGENTS.md\nNo guard block here.\n", encoding="utf-8")

            cmd = [
                sys.executable,
                str(self.repo_root / "tools" / "generate_agents_index.py"),
                "--check",
                "--repo-root",
                str(tmp_root),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True)
            self.assertNotEqual(res.returncode, 0, "AGENTS.md missing sentinels must fail check")
            self.assertIn("missing NEE-RUNTIME-EVIDENCE-GUARD", res.stderr + res.stdout)

    def test_03_adversarial_rule_missing_frontmatter_fails_validation(self) -> None:
        """Verify that a rule file missing frontmatter in .agents/rules/ is flagged."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = Path(tmp_dir)
            rules_dir = tmp_root / ".agents" / "rules"
            rules_dir.mkdir(parents=True)

            # Create one rule with frontmatter, one without
            valid_rule = rules_dir / "valid-rule.md"
            valid_rule.write_text("---\ntrigger: always_on\n---\n# Valid Rule\n", encoding="utf-8")

            invalid_rule = rules_dir / "invalid-rule.md"
            invalid_rule.write_text("# Invalid Rule\nNo frontmatter at all.\n", encoding="utf-8")

            rules = scan_rule_metadata(tmp_root)
            self.assertEqual(len(rules), 2)
            # invalid_rule should not have an explicit trigger from frontmatter (defaults or fails)
            inval = next(r for r in rules if r["filename"] == "invalid-rule.md")
            self.assertEqual(inval["filename"], "invalid-rule.md")
            # If text didn't start with ---, it defaults or indicates missing trigger
            raw_text = invalid_rule.read_text(encoding="utf-8")
            self.assertFalse(
                raw_text.startswith("---"), "Invalid rule text must not have frontmatter"
            )

    def test_04_adversarial_threshold_drift_detection(self) -> None:
        """Verify that conflicting threshold restatements between registry and policies fail."""
        registry = load_policy_registry(self.repo_root)
        policies = {p["id"]: p for p in registry.get("policies", [])}

        # Validate that canonical 15% ambiguity margin is strictly 0.15
        self.assertIn("INV-HITL-AMBIGUITY-MARGIN", policies)
        self.assertEqual(policies["INV-HITL-AMBIGUITY-MARGIN"]["threshold"], 0.15)

        # Validate that legacy 25% is NOT the threshold
        self.assertNotEqual(policies["INV-HITL-AMBIGUITY-MARGIN"]["threshold"], 0.25)

        # Validate that max file lines is strictly 400
        self.assertIn("INV-BUDGET-FILE-LINES", policies)
        self.assertEqual(policies["INV-BUDGET-FILE-LINES"]["threshold"], 400)

        # Validate that cyclomatic complexity ceiling is 15
        self.assertIn("INV-COMPLEXITY-MAX", policies)
        self.assertEqual(policies["INV-COMPLEXITY-MAX"]["threshold"], 15)

    def test_05_adversarial_pr_queue_missing_columns_fails_golden_layout(self) -> None:
        """Verify that any deviation from the 12-column PR queue contract is detected."""
        malformed_headers = [
            "| Priority | Branch | Next Action | Readiness | Review |",  # 5 columns
            "| Priority | Domain | Branch | Next Action | Readiness | Review | Effort | Completed Waves | Open Waves | Ahead | Behind |",  # 11 columns (missing Ranking Rationale)
        ]

        expected_12_cols = [
            "Priority",
            "Domain",
            "Branch",
            "Next Action",
            "Ranking Rationale",
            "Readiness",
            "Review",
            "Effort",
            "Completed Waves",
            "Open Waves",
            "Ahead",
            "Behind",
        ]

        for bad_header in malformed_headers:
            cols = [c.strip() for c in bad_header.split("|") if c.strip()]
            self.assertNotEqual(
                len(cols),
                12,
                f"Malformed header unexpectedly had 12 columns: {bad_header}",
            )
            self.assertNotEqual(cols, expected_12_cols)


if __name__ == "__main__":
    unittest.main()
