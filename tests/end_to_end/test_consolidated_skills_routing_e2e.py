#!/usr/bin/env python3
"""End-to-end integration test for consolidated skills routing and registry integrity.

Verifies:
1. Central registry (.agents/skills.json) counts and disk parity (19 canonical + 2 plugins = 21).
2. Routing table (.agents/rules/agent-routing.md) binding to consolidated skills:
   - Route 8 -> nee-release
   - Route 13 -> figma-visual-studio
   - Route 20 -> figma-visual-studio
   - Route 21 -> skill-architectural-governance
3. Absolute absence of retired micro-skills from filesystem and routing dispatch.
4. CLI execution of consolidated skill scripts and audit tools.
5. Adversarial input and edge-case handling for skill dispatch.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

RETIRED_MICRO_SKILLS = frozenset([
    "figma-4k-visual-governance",
    "figma-canvas-writer",
    "nee-retrieval-eval",
    "skill-architectural-integrity-auditor",
    "skill-concurrency-stress-harness",
    "skill-provider-fault-injector",
    "skill-runtime-evidence-and-hygiene-auditor",
])


class TestConsolidatedSkillsRoutingE2E(unittest.TestCase):
    """E2E suite for consolidated skills routing, registry, and CLI execution."""

    def setUp(self) -> None:
        self.skills_json_path = REPO_ROOT / ".agents" / "skills.json"
        self.routing_path = REPO_ROOT / ".agents" / "rules" / "agent-routing.md"
        self.skills_dir = REPO_ROOT / ".agents" / "skills"

    def test_skills_registry_counts_and_disk_parity(self) -> None:
        """Verify skills.json declares exactly 19 canonical, 2 plugins, 21 total skills matching disk."""
        self.assertTrue(self.skills_json_path.is_file(), ".agents/skills.json missing")
        data = json.loads(self.skills_json_path.read_text(encoding="utf-8"))

        self.assertEqual(data.get("canonical_skills_count"), 19)
        self.assertEqual(data.get("plugin_skills_count"), 2)
        self.assertEqual(data.get("total_skills_count"), 21)

        core_skills = data.get("skills", [])
        plugin_skills = data.get("plugin_skills", [])
        self.assertEqual(len(core_skills), 19)
        self.assertEqual(len(plugin_skills), 2)

        # Verify all core skills exist on disk with valid SKILL.md
        for skill in core_skills:
            skill_path = REPO_ROOT / skill["path"]
            self.assertTrue(skill_path.is_dir(), f"Skill directory missing: {skill_path}")
            skill_md = skill_path / "SKILL.md"
            self.assertTrue(skill_md.is_file(), f"SKILL.md missing: {skill_md}")

        # Verify plugin skills exist on disk
        for pskill in plugin_skills:
            pskill_path = REPO_ROOT / pskill["path"]
            self.assertTrue(pskill_path.is_dir(), f"Plugin skill directory missing: {pskill_path}")
            self.assertTrue((pskill_path / "SKILL.md").is_file(), f"Plugin SKILL.md missing: {pskill_path}")

    def test_retired_micro_skills_absent_from_disk_and_registry(self) -> None:
        """Ensure no retired micro-skills linger in .agents/skills/ or .agents/skills.json."""
        data = json.loads(self.skills_json_path.read_text(encoding="utf-8"))
        registered_core_names = {s["name"] for s in data.get("skills", [])}

        for retired in RETIRED_MICRO_SKILLS:
            # Must not be an active directory in .agents/skills/
            skill_dir = self.skills_dir / retired
            self.assertFalse(skill_dir.exists(), f"Retired skill directory still exists: {skill_dir}")
            # Must not be listed in active skills.json core skills
            self.assertNotIn(retired, registered_core_names, f"Retired skill still in skills.json: {retired}")

    def test_routing_table_aligns_with_consolidated_skills(self) -> None:
        """Verify agent-routing.md routes dispatch exclusively to valid consolidated skills."""
        self.assertTrue(self.routing_path.is_file(), ".agents/rules/agent-routing.md missing")
        content = self.routing_path.read_text(encoding="utf-8")

        # Check specific consolidated routes
        self.assertIn("| **8. Retrieval Eval** |", content)
        self.assertIn("`nee-release`", content)
        self.assertIn("| **13. Visual Governance (Figma)** |", content)
        self.assertIn("`figma-visual-studio`", content)
        self.assertIn("| **20. Figma Canvas Authoring** |", content)
        self.assertIn("| **21. Architectural & Concurrency Governance** |", content)
        self.assertIn("`skill-architectural-governance`", content)

        # Parse all skill backtick mentions in the table
        table_match = re.search(r"## Route Classification Table\s*\n(.*?)(?=\n## |\Z)", content, re.DOTALL)
        self.assertIsNotNone(table_match, "Route Classification Table section missing")
        table_text = table_match.group(1)

        # Extract skills column tokens: `skill-name`
        skill_mentions = set(re.findall(r"`([a-zA-Z0-9_\-]+)`", table_text))
        # Filter down to skill-like names (exclude flags or python commands)
        skill_mentions = {s for s in skill_mentions if not s.startswith("-") and not s.endswith(".py") and s not in {"p", "m", "a"}}

        for retired in RETIRED_MICRO_SKILLS:
            self.assertNotIn(retired, skill_mentions, f"Retired skill {retired} referenced in routing table")

    def test_consolidated_skill_cli_execution_e2e(self) -> None:
        """Execute CLI commands for consolidated skills and verify clean exit."""
        # Test 1: figma-visual-studio release gate CLI help
        gate_script = self.skills_dir / "figma-visual-studio" / "scripts" / "run_release_gate.py"
        proc1 = subprocess.run([sys.executable, str(gate_script), "--help"], capture_output=True, text=True)
        self.assertEqual(proc1.returncode, 0, f"run_release_gate.py --help failed: {proc1.stderr}")

        # Test 2: figma-visual-studio export manifest CLI help
        manifest_script = self.skills_dir / "figma-visual-studio" / "scripts" / "export_scene_manifest.py"
        proc2 = subprocess.run([sys.executable, str(manifest_script), "--help"], capture_output=True, text=True)
        self.assertEqual(proc2.returncode, 0, f"export_scene_manifest.py --help failed: {proc2.stderr}")

        # Test 3: audit_skills_budget CLI
        budget_tool = REPO_ROOT / "tools" / "audit_skills_budget.py"
        proc3 = subprocess.run([sys.executable, str(budget_tool)], capture_output=True, text=True)
        self.assertEqual(proc3.returncode, 0, f"audit_skills_budget.py failed: {proc3.stderr}")
        self.assertIn("PASSED", proc3.stdout)
        self.assertIn("%", proc3.stdout)

    def test_adversarial_and_edge_case_dispatch(self) -> None:
        """Adversarial and edge-case testing for consolidated tool invocation."""
        gate_script = self.skills_dir / "figma-visual-studio" / "scripts" / "run_release_gate.py"

        # Edge case 1: Missing required arguments
        proc_empty = subprocess.run([sys.executable, str(gate_script)], capture_output=True, text=True)
        self.assertNotEqual(proc_empty.returncode, 0)
        self.assertIn("error: the following arguments are required: --receipt", proc_empty.stderr)

        # Edge case 2: Non-existent receipt file
        proc_missing = subprocess.run(
            [sys.executable, str(gate_script), "--receipt", "non_existent_file.json"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(proc_missing.returncode, 0)
        self.assertIn("Receipt file not found", proc_missing.stderr)

        # Edge case 3: export_scene_manifest missing required flags
        manifest_script = self.skills_dir / "figma-visual-studio" / "scripts" / "export_scene_manifest.py"
        proc_manifest_empty = subprocess.run([sys.executable, str(manifest_script)], capture_output=True, text=True)
        self.assertNotEqual(proc_manifest_empty.returncode, 0)
        self.assertIn("the following arguments are required", proc_manifest_empty.stderr)


if __name__ == "__main__":
    unittest.main()
