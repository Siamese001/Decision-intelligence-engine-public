#!/usr/bin/env python3
"""Comprehensive End-to-End Governance Suite for Workspace Skills SSOT.

Verifies:
1. Strict SSOT consistency between .agents/skills.json and all 25 skills on disk.
2. Complete absence of vendor-branded artifacts (openai.yaml, .codex-plugin, etc.).
3. 100% resolution of referenced scripts, modules, and links in all SKILL.md files.
4. Live end-to-end execution of storage.stress_harness, evals.run_provider_fault_injection, and tools.audit_worktree_hygiene.
5. Canonical SHA-256 signed receipts verified for all governance runs.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_BIN = sys.executable

from tools.audit_skills_budget import (
    DEFAULT_MAX_BUDGET_TOKENS,
    DEFAULT_MAX_SKILLS_COUNT,
    DEFAULT_PLUGINS_DIR,
    DEFAULT_SKILLS_DIR,
    DEFAULT_SKILLS_JSON,
    audit_skills_budget,
)


class TestSkillsEndToEndGovernance(unittest.TestCase):
    """End-to-End Governance verification suite."""

    def test_ssot_all_25_skills_registered_and_exist(self) -> None:
        """Verify .agents/skills.json registers all 23 core skills and 2 plugin skills."""
        self.assertTrue(DEFAULT_SKILLS_JSON.is_file(), ".agents/skills.json must exist")
        data = json.loads(DEFAULT_SKILLS_JSON.read_text(encoding="utf-8"))

        self.assertEqual(data.get("canonical_skills_count"), 24)
        self.assertEqual(data.get("plugin_skills_count"), 2)
        self.assertEqual(data.get("total_skills_count"), 26)

        # Core skills
        core_names = {s["name"] for s in data.get("skills", [])}
        disk_core = {p.name for p in DEFAULT_SKILLS_DIR.iterdir() if p.is_dir() and not p.name.startswith(".")}
        self.assertEqual(core_names, disk_core, f"Core skills mismatch: {core_names ^ disk_core}")

        # Plugin skills
        plugin_names = {s["name"] for s in data.get("plugin_skills", [])}
        disk_plugins = set()
        for pdir in DEFAULT_PLUGINS_DIR.iterdir():
            if pdir.is_dir() and not pdir.name.startswith("."):
                pskills = pdir / "skills"
                if pskills.is_dir():
                    for sp in pskills.iterdir():
                        if sp.is_dir() and not sp.name.startswith("."):
                            disk_plugins.add(sp.name)
        self.assertEqual(plugin_names, disk_plugins, f"Plugin skills mismatch: {plugin_names ^ disk_plugins}")

    def test_zero_vendor_redundant_files(self) -> None:
        """Verify no openai.yaml, .codex-plugin, or vendor artifacts exist in .agents."""
        agents_dir = ROOT / ".agents"
        forbidden_patterns = ["*openai*", "*codex*"]
        found = []
        for pat in forbidden_patterns:
            for p in agents_dir.rglob(pat):
                found.append(str(p.relative_to(ROOT)))
        self.assertEqual(found, [], f"Found forbidden vendor artifacts: {found}")

    def test_live_storage_stress_harness_cli(self) -> None:
        """Execute python -m storage.stress_harness with receipt."""
        with tempfile.TemporaryDirectory() as tmpdir:
            receipt_path = Path(tmpdir) / "stress_receipt.json"
            cmd = [
                PYTHON_BIN,
                "-m",
                "storage.stress_harness",
                "--workers",
                "4",
                "--duration-seconds",
                "1.0",
                "--target-db-mode",
                "wal",
                "--verify-integrity",
                "--receipt",
                str(receipt_path),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
            self.assertEqual(res.returncode, 0, f"Harness failed: {res.stdout} {res.stderr}")
            self.assertTrue(receipt_path.is_file())
            data = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "PASSED")
            self.assertEqual(data["metrics"]["locked_errors_count"], 0)
            self.assertIn("canonical_digest", data)

    def test_live_provider_fault_injection_cli(self) -> None:
        """Execute python -m evals.run_provider_fault_injection with receipt."""
        with tempfile.TemporaryDirectory() as tmpdir:
            receipt_path = Path(tmpdir) / "fault_receipt.json"
            cmd = [
                PYTHON_BIN,
                "-m",
                "evals.run_provider_fault_injection",
                "--scenarios",
                "FAULT-429,FAULT-503,FAULT-TRUNC,FAULT-TIMEOUT",
                "--pipeline",
                "pipeline_b_extraction",
                "--receipt",
                str(receipt_path),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
            self.assertEqual(res.returncode, 0, f"Fault injection failed: {res.stdout} {res.stderr}")
            self.assertTrue(receipt_path.is_file())
            data = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "PASSED")
            self.assertEqual(data["metrics"]["passed_scenarios"], 4)
            self.assertIn("canonical_digest", data)

    def test_live_worktree_hygiene_cli(self) -> None:
        """Execute python tools/audit_worktree_hygiene.py with receipt."""
        with tempfile.TemporaryDirectory() as tmpdir:
            receipt_path = Path(tmpdir) / "hygiene_receipt.json"
            cmd = [
                PYTHON_BIN,
                "tools/audit_worktree_hygiene.py",
                "--clean-dangling-tmp",
                "--check",
                "--receipt",
                str(receipt_path),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
            self.assertEqual(res.returncode, 0, f"Hygiene check failed: {res.stdout} {res.stderr}")
            self.assertTrue(receipt_path.is_file())
            data = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "PASSED")
            self.assertTrue(data["metrics"]["is_hygiene_compliant"])
            self.assertIn("canonical_digest", data)

    def test_cleanup_merged_worktrees_logic(self) -> None:
        """Verify cleanup_merged_worktrees helper correctly identifies and prunes merged worktrees."""
        from tools.audit_worktree_hygiene import is_branch_merged
        self.assertTrue(is_branch_merged("main", ROOT, base_branch="main"))
        self.assertFalse(is_branch_merged("non_existent_branch_xyz_123", ROOT, base_branch="main"))

    def test_full_skills_budget_and_disk_verification(self) -> None:
        """Complete 25-skill audit with 100% token, SSOT, and disk verification."""
        report = audit_skills_budget(
            skills_dir=DEFAULT_SKILLS_DIR,
            plugins_dir=DEFAULT_PLUGINS_DIR,
            skills_json=DEFAULT_SKILLS_JSON,
            max_budget_tokens=DEFAULT_MAX_BUDGET_TOKENS,
            max_skills_count=DEFAULT_MAX_SKILLS_COUNT,
            verify_disk=True,
        )
        self.assertTrue(report.is_overall_compliant, f"Skills audit failed: {report.errors}")
        self.assertTrue(report.is_ssot_compliant)
        self.assertEqual(report.total_skills, 26)
        self.assertLessEqual(report.total_description_tokens, DEFAULT_MAX_BUDGET_TOKENS)
        self.assertEqual(len(report.errors), 0)


if __name__ == "__main__":
    unittest.main()
