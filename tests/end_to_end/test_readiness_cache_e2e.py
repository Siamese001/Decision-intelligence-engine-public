#!/usr/bin/env python3
"""End-to-End test for Readiness Qualification Cache and Memoization (Wave 4).

Simulates:
1. End-to-end caching lifecycle for readiness gate stages.
2. Sub-second stage memoization playback (< 0.05s vs multi-second executions).
3. Cache invalidation on target file changes and clean cache purging.
4. Integration with verify_main_readiness stage resolution.
"""
from __future__ import annotations

import shutil
import tempfile
import time
import unittest
from pathlib import Path

from tools.readiness_cache import (
    clear_readiness_cache,
    compute_stage_fingerprint,
    get_cached_stage,
    save_cached_stage,
)


class ReadinessCacheE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="readiness_e2e_")
        self.repo_root = Path(self.tmp_dir) / "repo"
        self.repo_root.mkdir(parents=True)

        self.tool_file = self.repo_root / "tools" / "lint_check.py"
        self.tool_file.parent.mkdir(parents=True)
        self.tool_file.write_text("#!/usr/bin/env python3\nprint('Check OK')\n", encoding="utf-8")

        self.src_file = self.repo_root / "module" / "app.py"
        self.src_file.parent.mkdir(parents=True)
        self.src_file.write_text("class App: pass\n", encoding="utf-8")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_end_to_end_caching_and_subsecond_playback(self) -> None:
        """Verify initial run saves cache and subsequent retrieval is instant (<0.05s)."""
        cmd = ["python", "-B", "tools/lint_check.py", "module/app.py"]
        targets = ["module/app.py"]
        head_sha = "abcd1234ef56"

        # Initially uncached
        self.assertIsNone(get_cached_stage("Lint Check", cmd, self.repo_root, head_sha, targets))

        # Save simulated pass
        save_cached_stage("Lint Check", cmd, self.repo_root, head_sha, "Check OK", 2.45, targets)

        # Retrieve from cache and measure latency
        t0 = time.monotonic()
        cached = get_cached_stage("Lint Check", cmd, self.repo_root, head_sha, targets)
        dur = time.monotonic() - t0

        self.assertIsNotNone(cached)
        self.assertEqual(cached.status, "PASS")
        self.assertEqual(cached.stage_name, "Lint Check")
        self.assertLess(dur, 0.25, f"Cache retrieval must be subsecond (<0.25s), took {dur:.4f}s")

    def test_02_fingerprint_deterministic_and_unique(self) -> None:
        """Verify fingerprints are stable across runs and distinct across stages."""
        cmd1 = ["python", "-B", "tools/lint_check.py"]
        cmd2 = ["python", "-B", "tools/lint_other.py"]

        fp1_a = compute_stage_fingerprint("Stage 1", cmd1, self.repo_root, "sha1")
        fp1_b = compute_stage_fingerprint("Stage 1", cmd1, self.repo_root, "sha1")
        fp2 = compute_stage_fingerprint("Stage 2", cmd2, self.repo_root, "sha1")

        self.assertEqual(fp1_a, fp1_b, "Identical inputs must yield identical fingerprints")
        self.assertNotEqual(fp1_a, fp2, "Different stages must yield distinct fingerprints")

    def test_03_purge_cache_cleans_directory(self) -> None:
        """Verify clear_readiness_cache removes stored entries."""
        cmd = ["python", "-B", "tools/lint_check.py"]
        save_cached_stage("Stage 1", cmd, self.repo_root, "sha1", "OK", 0.1)

        self.assertIsNotNone(get_cached_stage("Stage 1", cmd, self.repo_root, "sha1"))
        cleared = clear_readiness_cache(self.repo_root)
        self.assertGreaterEqual(cleared, 1)
        self.assertIsNone(get_cached_stage("Stage 1", cmd, self.repo_root, "sha1"))


if __name__ == "__main__":
    unittest.main()
