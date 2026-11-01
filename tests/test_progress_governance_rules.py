"""
Governance test suite for Progress Indicators & In-Band IDE Fenced Tracking.

Enforces:
1. AGENTS.md mandates in-band IDE fenced tracking.
2. .agents/rules/progress-indicators.md defines Section 5 In-Band IDE Fenced Tracking Mandate.
3. Prohibition of ephemeral progress markdown files on disk.
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ProgressGovernanceRulesTests(unittest.TestCase):
    def test_agents_md_mandates_in_band_fenced_tracking(self) -> None:
        agents_md = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("In-band IDE fenced tracking", agents_md)
        self.assertIn("ephemeral markdown files in the artifact registry", agents_md)

    def test_progress_indicators_rule_contains_mandate(self) -> None:
        rule_path = ROOT / ".agents" / "rules" / "progress-indicators.md"
        self.assertTrue(rule_path.exists())
        rule_content = rule_path.read_text(encoding="utf-8")
        self.assertIn("In-Band IDE Fenced Tracking Mandate", rule_content)
        self.assertIn("live_pipeline_progress.md", rule_content)
        self.assertIn("fails closed", rule_content)

    def test_no_codebase_writes_to_live_pipeline_progress(self) -> None:
        """Ensure no production tool or pipeline code calls write_text on live_pipeline_progress."""
        violating_files = []
        for py_path in (ROOT / "tools").glob("*.py"):
            text = py_path.read_text(encoding="utf-8")
            if "live_pipeline_progress.md" in text and ".write_text(" in text:
                violating_files.append(str(py_path))

        self.assertEqual(
            violating_files,
            [],
            f"Found tools writing live_pipeline_progress.md to disk: {violating_files}",
        )

    def test_progress_indicators_rule_contains_heartbeat_and_anti_sleep(self) -> None:
        rule_path = ROOT / ".agents" / "rules" / "progress-indicators.md"
        rule_content = rule_path.read_text(encoding="utf-8")
        self.assertIn("Stream Flushing & Non-TTY Adaptation", rule_content)
        self.assertIn("Active Model Heartbeats", rule_content)
        self.assertIn("Anti-Sleep Animation Prohibition", rule_content)

    def test_agents_md_contains_heartbeat_mandate(self) -> None:
        agents_md = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("Long operations (>15s) must emit periodic heartbeats", agents_md)
        self.assertIn("post-hoc synthetic sleep animations are prohibited", agents_md)


if __name__ == "__main__":
    unittest.main()
