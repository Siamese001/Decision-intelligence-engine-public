"""Adversarial and edge-case tests for Wave 3: Ephemeralization of Generated Reports.

Covers 5 distinct edge-case categories under AGENTS.md and e2e-and-edge-case-coverage.md:
1. Boundary: Missing files, empty 0-byte files, null values.
2. Malformed input: Truncated JSON, non-dict roots, wrong data types.
3. Concurrency & race: Multi-threaded concurrent writes to TestTimingDB.
4. Invariant bypass attempt: Staged modifications to baseline blocked on feature branch.
5. Exact regression / legacy format: Old-style schemas safely handled.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.file_budget_core.ratchet import load_ratchet_baseline
from tools.guard_burndown_baseline_freeze import check_baseline_freeze
from tools.post_merge_report_regenerator import ensure_timing_db
from tools.test_timing_db import TestTimingDB


class GeneratedReportsEdgeCasesTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_01_boundary_missing_and_zero_byte_reports(self):
        """Category: Boundary payload - 0-byte or non-existent files."""
        zero_file = self.tmp_path / "zero_timing.json"
        zero_file.touch()

        # Zero-byte file should not crash TestTimingDB
        db = TestTimingDB(zero_file)
        self.assertEqual(db.entry_count(), 0)
        self.assertEqual(db.get_duration("missing_test"), 0.0)

        # ensure_timing_db resets 0-byte file cleanly
        res = ensure_timing_db(self.tmp_path)
        self.assertIn(res["status"], ("INITIALIZED", "RESET_CORRUPT"))

        # Zero-byte baseline loading
        baseline_zero = self.tmp_path / "zero_baseline.json"
        baseline_zero.touch()
        data = load_ratchet_baseline(baseline_zero)
        self.assertEqual(data, {"modules": {}})

    def test_02_malformed_truncated_and_non_dict_json(self):
        """Category: Malformed input - truncated syntax, wrong types, non-dict roots."""
        malformed_file = self.tmp_path / "malformed.json"

        # Truncated JSON
        with open(malformed_file, "w", encoding="utf-8") as f:
            f.write('{"test_incomplete": 1.23, "test_b": ')

        db = TestTimingDB(malformed_file)
        self.assertEqual(db.entry_count(), 0)

        # Non-dict JSON root
        with open(malformed_file, "w", encoding="utf-8") as f:
            json.dump(["array", "of", "items"], f)

        db2 = TestTimingDB(malformed_file)
        self.assertEqual(db2.entry_count(), 0)

        # Invalid value types
        with open(malformed_file, "w", encoding="utf-8") as f:
            json.dump({"test_ok": 1.5, "test_bad": "not-a-number"}, f)

        db3 = TestTimingDB(malformed_file)
        # Should cleanly handle or drop invalid entries
        self.assertIn(db3.entry_count(), (0, 1))

    def test_03_concurrent_multithreaded_timing_db_writers(self):
        """Category: Concurrency & race - simultaneous writers to TestTimingDB."""
        shared_db_file = self.tmp_path / "concurrent_timing_db.json"
        errors = []

        def worker_write(worker_id: int):
            try:
                for i in range(10):
                    db = TestTimingDB(shared_db_file)
                    db.record(f"test_worker_{worker_id}_{i}", 0.05 * (i + 1))
                    db.save()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker_write, args=(w,)) for w in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Concurrent writes threw errors: {errors}")
        self.assertTrue(shared_db_file.is_file())

        # Verify output is valid readable JSON after concurrent writes
        with open(shared_db_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIsInstance(data, dict)
        self.assertGreater(len(data), 0)

    def test_04_invariant_bypass_baseline_freeze_guard(self):
        """Category: Invariant bypass attempt - modifying baseline on branch without merge token."""
        # Simulated test git repo
        repo_dir = self.tmp_path / "repo"
        repo_dir.mkdir()
        subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)

        baseline_file = repo_dir / "reports" / "file_budget_burndown_baseline.json"
        baseline_file.parent.mkdir(parents=True)
        baseline_file.write_text(json.dumps({"modules": {}}), encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_dir, check=True)

        # Create and switch to feature branch
        subprocess.run(["git", "checkout", "-b", "feat/unauthorized-baseline"], cwd=repo_dir, check=True)

        # Modify baseline on branch
        baseline_file.write_text(json.dumps({"modules": {"unauthorized.py": 500}}), encoding="utf-8")
        subprocess.run(["git", "add", str(baseline_file)], cwd=repo_dir, check=True)

        # Without GOVERNED_PR_MERGE=1, check_baseline_freeze must FAIL
        old_env = os.environ.get("GOVERNED_PR_MERGE")
        try:
            if "GOVERNED_PR_MERGE" in os.environ:
                del os.environ["GOVERNED_PR_MERGE"]

            allowed, msg = check_baseline_freeze(repo_root=repo_dir, staged=True)
            self.assertFalse(allowed, "Modifying baseline on feature branch must be blocked.")
            self.assertIn("COMMIT REJECTED", msg)

            # With GOVERNED_PR_MERGE=1, check_baseline_freeze must PASS
            os.environ["GOVERNED_PR_MERGE"] = "1"
            allowed_with_env, msg_ok = check_baseline_freeze(repo_root=repo_dir, staged=True)
            self.assertTrue(allowed_with_env, "Merge queue with GOVERNED_PR_MERGE=1 must be authorized.")
        finally:
            if old_env is not None:
                os.environ["GOVERNED_PR_MERGE"] = old_env
            elif "GOVERNED_PR_MERGE" in os.environ:
                del os.environ["GOVERNED_PR_MERGE"]

    def test_05_legacy_format_schema_coercion(self):
        """Category: Exact production regression - legacy timing and baseline structures."""
        legacy_file = self.tmp_path / "legacy_timing.json"
        # Legacy formats might have strings or integers instead of floats
        with open(legacy_file, "w", encoding="utf-8") as f:
            json.dump({
                "test_integer": 2,
                "test_float_str": "3.14",
                "test_negative": -0.5,
            }, f)

        db = TestTimingDB(legacy_file)
        self.assertEqual(db.get_duration("test_integer"), 2.0)
        self.assertEqual(db.get_duration("test_float_str"), 3.14)

        # Recording new value persists cleanly
        db.record("test_integer", 2.5)
        db.save()

        with open(legacy_file, "r", encoding="utf-8") as f:
            saved_data = json.load(f)
        self.assertIsInstance(saved_data["test_integer"], float)


if __name__ == "__main__":
    unittest.main()
