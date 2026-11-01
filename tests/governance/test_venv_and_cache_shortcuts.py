"""tests/governance/test_venv_and_cache_shortcuts.py

Verification tests for Wave 1 (Virtualenv Resolution & Test Cache Acceleration):
- In-process memoization and virtualenv fastpath for resolve_python_interpreter() across tools.
- Verification that zero git subprocesses are spawned when sys.prefix != sys.base_prefix.
- Verification of --cache-modules injection in deterministic_commands and e2e_commands.
- Verification of --no-module-cache CLI flag in tools/run_ci_gates.py.
"""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.ci_gates_core.commands import (
    deterministic_commands,
    e2e_commands,
)
from tools.ci_gates_core.runner import resolve_python_interpreter as resolve_ci_python
from tools.pr_governance_core.common import resolve_python as resolve_pr_python
from tools.readiness_core import resolve_python_interpreter as resolve_readiness_python
from tools.run_ci_gates import (
    _parser,
    _resolve_profile_execution,
)


class TestVenvAndCacheShortcuts(unittest.TestCase):
    """Test suite verifying venv resolution shortcuts and test runner module cache flags."""

    def test_readiness_python_venv_fastpath(self) -> None:
        """When in virtualenv, resolve_python_interpreter must return sys.executable and memoize."""
        import tools.readiness_core as rc

        old_cached = rc._RESOLVED_PYTHON_INTERPRETER
        rc._RESOLVED_PYTHON_INTERPRETER = None
        try:
            py = resolve_readiness_python()
            self.assertEqual(py, sys.executable)
            self.assertEqual(rc._RESOLVED_PYTHON_INTERPRETER, sys.executable)
            # Second call should use memoization
            py2 = resolve_readiness_python()
            self.assertEqual(py2, sys.executable)
        finally:
            rc._RESOLVED_PYTHON_INTERPRETER = old_cached

    def test_ci_python_venv_fastpath(self) -> None:
        """ci_gates runner resolve_python_interpreter must prioritize virtualenv and memoize."""
        import tools.ci_gates_core.runner as cr

        old_cached = cr._RESOLVED_CI_PYTHON_INTERPRETER
        cr._RESOLVED_CI_PYTHON_INTERPRETER = None
        try:
            py = resolve_ci_python()
            self.assertEqual(py, sys.executable)
            self.assertEqual(cr._RESOLVED_CI_PYTHON_INTERPRETER, sys.executable)
            py2 = resolve_ci_python()
            self.assertEqual(py2, sys.executable)
        finally:
            cr._RESOLVED_CI_PYTHON_INTERPRETER = old_cached

    def test_pr_governance_python_venv_fastpath(self) -> None:
        """pr_governance common resolve_python must prioritize virtualenv without subprocess."""
        with patch("sys.prefix", "/custom/venv"), patch("sys.base_prefix", "/usr"), patch(
            "pathlib.Path.is_file", return_value=True
        ), patch("pathlib.Path.exists", return_value=False):
            import tools.pr_governance_core.common as prc
            old_cached = prc._RESOLVED_PR_PYTHON
            prc._RESOLVED_PR_PYTHON = None
            try:
                py = resolve_pr_python()
                self.assertEqual(py, sys.executable)
            finally:
                prc._RESOLVED_PR_PYTHON = old_cached

    def test_deterministic_commands_cache_modules_flag(self) -> None:
        """deterministic_commands must include --cache-modules by default and omit when disabled."""
        repo_root = Path("/tmp/mock_repo")
        output_root = Path("/tmp/mock_repo/output")
        tests = ["tests/test_mock.py"]

        cmds_with_cache = deterministic_commands(
            python=sys.executable,
            repository_root=repo_root,
            output_root=output_root,
            tests=tests,
            cache_modules=True,
        )
        test_cmd_with = [c for c in cmds_with_cache if "tools/run_tests_with_progress.py" in c][0]
        self.assertIn("--cache-modules", test_cmd_with)

        cmds_without_cache = deterministic_commands(
            python=sys.executable,
            repository_root=repo_root,
            output_root=output_root,
            tests=tests,
            cache_modules=False,
        )
        test_cmd_without = [c for c in cmds_without_cache if "tools/run_tests_with_progress.py" in c][0]
        self.assertNotIn("--cache-modules", test_cmd_without)

    def test_e2e_commands_cache_modules_flag(self) -> None:
        """e2e_commands must include --cache-modules by default and omit when disabled."""
        cmds_with_cache = e2e_commands(
            python=sys.executable,
            tests=["tests/test_e2e_mock.py"],
            cache_modules=True,
        )
        self.assertIn("--cache-modules", cmds_with_cache[0])

        cmds_without_cache = e2e_commands(
            python=sys.executable,
            tests=["tests/test_e2e_mock.py"],
            cache_modules=False,
        )
        self.assertNotIn("--cache-modules", cmds_without_cache[0])

    def test_run_ci_gates_parser_and_execution_resolution(self) -> None:
        """run_ci_gates parser must support --no-module-cache and propagate to execution commands."""
        parser = _parser()
        args_default = parser.parse_args(["--profile", "e2e", "--python", sys.executable])
        self.assertFalse(args_default.no_module_cache)

        args_no_cache = parser.parse_args(
            ["--profile", "e2e", "--python", sys.executable, "--no-module-cache"]
        )
        self.assertTrue(args_no_cache.no_module_cache)

        repo_root = Path("/tmp/mock_repo")
        output_root = Path("/tmp/mock_repo/output")

        # Verify command generation when default
        _, cmds_default = _resolve_profile_execution(
            args=args_default,
            parser=parser,
            repository_root=repo_root,
            output_root=output_root,
        )
        self.assertIn("--cache-modules", cmds_default[0])

        # Verify command generation when --no-module-cache
        _, cmds_no_cache = _resolve_profile_execution(
            args=args_no_cache,
            parser=parser,
            repository_root=repo_root,
            output_root=output_root,
        )
        self.assertNotIn("--cache-modules", cmds_no_cache[0])

    def test_prune_stale_caches_lifecycle(self) -> None:
        """Verify prune_stale_caches evicts expired items while preserving active and fresh artifacts."""
        import json
        import os
        import tempfile
        import time
        from tools.readiness_cache_core import prune_stale_caches

        with tempfile.TemporaryDirectory(prefix="test_cache_prune_") as tmpdir:
            root = Path(tmpdir)
            now = time.time()
            old_time = now - (30 * 86400)  # 30 days ago
            fresh_time = now - (1 * 86400)  # 1 day ago

            # Directories
            rc_dir = root / ".local" / "readiness_cache"
            tmc_dir = root / ".local" / "test_module_cache"
            cq_dir = root / ".local" / "coverage_qualification"
            rr_dir = root / ".local" / "receipts" / "readiness"
            rep_rr_dir = root / "reports" / "readiness"
            for d in (rc_dir, tmc_dir, cq_dir, rr_dir, rep_rr_dir):
                d.mkdir(parents=True, exist_ok=True)

            # 1. Active items (must be preserved regardless of age)
            active_cq = cq_dir / "active_tree_001"
            active_cq.mkdir()
            (active_cq / "summary.json").write_text("{}", encoding="utf-8")
            os.utime(active_cq, (old_time, old_time))

            active_attest = rr_dir / "active_tree_001.json"
            active_attest.write_text("{}", encoding="utf-8")
            os.utime(active_attest, (old_time, old_time))

            active_receipt = rep_rr_dir / "local_main_readiness_active_slug_receipt.json"
            active_receipt.write_text(json.dumps({"head_commit_sha": "active_sha_001"}), encoding="utf-8")
            os.utime(active_receipt, (old_time, old_time))

            main_receipt = rep_rr_dir / "local_main_readiness_receipt.json"
            main_receipt.write_text("{}", encoding="utf-8")
            os.utime(main_receipt, (old_time, old_time))

            # 2. Fresh items (must be preserved due to retention threshold)
            fresh_rc = rc_dir / "fresh_rc.json"
            fresh_rc.write_text("{}", encoding="utf-8")
            os.utime(fresh_rc, (fresh_time, fresh_time))

            # 3. Expired items (must be pruned)
            expired_rc = rc_dir / "expired_rc.json"
            expired_rc.write_text("{}", encoding="utf-8")
            os.utime(expired_rc, (old_time, old_time))

            expired_tmc = tmc_dir / "expired_tmc.json"
            expired_tmc.write_text("{}", encoding="utf-8")
            os.utime(expired_tmc, (old_time, old_time))

            expired_cq = cq_dir / "expired_tree_002"
            expired_cq.mkdir()
            (expired_cq / "meta.json").write_text("{}", encoding="utf-8")
            os.utime(expired_cq, (old_time, old_time))

            expired_attest = rr_dir / "expired_tree_002.json"
            expired_attest.write_text("{}", encoding="utf-8")
            os.utime(expired_attest, (old_time, old_time))

            expired_receipt = rep_rr_dir / "local_main_readiness_stale_slug_receipt.json"
            expired_receipt.write_text(json.dumps({"head_commit_sha": "stale_sha_999"}), encoding="utf-8")
            os.utime(expired_receipt, (old_time, old_time))

            # Dry run test
            res_dry = prune_stale_caches(
                root,
                active_tree_digests=["active_tree_001"],
                active_head_shas=["active_sha_001"],
                max_age_days=7,
                dry_run=True,
            )
            self.assertEqual(res_dry["readiness_cache"], 1)
            self.assertEqual(res_dry["test_module_cache"], 1)
            self.assertEqual(res_dry["coverage_qualification"], 1)
            self.assertEqual(res_dry["readiness_attestations"], 1)
            self.assertEqual(res_dry["readiness_receipts"], 1)
            self.assertEqual(res_dry["total_pruned"], 5)
            self.assertTrue(expired_rc.exists())
            self.assertTrue(expired_cq.exists())

            # Actual execution test
            res_exec = prune_stale_caches(
                root,
                active_tree_digests=["active_tree_001"],
                active_head_shas=["active_sha_001"],
                max_age_days=7,
                dry_run=False,
            )
            self.assertEqual(res_exec["total_pruned"], 5)

            # Verification: expired are gone
            self.assertFalse(expired_rc.exists())
            self.assertFalse(expired_tmc.exists())
            self.assertFalse(expired_cq.exists())
            self.assertFalse(expired_attest.exists())
            self.assertFalse(expired_receipt.exists())

            # Verification: active and fresh are intact
            self.assertTrue(active_cq.exists())
            self.assertTrue(active_attest.exists())
            self.assertTrue(active_receipt.exists())
            self.assertTrue(main_receipt.exists())
            self.assertTrue(fresh_rc.exists())

    def test_pr_governance_parser_prune_flags(self) -> None:
        """Verify pr_governance parser supports --prune-caches across doctor and sync."""
        from tools.pr_governance import build_parser

        parser = build_parser()
        args_doc = parser.parse_args(["doctor", "--prune-caches", "--retention-days", "14"])
        self.assertTrue(args_doc.prune_caches)
        self.assertEqual(args_doc.retention_days, 14)

        args_sync = parser.parse_args(["sync", "feat/test", "--prune-caches"])
        self.assertTrue(args_sync.prune_caches)


if __name__ == "__main__":
    unittest.main()

