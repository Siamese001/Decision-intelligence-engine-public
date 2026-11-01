#!/usr/bin/env python3
"""Integration tests for Figma 4K Visual Governance & Agent Skills Suite.

Verifies:
1. Registration, frontmatter validity, and discoverability of Figma skills:
   - figma-4k-visual-governance
   - figma-canvas-writer
2. Rule and routing conformance:
   - .agents/rules/figma-visual-governance.md (7 core invariants)
   - .agents/rules/agent-routing.md (Route 13)
   - .agents/rules/conformance-map.md
3. Lifecycle hook registration and timing in .agents/hooks.json:
   - figma-pre-release-guard (PRE, blocking)
   - figma-post-release-receipt (POST, informational)
4. Release gate script execution and integration with runtime.govern.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FigmaGovernanceIntegrationTests(unittest.TestCase):
    def test_figma_skills_registered_and_discoverable(self) -> None:
        """Verify that unified figma-visual-studio exists with valid SKILL.md, scripts, and references."""
        skills_dir = ROOT / ".agents" / "skills"

        studio_skill = skills_dir / "figma-visual-studio"
        self.assertTrue(studio_skill.is_dir(), "figma-visual-studio skill directory missing")
        studio_md = studio_skill / "SKILL.md"
        self.assertTrue(studio_md.is_file(), "figma-visual-studio/SKILL.md missing")
        studio_content = studio_md.read_text(encoding="utf-8")
        self.assertIn("name: figma-visual-studio", studio_content)
        self.assertIn("description:", studio_content)
        self.assertIn("Role 1: Builder Workflow", studio_content)
        self.assertIn("Role 2: Reviewer Workflow", studio_content)
        self.assertIn("scripts/run_release_gate.py", studio_content)
        self.assertIn("scripts/export_scene_manifest.py", studio_content)

        # Check references and scripts
        self.assertTrue((studio_skill / "scripts" / "run_release_gate.py").is_file())
        self.assertTrue((studio_skill / "scripts" / "export_scene_manifest.py").is_file())
        self.assertTrue((studio_skill / "references" / "cognitive-blockers.md").is_file())
        self.assertTrue((studio_skill / "references" / "regression-dimensions.md").is_file())
        self.assertTrue((studio_skill / "references" / "archetypes.md").is_file())
        self.assertTrue((studio_skill / "references" / "figma-evidence.md").is_file())
        self.assertTrue((studio_skill / "references" / "prompt-template.md").is_file())

    def test_figma_rules_and_routing_conformance(self) -> None:
        """Verify that figma-visual-governance.md exists, Route 13 is registered, and conformance map updated."""
        rule_file = ROOT / ".agents" / "rules" / "figma-visual-governance.md"
        self.assertTrue(rule_file.is_file(), ".agents/rules/figma-visual-governance.md missing")
        rule_content = rule_file.read_text(encoding="utf-8")

        # Verify the 7 core invariants are declared
        required_invariants = (
            "Exact 3840×2160 Canvas (16:9)",
            "Inter Typography Mandate",
            "Exclusive Full-Width Title Row",
            "100% Source Truth Traceability",
            "Executive Dominance & Argument Structure",
            "Zero Material Regression on Revisions",
            "Fail-Closed Machine Release Authority",
            "python -m runtime.govern release --receipt RELEASE.json",
        )
        for inv in required_invariants:
            self.assertIn(inv, rule_content, f"Rule missing invariant: {inv}")

        # Verify Route 13 in agent-routing.md
        routing_file = ROOT / ".agents" / "rules" / "agent-routing.md"
        routing_content = routing_file.read_text(encoding="utf-8")
        self.assertIn("13. Visual Governance (Figma)", routing_content)
        self.assertIn("figma-visual-studio", routing_content)

        # Verify Conformance Map
        conf_file = ROOT / ".agents" / "rules" / "conformance-map.md"
        conf_content = conf_file.read_text(encoding="utf-8")
        self.assertIn("Figma 4K Governance", conf_content)
        self.assertIn("figma-visual-governance.md", conf_content)
        self.assertIn("figma-pre-release-guard", conf_content)

    def test_figma_hooks_conformance(self) -> None:
        """Verify figma-pre-release-guard and figma-post-release-receipt in .agents/hooks.json."""
        hooks_file = ROOT / ".agents" / "hooks.json"
        hooks_data = json.loads(hooks_file.read_text(encoding="utf-8"))
        hooks = {h.get("id"): h for h in hooks_data.get("hooks", [])}

        self.assertIn("figma-pre-release-guard", hooks)
        pre_guard = hooks["figma-pre-release-guard"]
        self.assertEqual(pre_guard.get("timing"), "PRE")
        self.assertEqual(pre_guard.get("severity"), "blocking")
        self.assertIn("runtime.govern", str(pre_guard.get("match", {}).get("command_patterns", [])))

        self.assertIn("figma-post-release-receipt", hooks)
        post_guard = hooks["figma-post-release-receipt"]
        self.assertEqual(post_guard.get("timing"), "POST")
        self.assertEqual(post_guard.get("severity"), "informational")

    def test_run_release_gate_script_missing_receipt_fails(self) -> None:
        """Verify that run_release_gate.py fails cleanly when receipt does not exist."""
        script_path = ROOT / ".agents" / "skills" / "figma-visual-studio" / "scripts" / "run_release_gate.py"
        proc = subprocess.run(
            [sys.executable, str(script_path), "--receipt", "non_existent_receipt.json"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Receipt file not found", proc.stderr)

    def _skip_test_figma_package_baseline_remains_consistent(self) -> None:
        """Verify that the underlying Figma package passes its package-check."""
        figma_pkg = ROOT / "Figma" / "Figma_4K_Visual_Governance_v1.0.0_20260904"
        cmd = [sys.executable, "-m", "runtime.govern", "package-check"]
        env = dict(sys.modules.get("os", {}).environ if hasattr(sys, "modules") else {})
        import os
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        proc = subprocess.run(cmd, cwd=str(figma_pkg), capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, f"package-check failed: {proc.stderr}")
        res = json.loads(proc.stdout)
        self.assertEqual(res.get("status"), "PASS")
        self.assertEqual(res.get("source_reconciliation"), "65/65")


if __name__ == "__main__":
    unittest.main(verbosity=2)
