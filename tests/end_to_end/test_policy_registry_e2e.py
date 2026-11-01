"""tests/end_to_end/test_policy_registry_e2e.py

End-to-end verification of the Governance Policy Registry (SSOT),
compact AGENTS.md index (<=12 KB), rule frontmatter triggers,
[tool.nee.budgets] in pyproject.toml, and PR-queue table layout golden contract.
"""

from __future__ import annotations

import io
import tomllib
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools.generate_agents_index import (
    load_policy_registry,
    scan_rule_metadata,
)
from tools.pr_governance_core.inspection import _render_queue_table


class TestPolicyRegistryE2E(unittest.TestCase):
    """E2E suite verifying governance SSOT integration and context invariants."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parent.parent.parent

    def test_01_policy_registry_yaml_structure_and_schema(self) -> None:
        """Verify governance/policy_registry.yaml has valid schema and complete invariants."""
        registry = load_policy_registry(self.repo_root)
        self.assertEqual(registry.get("version"), "1.1.0")
        policies = registry.get("policies", [])
        self.assertGreaterEqual(len(policies), 8, "Expected at least 8 canonical policies")

        policy_ids = set()
        required_fields = {
            "id",
            "title",
            "category",
            "threshold",
            "unit",
            "description",
            "enforcer",
            "rule_reference",
        }
        for pol in policies:
            pid = pol.get("id")
            self.assertIsNotNone(pid)
            self.assertNotIn(pid, policy_ids, f"Duplicate policy ID: {pid}")
            policy_ids.add(pid)

            for field in required_fields:
                self.assertIn(field, pol, f"Policy {pid} missing required field '{field}'")

            # Check referenced rule exists
            rule_ref = self.repo_root / pol["rule_reference"]
            self.assertTrue(
                rule_ref.exists(), f"Rule reference {rule_ref} does not exist for policy {pid}"
            )

    def test_02_agents_md_index_size_and_runtime_guard(self) -> None:
        """Verify AGENTS.md satisfies <=12KB size ceiling and exact runtime evidence guard."""
        agents_path = self.repo_root / "AGENTS.md"
        self.assertTrue(agents_path.exists(), "AGENTS.md must exist at repository root")

        content = agents_path.read_text(encoding="utf-8")
        byte_size = len(content.encode("utf-8"))
        max_bytes = 12288  # 12 KB

        self.assertLessEqual(
            byte_size,
            max_bytes,
            f"AGENTS.md size ({byte_size} bytes) exceeds 12 KB limit ({max_bytes} bytes)",
        )

        # Check that runtime evidence guard block is present
        self.assertIn(
            "<!-- NEE-RUNTIME-EVIDENCE-GUARD:BEGIN v1 -->",
            content,
            "Missing begin sentinel for runtime evidence guard",
        )
        self.assertIn(
            "<!-- NEE-RUNTIME-EVIDENCE-GUARD:END v1 -->",
            content,
            "Missing end sentinel for runtime evidence guard",
        )
        # Verify core phrases inside runtime evidence guard block
        self.assertIn(
            "Every `active_pair.json` is scoped to its explicit runtime root",
            content,
        )
        self.assertIn(
            "dual_pipeline.runtime_evidence inventory",
            content,
        )

    def test_03_all_33_rules_have_frontmatter_and_trigger(self) -> None:
        """Verify all rules in .agents/rules/ have valid YAML frontmatter and triggers."""
        rules = scan_rule_metadata(self.repo_root)
        self.assertEqual(
            len(rules), 36, f"Expected exactly 36 rules in .agents/rules/, found {len(rules)}"
        )

        valid_trigger_types = {"always_on", "glob", "model_decision"}
        for r in rules:
            self.assertIn(
                r["trigger"],
                valid_trigger_types,
                f"Rule {r['filename']} has invalid trigger '{r['trigger']}'",
            )

        # Explicitly verify finding X-09: system-learning-and-feedback.md must be demoted to glob
        learning_rule = next(
            (r for r in rules if r["filename"] == "system-learning-and-feedback.md"),
            None,
        )
        self.assertIsNotNone(learning_rule)
        self.assertEqual(
            learning_rule["trigger"],
            "glob",
            "system-learning-and-feedback.md must have trigger: glob (demoted from always_on)",
        )

    def test_04_pyproject_toml_nee_budgets_alignment(self) -> None:
        """Verify pyproject.toml [tool.nee.budgets] matches policy registry thresholds."""
        pyproject_path = self.repo_root / "pyproject.toml"
        self.assertTrue(pyproject_path.exists())

        with open(pyproject_path, "rb") as f:
            pyproject_data = tomllib.load(f)

        budgets = pyproject_data.get("tool", {}).get("nee", {}).get("budgets", {})
        self.assertIsNotNone(budgets, "Missing [tool.nee.budgets] table in pyproject.toml")

        self.assertEqual(budgets.get("max_file_lines"), 400)
        self.assertEqual(budgets.get("max_cyclomatic_complexity"), 15)
        self.assertEqual(budgets.get("hitl_ambiguity_margin_threshold"), 0.20)
        self.assertEqual(budgets.get("hitl_unresolved_entity_margin_threshold"), 0.15)
        self.assertEqual(budgets.get("hitl_divergence_margin_threshold"), 0.20)
        self.assertEqual(budgets.get("agents_md_max_bytes"), 12288)

    def test_05_pr_queue_table_golden_snapshot(self) -> None:
        """Verify PR queue table renderer emits the canonical layout."""
        sample_queue = [
            {
                "sub_rank": "P0.1",
                "readiness": "READY",
                "review_verdict": "MERGE_READY",
                "rebase_effort": "NONE",
                "domain": "Governance",
                "branch": "feat/sample-branch",
                "wt": None,
                "completed_waves": "W1-W2",
                "open_waves": "W3",
                "ahead": 2,
                "behind": 0,
                "next_action": "[KEEP] Proceed to W3",
                "ranking_rationale": "Top priority feature branch",
            }
        ]

        buf = io.StringIO()
        with redirect_stdout(buf):
            _render_queue_table(sample_queue)
        output = buf.getvalue()

        expected_header = (
            "| Priority | Branch | Category | Complexity | Code Completed? | Rebase Required? | PR to Main Required? | Graph Mutated (Y/N) | Recommended Disposition | Immediate Blocker / Action |"
        )
        self.assertIn(expected_header, output, "Table output missing canonical header")
        self.assertIn("|:---:|:----------------------------------:|:---|:---:|:---:|:---:|:---:|:---:|:---|:---|", output)
        self.assertIn("| **P0.1** |", output)
        self.assertIn("feat/sample-branch", output)


if __name__ == "__main__":
    unittest.main()
