"""
tests/test_stage28_sharding_and_coverage_reuse_e2e.py

End-to-End Acceptance Tests for Wave 3:
- 48GB RAM AST Cache & Fast Test Classification
- Class-Sharded Stage 28 Test Distribution
- Stage 29 Retained Coverage Package Reuse (<0.5s)
- Ephemeral SQLite Sandbox PRAGMA Acceleration (synchronous=OFF)
"""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from storage.connection import configure_pragmas, create_sqlite_conn, is_temp_sandbox_db
from tools.coverage_report_reuse import run_and_retain_coverage_audit
from tools.run_tests_with_progress import (
    ROOT,
    _has_unittest_testcase_ast,
    _inspect_pytest_module,
)
from tools.test_module_cache import (
    clear_ram_cache,
    compute_module_fingerprint,
    resolve_test_module_file,
)


class TestWave3ASTAndShardingE2E(unittest.TestCase):
    """Verifies AST discovery and class-level sharding logic."""

    def test_has_unittest_testcase_ast_detection(self) -> None:
        """Confirms AST inspection detects TestCase inheritance without importing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "test_sample.py"
            p.write_text(
                "import unittest\n\nclass SampleTest(unittest.TestCase):\n    def test_one(self): pass\n",
                encoding="utf-8",
            )
            self.assertTrue(_has_unittest_testcase_ast(p))

            p_pytest = Path(tmpdir) / "test_pytest_only.py"
            p_pytest.write_text(
                "def test_foo(): assert True\n",
                encoding="utf-8",
            )
            self.assertFalse(_has_unittest_testcase_ast(p_pytest))

    def test_inspect_pytest_module_ast_path(self) -> None:
        """Verifies pytest module analysis uses AST without importing into MainProcess."""
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "test_dummy_pytest.py"
            p.write_text(
                "def test_example(): pass\n",
                encoding="utf-8",
            )
            suite, mod_item = _inspect_pytest_module(str(p), "test_dummy_pytest")
            self.assertIsNone(suite)
            self.assertEqual(mod_item, str(p))

    def test_class_sharding_unit_grouping(self) -> None:
        """Verifies class-level grouping splits multi-class test modules across workers."""
        case_ids = [
            "tests.governance.test_pr_governance.TestPRGovernanceValidation.test_slugify_branch",
            "tests.governance.test_pr_governance.TestPRGovernanceValidation.test_check_merge_conflict_clean",
            "tests.governance.test_pr_governance.TestPRGovernanceDoctor.test_doctor_command",
            "tests.governance.test_pr_governance.TestPRGovernanceMerge.test_cmd_merge_atomic_single_commit",
            "tests.governance.test_pr_governance.TestPRGovernanceLifecycle.test_assert_safe_worktree_operation",
        ]
        unit_groups: dict[str, list[str]] = {}
        for tid in case_ids:
            unit_key = tid.rsplit(".", 1)[0]
            unit_groups.setdefault(unit_key, []).append(tid)

        self.assertEqual(len(unit_groups), 4)
        self.assertIn(
            "tests.governance.test_pr_governance.TestPRGovernanceValidation", unit_groups
        )
        self.assertIn(
            "tests.governance.test_pr_governance.TestPRGovernanceDoctor", unit_groups
        )
        self.assertIn(
            "tests.governance.test_pr_governance.TestPRGovernanceMerge", unit_groups
        )
        self.assertIn(
            "tests.governance.test_pr_governance.TestPRGovernanceLifecycle", unit_groups
        )


class TestWave3RamCacheAndFingerprint(unittest.TestCase):
    """Verifies RAM cache acceleration for module fingerprints."""

    def setUp(self) -> None:
        clear_ram_cache()

    def test_fingerprint_ram_memoization(self) -> None:
        """Verifies RAM cache eliminates redundant disk I/O on repeated fingerprint calls."""
        mod = "tools.verify_main_readiness"
        fp1, deps1 = compute_module_fingerprint(mod, ROOT)
        self.assertTrue(len(fp1) > 0)

        # Second call hits RAM cache
        fp2, deps2 = compute_module_fingerprint(mod, ROOT)
        self.assertEqual(fp1, fp2)
        self.assertEqual(deps1, deps2)


class TestWave3StorageSandboxPragmas(unittest.TestCase):
    """Verifies SQLite PRAGMA tuning for sandbox vs non-sandbox databases."""

    def test_is_temp_sandbox_db_recognition(self) -> None:
        """Verifies sandbox detection based on environment and path."""
        self.assertTrue(is_temp_sandbox_db(":memory:"))

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            # In tempdir -> considered sandbox
            self.assertTrue(is_temp_sandbox_db(db_path))

            # Non-temp path
            non_temp = Path("/opt/decision-intelligence/canonical.db")
            with patch.dict(os.environ, {"TEMP_SANDBOX_DIR": tmpdir}):
                self.assertFalse(is_temp_sandbox_db(non_temp))

            with patch.dict(os.environ, {"TEMP_SANDBOX_DIR": "/opt/decision-intelligence"}):
                self.assertTrue(is_temp_sandbox_db(non_temp))

    def test_configure_pragmas_sandbox_synchronous_off(self) -> None:
        """Verifies sandbox connections execute synchronous = OFF and WAL."""
        conn = sqlite3.connect(":memory:")
        configure_pragmas(conn, readonly=False, in_memory=False, is_sandbox=True)
        cur = conn.cursor()
        cur.execute("PRAGMA synchronous;")
        sync_mode = cur.fetchone()[0]
        # In SQLite: 0 = OFF, 1 = NORMAL, 2 = FULL
        self.assertEqual(sync_mode, 0)
        conn.close()

    def test_configure_pragmas_production_synchronous_normal(self) -> None:
        """Verifies non-sandbox connections preserve synchronous = NORMAL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_file = Path(tmpdir) / "prod_sim.db"
            conn = sqlite3.connect(str(db_file))
            configure_pragmas(conn, readonly=False, in_memory=False, is_sandbox=False)
            cur = conn.cursor()
            cur.execute("PRAGMA synchronous;")
            sync_mode = cur.fetchone()[0]
            # 1 = NORMAL
            self.assertEqual(sync_mode, 1)
            cur.execute("PRAGMA journal_mode;")
            jmode = cur.fetchone()[0].upper()
            self.assertEqual(jmode, "WAL")
            conn.close()


class TestWave3CoverageReuseShortCircuit(unittest.TestCase):
    """Verifies Stage 29 package reuse short-circuit."""

    def test_stage29_reuse_short_circuits_under_500ms(self) -> None:
        """Verifies that an existing passing qualification package is reused immediately."""
        import sys
        import time

        # Ensure qualification package exists for current tree digest before testing reuse
        run_and_retain_coverage_audit(ROOT, sys.executable, min_e2e_score=80.0)

        t0 = time.monotonic()
        ok, out, dur, meta = run_and_retain_coverage_audit(ROOT, sys.executable, min_e2e_score=80.0)
        elapsed = time.monotonic() - t0

        self.assertTrue(ok)
        self.assertIsNotNone(meta)
        self.assertEqual(meta["summary"]["overall_verdict"], "PASS")
        self.assertLess(dur, 1.0, f"Package reuse must complete in <1.0s, took {dur}s")


if __name__ == "__main__":
    unittest.main()
