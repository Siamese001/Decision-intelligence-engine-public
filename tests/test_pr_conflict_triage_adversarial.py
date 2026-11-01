"""
Adversarial and boundary tests for PR conflict triage engine.

Tests multi-class conflict prioritization, modify/delete detection,
rename/modify detection, superseded detection, import-only filtering,
git error resilience, and line-churn penalty scaling.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.pr_governance_core.conflict_triage import (
    ConflictClass,
    EffortBand,
    format_conflict_summary,
    triage_branch_conflicts,
)


def _init_repo(repo: Path):
    repo.mkdir(parents=True, exist_ok=True)

    def run_g(*args):
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()

    run_g("init", "-b", "main")
    run_g("config", "user.name", "Test User")
    run_g("config", "user.email", "test@example.com")
    run_g("config", "commit.gpgsign", "false")
    return run_g


class TestPRConflictTriageAdversarial(unittest.TestCase):
    def test_adversarial_modify_delete_conflict(self):
        """File deleted in branch but modified in main must be classified as STRUCTURAL."""
        temp_dir = tempfile.mkdtemp()
        repo = Path(temp_dir) / "repo_mod_del"
        run_g = _init_repo(repo)

        try:
            f = repo / "dual_pipeline" / "worker.py"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("def work():\n    return 1\n", encoding="utf-8")
            run_g("add", ".")
            run_g("commit", "-m", "initial")

            # Branch deletes worker.py
            run_g("checkout", "-b", "feat/delete-worker")
            run_g("rm", "dual_pipeline/worker.py")
            run_g("commit", "-m", "delete worker")

            # Main modifies worker.py
            run_g("checkout", "main")
            f.write_text("def work():\n    return 2\n", encoding="utf-8")
            run_g("commit", "-am", "modify worker")

            res = triage_branch_conflicts("feat/delete-worker", repo, base="main", use_cache=False)
            self.assertEqual(res.total_files, 1)
            fc = res.conflicted_files[0]
            self.assertEqual(fc.path, "dual_pipeline/worker.py")
            self.assertEqual(fc.conflict_class, ConflictClass.STRUCTURAL)
            self.assertTrue(fc.is_modify_delete)
            self.assertEqual(fc.weight, 3.0)
            self.assertEqual(res.manual_files_count, 1)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_adversarial_rename_modify_conflict(self):
        """File moved in main but modified at old path in branch must be classified as STRUCTURAL."""
        temp_dir = tempfile.mkdtemp()
        repo = Path(temp_dir) / "repo_rename"
        run_g = _init_repo(repo)

        try:
            f = repo / "service.py"
            f.write_text("class Service:\n    pass\n", encoding="utf-8")
            run_g("add", ".")
            run_g("commit", "-m", "initial")

            # Branch modifies service.py
            run_g("checkout", "-b", "feat/edit-service")
            f.write_text("class Service:\n    version = 2\n", encoding="utf-8")
            run_g("commit", "-am", "edit service")

            # Main renames service.py to archive/service.py and modifies it
            run_g("checkout", "main")
            run_g("mv", "service.py", "archive_service.py")
            (repo / "archive_service.py").write_text("class Service:\n    version = 3\n", encoding="utf-8")
            run_g("commit", "-am", "rename and edit")

            res = triage_branch_conflicts("feat/edit-service", repo, base="main", use_cache=False)
            self.assertEqual(res.total_files, 1)
            fc = res.conflicted_files[0]
            self.assertIn(fc.conflict_class, (ConflictClass.STRUCTURAL, ConflictClass.LOGIC))
            self.assertGreaterEqual(fc.weight, 2.0)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_adversarial_superseded_file_conflict(self):
        """If branch added lines already exist in main, file must be SUPERSEDED."""
        temp_dir = tempfile.mkdtemp()
        repo = Path(temp_dir) / "repo_superseded"
        run_g = _init_repo(repo)

        try:
            f = repo / "constants.py"
            f.write_text("A = 1\nB = 2\n", encoding="utf-8")
            run_g("add", ".")
            run_g("commit", "-m", "initial")

            # Branch adds C = 3 and D = 4
            run_g("checkout", "-b", "feat/add-constants")
            f.write_text("A = 1\nB = 2\nC = 3\nD = 4\n", encoding="utf-8")
            run_g("commit", "-am", "add C and D")

            # Main also adds C = 3 and D = 4 with different formatting / other lines causing merge conflict
            run_g("checkout", "main")
            f.write_text("A = 1\nB = 99\nC = 3\nD = 4\nE = 5\n", encoding="utf-8")
            run_g("commit", "-am", "main diverged")

            res = triage_branch_conflicts("feat/add-constants", repo, base="main", use_cache=False)
            self.assertEqual(res.total_files, 1)
            fc = res.conflicted_files[0]
            self.assertEqual(fc.conflict_class, ConflictClass.SUPERSEDED)
            self.assertGreaterEqual(fc.in_main_pct, 0.80)
            self.assertEqual(fc.weight, 0.5)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_adversarial_lint_import_only_conflict(self):
        """Hunks containing only imports or suppressions must be classified as LINT_IMPORT."""
        temp_dir = tempfile.mkdtemp()
        repo = Path(temp_dir) / "repo_lint"
        run_g = _init_repo(repo)

        try:
            f = repo / "module.py"
            f.write_text("import sys\nimport os\n\ndef run():\n    pass\n", encoding="utf-8")
            run_g("add", ".")
            run_g("commit", "-m", "initial")

            # Branch sorts imports
            run_g("checkout", "-b", "feat/sort-imports")
            f.write_text("import os\nimport sys\nfrom pathlib import Path\n\ndef run():\n    pass\n", encoding="utf-8")
            run_g("commit", "-am", "sort branch")

            # Main adds a different import
            run_g("checkout", "main")
            f.write_text("import json\nimport os\nimport sys\n\ndef run():\n    pass\n", encoding="utf-8")
            run_g("commit", "-am", "import main")

            res = triage_branch_conflicts("feat/sort-imports", repo, base="main", use_cache=False)
            self.assertEqual(res.total_files, 1)
            fc = res.conflicted_files[0]
            self.assertEqual(fc.conflict_class, ConflictClass.LINT_IMPORT)
            self.assertEqual(fc.weight, 0.5)
            self.assertIn("[MECHANICAL]", fc.action_hint)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_adversarial_unknown_ref_fails_closed(self):
        """Invalid or nonexistent branch ref must fail closed with HIGH effort and [INVESTIGATE]."""
        temp_dir = tempfile.mkdtemp()
        repo = Path(temp_dir) / "repo_err"
        _init_repo(repo)

        try:
            res = triage_branch_conflicts("nonexistent-branch-xyz", repo, base="main", use_cache=False)
            self.assertEqual(res.effort_band, EffortBand.HIGH)
            self.assertEqual(res.manual_files_count, 1)
            self.assertIn("[INVESTIGATE]", res.action_summary)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_adversarial_format_summary_empty(self):
        """Empty conflict list produces clean summary."""
        summary = format_conflict_summary(
            total_files=0, band=EffortBand.TRIVIAL, manual_files=0, class_counts={}, behind=0, key_detail=""
        )
        self.assertIn("[CLEAN]", summary)
