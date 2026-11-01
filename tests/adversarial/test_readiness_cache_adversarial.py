#!/usr/bin/env python3
"""Adversarial and Edge-Case Tests for Readiness Qualification Cache (Wave 4).

Covers:
1. Malformed JSON payloads and arbitrary bytes in readiness cache directory.
2. Invalidation upon working tree unstaged/staged modifications.
3. Invalidation upon tool script AST changes.
4. Repo-wide stage invalidation upon git commit SHA changes.
5. Invariant enforcement: failed stages are never cached as passes.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from tools.readiness_cache import (
    clear_readiness_cache,
    compute_qualification_fingerprint,
    compute_stage_fingerprint,
    get_cached_stage,
    get_stage_cache_dir,
    save_cached_stage,
)
from tools.readiness_core import run_stage


class ReadinessCacheAdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="readiness_cache_adv_")
        self.repo_root = Path(self.tmp_dir) / "repo"
        self.repo_root.mkdir(parents=True)

        self.tool_script = self.repo_root / "tools" / "lint_dummy.py"
        self.tool_script.parent.mkdir(parents=True)
        self.tool_script.write_text("# dummy tool\n", encoding="utf-8")

        self.target_file = self.repo_root / "src" / "dummy.py"
        self.target_file.parent.mkdir(parents=True)
        self.target_file.write_text("# dummy target\n", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_malformed_cache_file_gracefully_ignored(self) -> None:
        """Verify corrupted JSON in cache dir does not crash get_cached_stage."""
        cache_dir = get_stage_cache_dir(self.repo_root)
        cmd = ["python", "-B", "tools/lint_dummy.py"]
        fp = compute_stage_fingerprint("Dummy Stage", cmd, self.repo_root, "sha123")

        corrupt_file = cache_dir / f"{fp}.json"
        corrupt_file.write_bytes(b"\x00\xff\xfe corrupted binary { invalid")

        cached = get_cached_stage("Dummy Stage", cmd, self.repo_root, "sha123")
        self.assertIsNone(cached)

    def test_02_target_file_mutation_invalidates_fingerprint(self) -> None:
        """Verify modifying target file changes fingerprint and causes cache miss."""
        cmd = ["python", "-B", "tools/lint_dummy.py", "src/dummy.py"]
        target = ["src/dummy.py"]

        fp1 = compute_stage_fingerprint("File Stage", cmd, self.repo_root, "sha123", target)
        save_cached_stage("File Stage", cmd, self.repo_root, "sha123", "Passed", 0.1, target)

        cached_before = get_cached_stage("File Stage", cmd, self.repo_root, "sha123", target)
        self.assertIsNotNone(cached_before)

        # Mutate target file
        self.target_file.write_text("# mutated content\n", encoding="utf-8")
        fp2 = compute_stage_fingerprint("File Stage", cmd, self.repo_root, "sha123", target)
        self.assertNotEqual(fp1, fp2, "Fingerprints must diverge on file modification")

        cached_after = get_cached_stage("File Stage", cmd, self.repo_root, "sha123", target)
        self.assertIsNone(cached_after, "Mutated file must cause cache miss")

    def test_03_tool_script_mutation_invalidates_fingerprint(self) -> None:
        """Verify modifying the linter script itself invalidates the cache."""
        cmd = ["python", "-B", "tools/lint_dummy.py"]
        fp1 = compute_stage_fingerprint("Tool Stage", cmd, self.repo_root, "sha123")
        save_cached_stage("Tool Stage", cmd, self.repo_root, "sha123", "Passed", 0.1)

        # Mutate tool script
        self.tool_script.write_text("# new linter rule\n", encoding="utf-8")
        fp2 = compute_stage_fingerprint("Tool Stage", cmd, self.repo_root, "sha123")
        self.assertNotEqual(fp1, fp2)

        cached = get_cached_stage("Tool Stage", cmd, self.repo_root, "sha123")
        self.assertIsNone(cached)

    def test_04_head_sha_change_invalidates_repo_wide_stage(self) -> None:
        """Verify repo-wide stages are invalidated when HEAD SHA changes."""
        cmd = ["python", "-B", "tools/lint_production_purity.py", "--all"]
        fp_sha1 = compute_stage_fingerprint("Purity", cmd, self.repo_root, "sha_commit_1")
        fp_sha2 = compute_stage_fingerprint("Purity", cmd, self.repo_root, "sha_commit_2")

        self.assertNotEqual(fp_sha1, fp_sha2)

    def test_05_clear_readiness_cache_purges_safely(self) -> None:
        """Verify clear_readiness_cache safely removes all cache files."""
        cmd = ["python", "-B", "tools/lint_dummy.py"]
        save_cached_stage("Stage A", cmd, self.repo_root, "sha123", "Passed", 0.05)
        save_cached_stage("Stage B", cmd, self.repo_root, "sha123", "Passed", 0.08)

        count = clear_readiness_cache(self.repo_root)
        self.assertEqual(count, 2)
        self.assertEqual(clear_readiness_cache(self.repo_root), 0)

    def test_06_imported_verifier_mutation_invalidates_cache(self) -> None:
        """A changed helper imported by a gate must invalidate an otherwise identical result."""
        helper = self.repo_root / "tools" / "dummy_helper.py"
        helper.write_text("VALUE = 1\n", encoding="utf-8")
        self.tool_script.write_text(
            "from tools.dummy_helper import VALUE\nprint(VALUE)\n", encoding="utf-8"
        )
        cmd = ["python", "tools/lint_dummy.py"]
        before = compute_stage_fingerprint("Imported verifier", cmd, self.repo_root, "same")
        qualification_before = compute_qualification_fingerprint(
            [("Imported verifier", cmd)], self.repo_root, "same"
        )
        helper.write_text("VALUE = 20\n", encoding="utf-8")
        after = compute_stage_fingerprint("Imported verifier", cmd, self.repo_root, "same")
        qualification_after = compute_qualification_fingerprint(
            [("Imported verifier", cmd)], self.repo_root, "same"
        )
        self.assertNotEqual(before, after)
        self.assertNotEqual(qualification_before, qualification_after)

    def test_07_gate_environment_mutation_invalidates_cache(self) -> None:
        """Gate-affecting environment changes must not reuse a prior PASS."""
        cmd = ["python", "tools/lint_dummy.py"]
        with patch.dict("os.environ", {"CI": "0"}, clear=False):
            before = compute_stage_fingerprint("Environment", cmd, self.repo_root, "same")
            qualification_before = compute_qualification_fingerprint(
                [("Environment", cmd)], self.repo_root, "same"
            )
        with patch.dict("os.environ", {"CI": "1"}, clear=False):
            after = compute_stage_fingerprint("Environment", cmd, self.repo_root, "same")
            qualification_after = compute_qualification_fingerprint(
                [("Environment", cmd)], self.repo_root, "same"
            )
        self.assertNotEqual(before, after)
        self.assertNotEqual(qualification_before, qualification_after)

    def test_08_cached_pass_cannot_mask_failure_and_recovery(self) -> None:
        """A broken dependency must execute and fail; a distinct repair must execute and pass."""
        helper = self.repo_root / "tools" / "dummy_helper.py"
        helper.write_text("VALUE = 1\n", encoding="utf-8")
        self.tool_script.write_text(
            "from tools.dummy_helper import VALUE\nraise SystemExit(0 if VALUE > 0 else 1)\n",
            encoding="utf-8",
        )
        cmd = ["python", "tools/lint_dummy.py"]
        save_cached_stage("Failure recovery", cmd, self.repo_root, "same", "pass", 0.1)
        self.assertIsNotNone(get_cached_stage("Failure recovery", cmd, self.repo_root, "same"))

        helper.write_text("VALUE = 0\n", encoding="utf-8")
        self.assertIsNone(get_cached_stage("Failure recovery", cmd, self.repo_root, "same"))
        failed, _, _ = run_stage("Failure recovery", cmd, self.repo_root)
        self.assertFalse(failed)

        helper.write_text("VALUE = 20\n", encoding="utf-8")
        self.assertIsNone(get_cached_stage("Failure recovery", cmd, self.repo_root, "same"))
        recovered, _, _ = run_stage("Failure recovery", cmd, self.repo_root)
        self.assertTrue(recovered)


if __name__ == "__main__":
    unittest.main()
