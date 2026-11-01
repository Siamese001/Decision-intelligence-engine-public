"""Tests for Workspace Skills Context Budget & Governance.

Verifies:
  1. All workspace skills in .agents/skills/ comply with the 2,000 token ceiling.
  2. Budget utilization does not exceed 100.0%.
  3. Description length per skill stays within 100 tokens.
  4. Descriptions remain model-neutral and free of vendor branding.
  5. .agents/skills.json matches existing workspace skills.
  6. Audit tool detects overages, banned tokens, and emits valid signed receipts.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from tools.audit_skills_budget import (
    DEFAULT_CAPACITY_CLEANUP_THRESHOLD_PCT,
    DEFAULT_MAX_BUDGET_TOKENS,
    DEFAULT_MAX_DESCRIPTION_TOKENS,
    DEFAULT_MAX_SKILLS_COUNT,
    DEFAULT_PLUGINS_DIR,
    DEFAULT_SKILLS_DIR,
    DEFAULT_SKILLS_JSON,
    audit_single_skill,
    audit_skills_budget,
    build_audit_receipt,
    cleanup_skills_at_capacity,
    compact_skill_description,
    main,
    parse_frontmatter,
)


class TestSkillsBudgetGovernance(unittest.TestCase):
    _live_report = None

    @classmethod
    def setUpClass(cls) -> None:
        cls._live_report = audit_skills_budget(
            skills_dir=DEFAULT_SKILLS_DIR,
            plugins_dir=DEFAULT_PLUGINS_DIR,
            skills_json=DEFAULT_SKILLS_JSON,
            max_budget_tokens=DEFAULT_MAX_BUDGET_TOKENS,
            max_desc_tokens=DEFAULT_MAX_DESCRIPTION_TOKENS,
            max_skills_count=DEFAULT_MAX_SKILLS_COUNT,
            verify_disk=True,
        )

    def test_workspace_skills_are_within_budget(self) -> None:
        """Workspace skills must stay below 2,000 tokens and 100% budget utilization."""
        report = self._live_report or audit_skills_budget(
            skills_dir=DEFAULT_SKILLS_DIR,
            max_budget_tokens=DEFAULT_MAX_BUDGET_TOKENS,
            max_desc_tokens=DEFAULT_MAX_DESCRIPTION_TOKENS,
            max_skills_count=DEFAULT_MAX_SKILLS_COUNT,
        )
        self.assertTrue(
            report.is_overall_compliant,
            f"Workspace skills failed governance audit: {report.errors}",
        )
        self.assertTrue(report.is_budget_compliant)
        self.assertTrue(report.is_count_compliant)
        self.assertLessEqual(report.budget_utilization_pct, 100.0)
        self.assertLessEqual(report.total_skills, DEFAULT_MAX_SKILLS_COUNT)
        self.assertGreater(report.total_skills, 0)
        self.assertEqual(len(report.errors), 0)

    def test_skills_json_matches_workspace_skills(self) -> None:
        """.agents/skills.json must accurately inventory all existing workspace skills."""
        self.assertTrue(DEFAULT_SKILLS_JSON.is_file(), ".agents/skills.json must exist")
        data = json.loads(DEFAULT_SKILLS_JSON.read_text(encoding="utf-8"))

        registered_names = {s["name"] for s in data.get("skills", [])}
        disk_skills = {
            p.name
            for p in DEFAULT_SKILLS_DIR.iterdir()
            if p.is_dir() and not p.name.startswith(".")
        }

        self.assertEqual(
            registered_names,
            disk_skills,
            f"Discrepancy between .agents/skills.json and .agents/skills/: "
            f"In JSON only: {registered_names - disk_skills}, On disk only: {disk_skills - registered_names}",
        )
        self.assertEqual(len(disk_skills), data.get("canonical_skills_count"))

    def test_every_skill_has_valid_frontmatter_and_bounds(self) -> None:
        """Every workspace skill must have valid name, description, and token counts."""
        report = self._live_report
        canonical_results = [r for r in report.skills if r.skill_type == "core"] if report else [
            audit_single_skill(skill_dir, max_desc_tokens=DEFAULT_MAX_DESCRIPTION_TOKENS)
            for skill_dir in DEFAULT_SKILLS_DIR.iterdir()
            if skill_dir.is_dir() and not skill_dir.name.startswith(".")
        ]
        self.assertGreater(len(canonical_results), 0)
        for res in canonical_results:
            self.assertEqual(
                len(res.violations),
                0,
                f"Skill '{res.skill_name}' has violations: {res.violations}",
            )
            self.assertTrue(res.is_description_compliant)
            self.assertTrue(res.is_model_neutral)
            self.assertLessEqual(res.description_token_count, DEFAULT_MAX_DESCRIPTION_TOKENS)

    def test_parse_frontmatter_multiline_and_folded(self) -> None:
        """parse_frontmatter handles scalar, folded (>), and literal (|) frontmatter."""
        raw = """---
