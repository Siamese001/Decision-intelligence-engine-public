"""
tests/governance/test_scope_collection_regressions.py

Regression cases verifying resolution of all 7 scope collection defect categories
in get_branch_target_files and readiness planning (Wave 3).

Covers all 7 mandatory defect categories:
1. Deletions: Accurately captured (diff-filter restriction removed).
2. Renames: Endpoints symmetrically captured as both old and new paths.
3. Unknown extensions: Files without standard extensions (.sql, .cfg) retained.
4. Shell/extensionless hooks: .githooks/* included in target files.
5. Untracked & dirty inputs: Untracked files captured from working tree.
6. Ingestion paths: ingestion/ paths retained in target scope.
7. Git collection errors: Explicit RuntimeError raised on non-git repositories.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.readiness_core import get_branch_target_files


class TestScopeCollectionRegressions(unittest.TestCase):
    """Verify resolution of all 7 scope collection defect categories in get_branch_target_files."""

    def _init_repo(self, repo_dir: Path) -> None:
        subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test Agent"], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "agent@test.local"], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo_dir, check=True, capture_output=True)
        (repo_dir / "README.md").write_text("# Test Repo\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "chore: initial commit"], cwd=repo_dir, check=True, capture_output=True)

    def test_regression_deletion_excluded_by_diff_filter(self) -> None:
        """Category 1: Deleted files are captured."""
        with tempfile.TemporaryDirectory(prefix="scope_reg_del_") as td:
            repo = Path(td)
            self._init_repo(repo)

            victim = repo / "module_victim.py"
            victim.write_text("print('victim')\n", encoding="utf-8")
            subprocess.run(["git", "add", "module_victim.py"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "feat: add victim module"], cwd=repo, check=True, capture_output=True)

            subprocess.run(["git", "checkout", "-b", "feat/deletion-test"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "rm", "module_victim.py"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "fix: remove victim module"], cwd=repo, check=True, capture_output=True)

            targets = get_branch_target_files(repo)
            self.assertIn("module_victim.py", targets)

    def test_regression_rename_endpoints_accounting(self) -> None:
        """Category 2: Renames track both source and destination endpoints symmetrically."""
        with tempfile.TemporaryDirectory(prefix="scope_reg_ren_") as td:
            repo = Path(td)
            self._init_repo(repo)

            old_file = repo / "old_name.py"
            old_file.write_text("print('hello')\n", encoding="utf-8")
            subprocess.run(["git", "add", "old_name.py"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "feat: add old name"], cwd=repo, check=True, capture_output=True)

            subprocess.run(["git", "checkout", "-b", "feat/rename-test"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "mv", "old_name.py", "new_name.py"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "refactor: rename file"], cwd=repo, check=True, capture_output=True)

            targets = get_branch_target_files(repo)
            self.assertIn("new_name.py", targets)
            self.assertIn("old_name.py", targets)

    def test_regression_unknown_extensions_omitted(self) -> None:
        """Category 3: Non-standard extensions (.sql, .cfg) are retained in target files."""
        with tempfile.TemporaryDirectory(prefix="scope_reg_ext_") as td:
            repo = Path(td)
            self._init_repo(repo)

            subprocess.run(["git", "checkout", "-b", "feat/ext-test"], cwd=repo, check=True, capture_output=True)
            sql_file = repo / "query.sql"
            sql_file.write_text("SELECT 1;\n", encoding="utf-8")
            cfg_file = repo / "settings.cfg"
            cfg_file.write_text("[section]\nkey=val\n", encoding="utf-8")

            subprocess.run(["git", "add", "query.sql", "settings.cfg"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "feat: add sql and cfg"], cwd=repo, check=True, capture_output=True)

            targets = get_branch_target_files(repo)
            self.assertIn("query.sql", targets)
            self.assertIn("settings.cfg", targets)

    def test_regression_shell_and_extensionless_hooks_omitted(self) -> None:
        """Category 4: Shell scripts and extensionless hook files (.githooks/*) are retained."""
        with tempfile.TemporaryDirectory(prefix="scope_reg_hook_") as td:
            repo = Path(td)
            self._init_repo(repo)

            subprocess.run(["git", "checkout", "-b", "feat/hook-test"], cwd=repo, check=True, capture_output=True)
            hooks_dir = repo / ".githooks"
            hooks_dir.mkdir(parents=True, exist_ok=True)
            pre_commit = hooks_dir / "pre-commit"
            pre_commit.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            common_sh = hooks_dir / "_common.sh"
            common_sh.write_text("#!/bin/sh\n", encoding="utf-8")

            subprocess.run(["git", "add", ".githooks/"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "chore: add hooks"], cwd=repo, check=True, capture_output=True)

            targets = get_branch_target_files(repo)
            self.assertIn(".githooks/pre-commit", targets)
            self.assertIn(".githooks/_common.sh", targets)

    def test_regression_untracked_and_dirty_inputs_omitted(self) -> None:
        """Category 5: Untracked and uncommitted files are captured in scope collection."""
        with tempfile.TemporaryDirectory(prefix="scope_reg_untracked_") as td:
            repo = Path(td)
            self._init_repo(repo)

            subprocess.run(["git", "checkout", "-b", "feat/dirty-test"], cwd=repo, check=True, capture_output=True)
            untracked_py = repo / "untracked_script.py"
            untracked_py.write_text("print('untracked')\n", encoding="utf-8")

            targets = get_branch_target_files(repo)
            self.assertIn("untracked_script.py", targets)

    def test_regression_untracked_reports_artifacts_excluded(self) -> None:
        """Category 5b: Ephemeral untracked test/readiness receipts in reports/ are excluded from target scope."""
        with tempfile.TemporaryDirectory(prefix="scope_reg_reports_") as td:
            repo = Path(td)
            self._init_repo(repo)

            subprocess.run(["git", "checkout", "-b", "feat/reports-test"], cwd=repo, check=True, capture_output=True)
            report_receipt = repo / "reports" / "readiness" / "receipt.json"
            report_receipt.parent.mkdir(parents=True, exist_ok=True)
            report_receipt.write_text('{"status": "PASS"}\n', encoding="utf-8")

            targets = get_branch_target_files(repo)
            self.assertNotIn("reports/readiness/receipt.json", targets)

    def test_regression_ignored_paths_consumed_by_gates(self) -> None:
        """Category 6: Ingestion paths are retained in target scope."""
        with tempfile.TemporaryDirectory(prefix="scope_reg_ingest_") as td:
            repo = Path(td)
            self._init_repo(repo)

            subprocess.run(["git", "checkout", "-b", "feat/ingest-test"], cwd=repo, check=True, capture_output=True)
            ingest_dir = repo / "ingestion" / "source_pack"
            ingest_dir.mkdir(parents=True, exist_ok=True)
            ingest_file = ingest_dir / "meta.json"
            ingest_file.write_text('{"status": "raw"}\n', encoding="utf-8")

            subprocess.run(["git", "add", "ingestion/"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "feat: add ingestion meta"], cwd=repo, check=True, capture_output=True)

            targets = get_branch_target_files(repo)
            self.assertIn("ingestion/source_pack/meta.json", targets)

    def test_regression_git_collection_errors_suppressed(self) -> None:
        """Category 7: Git collection errors raise explicit RuntimeError instead of swallowing."""
        with tempfile.TemporaryDirectory(prefix="scope_reg_err_") as td:
            non_repo = Path(td)
            with self.assertRaises(RuntimeError):
                get_branch_target_files(non_repo)


if __name__ == "__main__":
    unittest.main()
