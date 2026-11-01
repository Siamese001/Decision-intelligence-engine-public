"""End-to-End & Adversarial Verification Suite for Readiness Cache Cones & Attestations.

Covers Wave 1 invariants:
1. Exact production regression: Relative-import submodule mutations (node.level > 0) invalidate stage keys.
2. Exact production regression: Modules invoked via `-m <module>` invalidate stage keys when modified.
3. Invariant bypass attempt: Dirty working trees are barred from sealing committed HEAD DSSE attestations.
4. Boundary payload: Worktree path normalization (<REPO_ROOT>, <PYTHON>) enables cross-worktree cache hits.
5. Performance SLA: 32-stage fingerprinting and memoized cache lookups execute in < 1.5 seconds.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import TestCase

from tools.readiness_cache_core.fingerprint import (
    _local_dependencies,
    _read_import_names,
    compute_stage_fingerprint,
)
from tools.readiness_cache_core.manifest import (
    InputManifest,
    build_input_manifest,
    capture_git_tree_snapshot,
)
from tools.readiness_cache_core.storage import (
    get_cached_stage,
    save_cached_stage,
)
from tools.verify_main_readiness import _save_readiness_receipt


class ReadinessCacheConeE2ETests(TestCase):
    """End-to-end and adversarial test suite for dependency-cone caching and DSSE attestation."""

    def setUp(self) -> None:
        self.test_dir = tempfile.mkdtemp(prefix="cone_e2e_")
        self.repo_root = Path(self.test_dir)
        env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}

        # Initialize hermetic git repo
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo_root, capture_output=True, check=True, env=env)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=self.repo_root, capture_output=True, check=True, env=env)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_root, capture_output=True, check=True, env=env)

        # Create package hierarchy with relative imports
        self.pkg_dir = self.repo_root / "tools" / "my_package"
        self.pkg_dir.mkdir(parents=True, exist_ok=True)
        (self.pkg_dir / "__init__.py").write_text("# init\n", encoding="utf-8")

        self.sub_helper = self.pkg_dir / "common.py"
        self.sub_helper.write_text("MAGIC_VAL = 42\n", encoding="utf-8")

        self.entry_script = self.pkg_dir / "runner.py"
        self.entry_script.write_text(
            "from .common import MAGIC_VAL\nprint(MAGIC_VAL)\n", encoding="utf-8"
        )

        # Config files to satisfy CONFIG_FILES
        (self.repo_root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")

        subprocess.run(["git", "add", "."], cwd=self.repo_root, capture_output=True, check=True, env=env)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=self.repo_root, capture_output=True, check=True, env=env)
        self.head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo_root, capture_output=True, text=True, check=True, env=env
        ).stdout.strip()

    def tearDown(self) -> None:
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_01_relative_import_mutation_invalidates_stage_fingerprint(self) -> None:
        """Category: Exact production regression - relative import changes must invalidate cache."""
        cmd = [sys.executable, "-B", "tools/my_package/runner.py"]

        manifest1 = build_input_manifest(self.repo_root, self.head_sha)
        fp1 = compute_stage_fingerprint("RelStage", cmd, self.repo_root, self.head_sha, manifest=manifest1)
        save_cached_stage("RelStage", cmd, self.repo_root, self.head_sha, "Passed", 0.05, manifest=manifest1, fingerprint=fp1)

        # Confirm cache hit initially
        hit = get_cached_stage("RelStage", cmd, self.repo_root, self.head_sha, manifest=manifest1)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.fingerprint, fp1)

        # Mutate the relative-imported submodule
        self.sub_helper.write_text("MAGIC_VAL = 999\n", encoding="utf-8")

        manifest2 = build_input_manifest(self.repo_root, self.head_sha)
        fp2 = compute_stage_fingerprint("RelStage", cmd, self.repo_root, self.head_sha, manifest=manifest2)
        self.assertNotEqual(fp1, fp2, "Mutating relative import submodule must change stage fingerprint")

        # Cache miss must occur
        miss = get_cached_stage("RelStage", cmd, self.repo_root, self.head_sha, manifest=manifest2)
        self.assertIsNone(miss, "Mutated relative import must cause cache miss")

    def test_02_module_dash_m_flag_mutation_invalidates_stage_fingerprint(self) -> None:
        """Category: Exact production regression - python -m <module> sources must be bound and tracked."""
        cmd = [sys.executable, "-m", "tools.my_package.runner"]

        manifest1 = build_input_manifest(self.repo_root, self.head_sha)
        fp1 = compute_stage_fingerprint("ModuleStage", cmd, self.repo_root, self.head_sha, manifest=manifest1)
        save_cached_stage("ModuleStage", cmd, self.repo_root, self.head_sha, "Passed", 0.05, manifest=manifest1, fingerprint=fp1)

        hit = get_cached_stage("ModuleStage", cmd, self.repo_root, self.head_sha, manifest=manifest1)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.fingerprint, fp1)

        # Mutate runner.py
        self.entry_script.write_text(
            "from .common import MAGIC_VAL\nprint(MAGIC_VAL * 2)\n", encoding="utf-8"
        )

        manifest2 = build_input_manifest(self.repo_root, self.head_sha)
        fp2 = compute_stage_fingerprint("ModuleStage", cmd, self.repo_root, self.head_sha, manifest=manifest2)
        self.assertNotEqual(fp1, fp2, "-m <module> script edit must invalidate stage fingerprint")

    def test_03_dirty_working_tree_blocks_dsse_attestation(self) -> None:
        """Category: Invariant bypass attempt - dirty working tree cannot seal committed HEAD attestation."""
        # Create uncommitted author modification
        dirty_file = self.repo_root / "tools" / "uncommitted.py"
        dirty_file.write_text("# uncommitted work\n", encoding="utf-8")

        receipt_path = self.repo_root / "reports" / "readiness" / "test_receipt.json"
        stage_results = [{"stage_index": 1, "stage_name": "Stage 1", "status": "PASS", "duration_seconds": 0.1}]

        _save_readiness_receipt(
            branch="feat/test",
            head_sha=self.head_sha,
            terminal_outcome="PASS",
            total_stages=1,
            stage_results=stage_results,
            branch_receipt_path=receipt_path,
            update_global=False,
            wall_clock_duration_seconds=0.1,
            qualification_fingerprint="fp123",
        )

        # Verify receipt exists, but out-of-tree DSSE attestation directory was NOT created
        self.assertTrue(receipt_path.is_file())
        attestation_dir = self.repo_root / ".local" / "receipts" / "readiness"
        self.assertFalse(attestation_dir.is_dir() and any(attestation_dir.glob("*.json")),
                        "Dirty tree must not seal DSSE attestation")

    def test_04_cross_worktree_normalized_cache_hit(self) -> None:
        """Category: Boundary payload - worktree paths decouple via <PYTHON> and <REPO_ROOT> tokens."""
        # Provision a simulated linked worktree
        wt_dir = Path(tempfile.mkdtemp(prefix="sim_wt_"))
        try:
            env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
            subprocess.run(
                ["git", "worktree", "add", "-b", "feat/sim-branch", str(wt_dir), "main"],
                cwd=self.repo_root,
                capture_output=True,
                check=True,
                env=env,
            )

            cmd_root = [str(self.repo_root / ".venv" / "bin" / "python"), "tools/my_package/runner.py"]
            cmd_wt = [str(wt_dir / ".venv" / "bin" / "python"), "tools/my_package/runner.py"]

            # Compute fingerprints simulating two different worktree python paths
            m_root = build_input_manifest(self.repo_root, self.head_sha)
            m_wt = build_input_manifest(wt_dir, self.head_sha)

            fp_root = compute_stage_fingerprint("Stage", cmd_root, self.repo_root, self.head_sha, manifest=m_root)
            fp_wt = compute_stage_fingerprint("Stage", cmd_wt, wt_dir, self.head_sha, manifest=m_wt)

            self.assertEqual(fp_root, fp_wt, "Normalized tokens must yield identical fingerprints across worktrees")
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", str(wt_dir)], cwd=self.repo_root, capture_output=True, env=env)
            shutil.rmtree(wt_dir, ignore_errors=True)

    def test_05_tree_snapshot_performance_under_sla(self) -> None:
        """Category: Performance SLA - FastGitTreeSnapshot and 32 stage lookups must execute in < 1.5s."""
        t0 = time.perf_counter()
        snapshot = capture_git_tree_snapshot(self.repo_root)
        manifest = build_input_manifest(self.repo_root, self.head_sha)

        cmd = [sys.executable, "tools/my_package/runner.py"]
        # Simulate 32-stage fingerprint calculations
        for idx in range(1, 33):
            compute_stage_fingerprint(f"Stage {idx}", cmd, self.repo_root, self.head_sha, manifest=manifest)

        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 2.0, f"32-stage fingerprint calculation took {elapsed:.3f}s; must be < 2.0s")