name: test-skill
description: >
  This is a folded
  multiline description
  for testing.
---
# Body
Some content here.
"""
        fm, body = parse_frontmatter(raw)
        self.assertEqual(fm.get("name"), "test-skill")
        self.assertIn("folded multiline description", fm.get("description", ""))
        self.assertIn("# Body", body)

    def test_detects_budget_overutilization(self) -> None:
        """Synthetic test verifying audit fails when utilization > 100%."""
        report = audit_skills_budget(
            skills_dir=DEFAULT_SKILLS_DIR,
            max_budget_tokens=500,  # Below actual ~1260 tokens
        )
        self.assertFalse(report.is_budget_compliant)
        self.assertFalse(report.is_overall_compliant)
        self.assertGreater(report.budget_utilization_pct, 100.0)
        self.assertTrue(any("exceeds budget" in err for err in report.errors))

    def test_detects_banned_vendor_model_tokens(self) -> None:
        """Auditor must flag descriptions containing vendor model branding."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_skill = Path(tmpdir) / "vendor-branded-skill"
            tmp_skill.mkdir()
            skill_md = tmp_skill / "SKILL.md"
            skill_md.write_text(
                "---\nname: vendor-branded-skill\ndescription: Use with ChatGPT and Codex.\n---\n# Content\n",
                encoding="utf-8",
            )
            res = audit_single_skill(tmp_skill)
            self.assertFalse(res.is_model_neutral)
            self.assertTrue(any("LLM neutrality" in v for v in res.violations))

    def test_detects_description_token_ceiling_exceeded(self) -> None:
        """Auditor must flag skills whose descriptions exceed max_desc_tokens."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_skill = Path(tmpdir) / "long-desc-skill"
            tmp_skill.mkdir()
            skill_md = tmp_skill / "SKILL.md"
            long_desc = "word " * 150
            skill_md.write_text(
                f"---\nname: long-desc-skill\ndescription: {long_desc}\n---\n# Content\n",
                encoding="utf-8",
            )
            res = audit_single_skill(tmp_skill, max_desc_tokens=50)
            self.assertFalse(res.is_description_compliant)
            self.assertTrue(any("exceeds maximum ceiling" in v for v in res.violations))

    def test_build_audit_receipt_canonical_digest(self) -> None:
        """Receipt must include valid schema, status, metrics, and sha256 canonical digest."""
        report = self._live_report or audit_skills_budget(skills_dir=DEFAULT_SKILLS_DIR)
        receipt = build_audit_receipt(report, command="audit-test-command")
        self.assertEqual(receipt["$schema"], "urn:nee:skills-budget-receipt:1.0.0")
        self.assertEqual(receipt["status"], "PASSED")
        self.assertIn("canonical_digest", receipt)
        self.assertEqual(len(receipt["canonical_digest"]), 64)
        self.assertEqual(receipt["metrics"]["total_skills"], report.total_skills)

    def test_cli_execution_with_receipt(self) -> None:
        """CLI main() execution produces receipt and exits with code 0 on compliance."""
        with tempfile.TemporaryDirectory() as tmpdir:
            receipt_path = Path(tmpdir) / "test_receipt.json"
            exit_code = main(["--check", "--receipt", str(receipt_path)])
            self.assertEqual(exit_code, 0)
            self.assertTrue(receipt_path.is_file())
            data = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "PASSED")

    def test_skills_json_matches_plugin_skills(self) -> None:
        """.agents/skills.json must accurately inventory all existing plugin skills."""
        data = json.loads(DEFAULT_SKILLS_JSON.read_text(encoding="utf-8"))
        registered_plugins = {s["name"] for s in data.get("plugin_skills", [])}
        disk_plugins = set()
        if DEFAULT_PLUGINS_DIR.is_dir():
            for pdir in DEFAULT_PLUGINS_DIR.iterdir():
                if not pdir.is_dir() or pdir.name.startswith("."):
                    continue
                pskills = pdir / "skills"
                if pskills.is_dir():
                    for sp in pskills.iterdir():
                        if sp.is_dir() and not sp.name.startswith("."):
                            disk_plugins.add(sp.name)

        self.assertEqual(
            registered_plugins,
            disk_plugins,
            f"Discrepancy between .agents/skills.json and plugin skills: "
            f"In JSON only: {registered_plugins - disk_plugins}, On disk only: {disk_plugins - registered_plugins}",
        )
        self.assertEqual(len(disk_plugins), data.get("plugin_skills_count", 0))

    def test_plugin_skills_are_compliant(self) -> None:
        """Every plugin skill must have valid frontmatter, model neutrality, and bounds."""
        if not DEFAULT_PLUGINS_DIR.is_dir():
            return
        report = self._live_report
        if report:
            for res in (r for r in report.skills if r.skill_type == "plugin"):
                self.assertEqual(
                    len(res.violations),
                    0,
                    f"Plugin skill '{res.skill_name}' has violations: {res.violations}",
                )
                self.assertTrue(res.is_description_compliant)
                self.assertTrue(res.is_model_neutral)
            return
        for pdir in DEFAULT_PLUGINS_DIR.iterdir():
            if not pdir.is_dir() or pdir.name.startswith("."):
                continue
            pskills = pdir / "skills"
            if pskills.is_dir():
                for sp in pskills.iterdir():
                    if not sp.is_dir() or sp.name.startswith("."):
                        continue
                    res = audit_single_skill(sp, max_desc_tokens=DEFAULT_MAX_DESCRIPTION_TOKENS, skill_type="plugin")
                    self.assertEqual(
                        len(res.violations),
                        0,
                        f"Plugin skill '{sp.name}' has violations: {res.violations}",
                    )
                    self.assertTrue(res.is_description_compliant)
                    self.assertTrue(res.is_model_neutral)

    def test_all_skills_have_valid_disk_references(self) -> None:
        """All core and plugin skills must reference existing scripts, modules, and links."""
        report = self._live_report or audit_skills_budget(
            skills_dir=DEFAULT_SKILLS_DIR,
            plugins_dir=DEFAULT_PLUGINS_DIR,
            skills_json=DEFAULT_SKILLS_JSON,
            verify_disk=True,
        )
        self.assertTrue(report.is_ssot_compliant, f"SSOT check failed: {report.errors}")
        self.assertTrue(report.is_overall_compliant, f"Overall check failed: {report.errors}")
        data = json.loads(DEFAULT_SKILLS_JSON.read_text(encoding="utf-8"))
        self.assertEqual(report.total_skills, data.get("total_skills_count"))
        self.assertEqual(report.canonical_skills_count, data.get("canonical_skills_count"))
        self.assertEqual(report.plugin_skills_count, data.get("plugin_skills_count"))

    def test_detects_missing_script_file(self) -> None:
        """Auditor must flag skills referencing non-existent script files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_skill = Path(tmpdir) / "ghost-script-skill"
            tmp_skill.mkdir()
            skill_md = tmp_skill / "SKILL.md"
            skill_md.write_text(
                "---\nname: ghost-script-skill\ndescription: Test skill with ghost script.\n---\n"
                "Execute: ```bash\npython scripts/non_existent_runner.py\n```\n",
                encoding="utf-8",
            )
            res = audit_single_skill(tmp_skill, verify_disk=True)
            self.assertTrue(any("does not exist on disk" in v for v in res.violations))

    def test_detects_banned_vendor_artifact_in_skill_directory(self) -> None:
        """Auditor must flag skills containing vendor model files (e.g. openai.yaml)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_skill = Path(tmpdir) / "vendor-file-skill"
            tmp_skill.mkdir()
            skill_md = tmp_skill / "SKILL.md"
            skill_md.write_text(
                "---\nname: vendor-file-skill\ndescription: Test skill with vendor file.\n---\n# Body\n",
                encoding="utf-8",
            )
            (tmp_skill / "agents").mkdir()
            (tmp_skill / "agents" / "openai.yaml").write_text("model: gpt-4\n", encoding="utf-8")
            res = audit_single_skill(tmp_skill, verify_disk=True)
            self.assertTrue(any("Model neutrality file violation" in v for v in res.violations))

    def test_all_skills_json_test_targets_resolve_on_disk(self) -> None:
        """Every skill registered in .agents/skills.json must specify an existing test_target on disk."""
        self.assertTrue(DEFAULT_SKILLS_JSON.is_file(), ".agents/skills.json must exist")
        data = json.loads(DEFAULT_SKILLS_JSON.read_text(encoding="utf-8"))
        all_skills = data.get("skills", []) + data.get("plugin_skills", [])
        self.assertGreater(len(all_skills), 0)

        missing = []
        for s in all_skills:
            name = s.get("name")
            tt = s.get("test_target")
            if not tt or not (ROOT / tt).is_file():
                missing.append((name, tt))

        self.assertEqual(
            missing,
            [],
            f"Skills in .agents/skills.json reference missing test_target files: {missing}",
        )

    def test_capacity_cleanup_triggers_and_compacts_at_90_percent_token_budget(self) -> None:
        """cleanup_skills_at_capacity triggers when token utilization >= 90% and compacts below 90%."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root_dir = Path(tmpdir)
            skills_dir = root_dir / ".agents" / "skills"
            skills_dir.mkdir(parents=True)
            skills_json = root_dir / ".agents" / "skills.json"

            # Create 3 verbose skills consuming ~190 tokens against a 200-token budget (95% capacity)
            names = ["skill-alpha", "skill-beta", "skill-gamma"]
            for name in names:
                sdir = skills_dir / name
                sdir.mkdir()
                desc = (
                    "Comprehensive guide and reference for designing, orchestrating, validating, and "
                    "troubleshooting complex multi-stage distributed pipelines across local runtimes. "
                    "Use this skill when the user wants to configure or inspect system parameters, "
                    "evaluate trade-offs, or run diagnostic checks across local workflows."
                )
                (sdir / "SKILL.md").write_text(
                    f"---\nname: {name}\ndescription: {desc}\n---\n# {name}\nBody content.\n",
                    encoding="utf-8",
                )

            skills_json.write_text(
                json.dumps({"skills": [{"name": n} for n in names], "plugin_skills": []}),
                encoding="utf-8",
            )

            pre = audit_skills_budget(
                skills_dir=skills_dir,
                plugins_dir=None,
                skills_json=skills_json,
                max_budget_tokens=160,
                max_skills_count=26,
                repo_root=root_dir,
                verify_disk=False,
            )
            self.assertGreaterEqual(pre.budget_utilization_pct, 90.0)
            self.assertTrue(pre.is_at_or_above_capacity_threshold)

            res = cleanup_skills_at_capacity(
                skills_dir=skills_dir,
                plugins_dir=None,
                skills_json=skills_json,
                max_budget_tokens=160,
                max_skills_count=26,
                capacity_threshold_pct=DEFAULT_CAPACITY_CLEANUP_THRESHOLD_PCT,
                repo_root=root_dir,
                verify_disk=False,
            )
            self.assertTrue(res["triggered"])
            self.assertGreater(len(res["actions"]), 0)
            self.assertLess(res["after_token_utilization_pct"], 90.0)
            self.assertFalse(res["report"].is_at_or_above_capacity_threshold)

    def test_capacity_cleanup_prunes_unregistered_skills_at_90_percent_count(self) -> None:
        """cleanup_skills_at_capacity prunes unregistered skill folders when skill count >= 90%."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root_dir = Path(tmpdir)
            skills_dir = root_dir / ".agents" / "skills"
            skills_dir.mkdir(parents=True)
            skills_json = root_dir / ".agents" / "skills.json"

            # 9 registered + 1 unregistered = 10/10 skills (100% >= 90% count capacity)
            reg_names = [f"skill-{i}" for i in range(9)]
            for name in reg_names + ["unregistered-stale-skill"]:
                sdir = skills_dir / name
                sdir.mkdir()
                (sdir / "SKILL.md").write_text(
                    f"---\nname: {name}\ndescription: Concise domain skill {name}.\n---\n# Body\n",
                    encoding="utf-8",
                )

            skills_json.write_text(
                json.dumps({"skills": [{"name": n} for n in reg_names], "plugin_skills": []}),
                encoding="utf-8",
            )

            res = cleanup_skills_at_capacity(
                skills_dir=skills_dir,
                plugins_dir=None,
                skills_json=skills_json,
                max_budget_tokens=2000,
                max_skills_count=10,
                capacity_threshold_pct=90.0,
                repo_root=root_dir,
                verify_disk=False,
            )
            self.assertTrue(res["triggered"])
            self.assertFalse((skills_dir / "unregistered-stale-skill").exists())
            self.assertTrue(any(a["action"] == "pruned_unregistered_skill" for a in res["actions"]))

    def test_capacity_cleanup_is_noop_below_90_percent(self) -> None:
        """cleanup_skills_at_capacity does not mutate skills when capacity is below 90%."""
        res = cleanup_skills_at_capacity(
            skills_dir=DEFAULT_SKILLS_DIR,
            plugins_dir=DEFAULT_PLUGINS_DIR,
            skills_json=DEFAULT_SKILLS_JSON,
            verify_disk=False,
        )
        self.assertFalse(res["triggered"])
        self.assertEqual(res["actions"], [])
        self.assertLess(res["after_token_utilization_pct"], 90.0)


if __name__ == "__main__":
    unittest.main()
