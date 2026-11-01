#!/usr/bin/env python3
"""
Unit and Invariant Tests for Multi-Dimensional Modularity Index (MMI) & File Budget Governance.

Verifies:
1. Valid production-cohesive classifications (cohesive_structural_module, pure_declarative_table) pass.
2. Stale or falsified attestations (CC, imports) are detected and fail closed.
3. Class ceiling line limits (e.g. 900 lines) are enforced.
4. Non-blocking Amber Zone (401-600 lines) allows low-complexity modules with warning.
5. High-complexity modules in amber zone are rejected as budget violations.
6. Pure declarative tables with branch logic are rejected.
7. MMI snapshot generator emits valid RFC-8259 telemetry.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.file_budget_core import (
    generate_mmi_snapshot,
)
from tools.lint_file_budgets import (
    PERMITTED_PRODUCTION_COHESIVE_CLASSIFICATIONS,
    _eval_module_budget,
    validate_exclusion_entry,
)


class TestMMIFileBudgetGovernance(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_valid_cohesive_structural_module_attestation(self) -> None:
        """Valid cohesive module with accurate AST attestations passes validation."""
        code = (
            "class DataStructure:\n"
            "    def __init__(self):\n"
            "        self.data = {}\n"
            "    def get_value(self, key):\n"
            "        if key in self.data:\n"
            "            return self.data[key]\n"
            "        return None\n"
        )
        mod_file = self.root / "dual_pipeline" / "my_cohesive_module.py"
        mod_file.parent.mkdir(parents=True, exist_ok=True)
        mod_file.write_text(code, encoding="utf-8")

        entry = {
            "path": "dual_pipeline/my_cohesive_module.py",
            "classification": "cohesive_structural_module",
            "justification": "Cohesive internal data structure managing graph node state.",
            "attestations": {
                "cognitive_complexity_ceiling_met": True,
                "fan_out_ceiling_met": True,
                "single_responsibility_rationale": "Encapsulates graph node lookup cache and lifecycle.",
                "max_function_cc": 2,
                "unique_external_imports": 0,
            },
            "expires_at": "2026-12-31",
        }
        valid, reason = validate_exclusion_entry(
            entry,
            repo_root=self.root,
            cohesive_specs=PERMITTED_PRODUCTION_COHESIVE_CLASSIFICATIONS,
        )
        self.assertTrue(valid, f"Expected valid attestation, got: {reason}")
        self.assertEqual(reason, "Valid")

    def test_stale_cognitive_complexity_attestation_rejected(self) -> None:
        """Attestation claiming max CC of 2 when actual CC is 20 must be rejected."""
        # Function with nesting producing high CC
        complex_func = (
            "def deeply_nested(a, b, c, d):\n"
            "    if a:\n"
            "        if b:\n"
            "            if c:\n"
            "                for x in d:\n"
            "                    while x > 0:\n"
            "                        if x % 2 == 0:\n"
            "                            return x\n"
            "    return None\n"
        )
        mod_file = self.root / "dual_pipeline" / "complex_module.py"
        mod_file.parent.mkdir(parents=True, exist_ok=True)
        mod_file.write_text(complex_func, encoding="utf-8")

        entry = {
            "path": "dual_pipeline/complex_module.py",
            "classification": "cohesive_structural_module",
            "justification": "High complexity module with falsely understated CC attestation.",
            "attestations": {
                "cognitive_complexity_ceiling_met": True,
                "fan_out_ceiling_met": True,
                "single_responsibility_rationale": "Complex state graph evaluator.",
                "max_function_cc": 2,  # Stale / understated
                "unique_external_imports": 0,
            },
            "expires_at": "2026-12-31",
        }
        valid, reason = validate_exclusion_entry(
            entry,
            repo_root=self.root,
            cohesive_specs=PERMITTED_PRODUCTION_COHESIVE_CLASSIFICATIONS,
        )
        self.assertFalse(valid, "Stale CC attestation must be rejected")
        self.assertIn("Stale attestation: actual max cognitive complexity", reason)

    def test_stale_external_imports_attestation_rejected(self) -> None:
        """Attestation claiming 0 external imports when module imports external packages must fail."""
        code = (
            "import storage.connection\n"
            "import dual_pipeline.contracts\n"
            "import config.transport\n"
            "def noop(): pass\n"
        )
        mod_file = self.root / "dual_pipeline" / "import_heavy.py"
        mod_file.parent.mkdir(parents=True, exist_ok=True)
        mod_file.write_text(code, encoding="utf-8")

        entry = {
            "path": "dual_pipeline/import_heavy.py",
            "classification": "cohesive_structural_module",
            "justification": "Module importing multiple external packages but attesting 0.",
            "attestations": {
                "cognitive_complexity_ceiling_met": True,
                "fan_out_ceiling_met": True,
                "single_responsibility_rationale": "Multi-package integration adapter.",
                "max_function_cc": 0,
                "unique_external_imports": 0,  # Stale / understated (actual: 3)
            },
            "expires_at": "2026-12-31",
        }
        valid, reason = validate_exclusion_entry(
            entry,
            repo_root=self.root,
            cohesive_specs=PERMITTED_PRODUCTION_COHESIVE_CLASSIFICATIONS,
        )
        self.assertFalse(valid, "Stale external import count must be rejected")
        self.assertIn("Stale attestation: actual external imports", reason)

    def test_exceeding_classification_ceiling_lines_rejected(self) -> None:
        """Module exceeding 900 lines cannot qualify for cohesive_structural_module."""
        lines = ["# padding comment line\n"] * 950
        mod_file = self.root / "dual_pipeline" / "oversized_cohesive.py"
        mod_file.parent.mkdir(parents=True, exist_ok=True)
        mod_file.write_text("".join(lines), encoding="utf-8")

        entry = {
            "path": "dual_pipeline/oversized_cohesive.py",
            "classification": "cohesive_structural_module",
            "justification": "Oversized module attempting to claim cohesive exemption beyond 900 lines.",
            "attestations": {
                "cognitive_complexity_ceiling_met": True,
                "fan_out_ceiling_met": True,
                "single_responsibility_rationale": "Excessively large monolithic module.",
                "max_function_cc": 0,
                "unique_external_imports": 0,
            },
            "expires_at": "2026-12-31",
        }
        valid, reason = validate_exclusion_entry(
            entry,
            repo_root=self.root,
            cohesive_specs=PERMITTED_PRODUCTION_COHESIVE_CLASSIFICATIONS,
        )
        self.assertFalse(valid, "Lines exceeding class ceiling must be rejected")
        self.assertIn("exceeds class ceiling of 900 lines", reason)

    def test_missing_mandatory_attestation_key_rejected(self) -> None:
        """Exemption missing a mandatory key from required_attestations is rejected."""
        mod_file = self.root / "dual_pipeline" / "simple.py"
        mod_file.parent.mkdir(parents=True, exist_ok=True)
        mod_file.write_text("def foo(): pass\n", encoding="utf-8")

        entry = {
            "path": "dual_pipeline/simple.py",
            "classification": "cohesive_structural_module",
            "justification": "Missing single responsibility rationale in attestations.",
            "attestations": {
                "cognitive_complexity_ceiling_met": True,
                "fan_out_ceiling_met": True,
                # Missing 'single_responsibility_rationale'
            },
            "expires_at": "2026-12-31",
        }
        valid, reason = validate_exclusion_entry(
            entry,
            repo_root=self.root,
            cohesive_specs=PERMITTED_PRODUCTION_COHESIVE_CLASSIFICATIONS,
        )
        self.assertFalse(valid, "Missing required attestation key must be rejected")
        self.assertIn("Missing mandatory attestation key: 'single_responsibility_rationale'", reason)

    def test_branch_logic_in_pure_declarative_table_rejected(self) -> None:
        """pure_declarative_table claiming zero branch logic must fail if AST contains branch logic."""
        code = (
            "TABLE = {'a': 1, 'b': 2}\n"
            "def lookup(k):\n"
            "    if k in TABLE:\n"
            "        return TABLE[k]\n"
            "    return None\n"
        )
        mod_file = self.root / "dual_pipeline" / "lookup_with_branch.py"
        mod_file.parent.mkdir(parents=True, exist_ok=True)
        mod_file.write_text(code, encoding="utf-8")

        entry = {
            "path": "dual_pipeline/lookup_with_branch.py",
            "classification": "pure_declarative_table",
            "justification": "Static lookup table claiming zero branch logic.",
            "attestations": {
                "zero_branch_logic": True,
                "cognitive_complexity_ceiling_met": True,
                "max_function_cc": 1,
            },
            "expires_at": "2026-12-31",
        }
        valid, reason = validate_exclusion_entry(
            entry,
            repo_root=self.root,
            cohesive_specs=PERMITTED_PRODUCTION_COHESIVE_CLASSIFICATIONS,
        )
        self.assertFalse(valid, "Branch logic in pure declarative table must be rejected")
        self.assertIn("branch logic (If/For/While/Match/Except) was detected", reason)

    def test_amber_zone_low_complexity_emits_warning_not_failure(self) -> None:
        """Module in 401-600 lines with CC <= 15 and imports <= 12 emits amber warning and passes."""
        # 450 lines of simple declarative assignments and simple functions
        lines = ["# Simple code\n"] * 440 + [
            "def simple_func():\n",
            "    return 42\n",
        ]
        mod_file = self.root / "dual_pipeline" / "amber_module.py"
        mod_file.parent.mkdir(parents=True, exist_ok=True)
        mod_file.write_text("".join(lines), encoding="utf-8")

        reason, is_grad, amber_info = _eval_module_budget(
            rel="dual_pipeline/amber_module.py",
            fp=mod_file,
            lines=442,
            max_lines=400,
            ratchet=False,
            baseline_modules={},
            tiered=False,
        )
        self.assertIsNone(reason, "Amber module should not produce a failure reason")
        self.assertFalse(is_grad)
        self.assertIsNotNone(amber_info, "Amber module should produce amber warning info")
        self.assertTrue("amber zone" in amber_info or "standard warning allowance" in amber_info)

    def test_amber_zone_high_complexity_produces_violation(self) -> None:
        """Module in 401-600 lines with CC > 15 must produce a budget violation."""
        code = (
            ["# Padding\n"] * 430
            + [
                "def nested_logic(a, b, c, d, e):\n",
                "    if a:\n",
                "        if b:\n",
                "            if c:\n",
                "                for x in d:\n",
                "                    while x > 0:\n",
                "                        if x % 2 == 0:\n",
                "                            return x\n",
                "    return None\n",
            ]
        )
        mod_file = self.root / "dual_pipeline" / "amber_high_cc.py"
        mod_file.parent.mkdir(parents=True, exist_ok=True)
        mod_file.write_text("".join(code), encoding="utf-8")

        reason, is_grad, amber_info = _eval_module_budget(
            rel="dual_pipeline/amber_high_cc.py",
            fp=mod_file,
            lines=440,
            max_lines=400,
            ratchet=False,
            baseline_modules={},
            tiered=False,
        )
        self.assertIsNotNone(reason, "Amber module with high CC must produce a violation")
        self.assertTrue("exceeds 400 lines" in reason or "Cognitive complexity violation" in reason)
        self.assertIsNone(amber_info)

    def test_mmi_snapshot_generation(self) -> None:
        """generate_mmi_snapshot produces valid RFC-8259 JSON report with telemetry."""
        mod1 = self.root / "dual_pipeline" / "mod1.py"
        mod1.parent.mkdir(parents=True, exist_ok=True)
        mod1.write_text("class A: pass\ndef b(): pass\n", encoding="utf-8")

        out_path = self.root / "reports" / "test_mmi_snapshot.json"
        exit_code = generate_mmi_snapshot(
            repo_root=self.root,
            target_files=[mod1],
            output_path=out_path,
            baseline_modules={},
            cohesive_exemptions={},
            get_tiered_max_lines_fn=lambda rel, default: default,
            default_max_lines=400,
            amber_zone_margin=200,
        )
        self.assertEqual(exit_code, 0)
        self.assertTrue(out_path.exists())

        data = json.loads(out_path.read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], "urn:nee:mmi:snapshot:1.0.0")
        self.assertEqual(data["scanned_modules"], 1)
        self.assertEqual(data["summary"]["WITHIN_STANDARD_BUDGET"], 1)
        self.assertEqual(data["modules"][0]["responsibility_count"], 2)


if __name__ == "__main__":
    unittest.main()
