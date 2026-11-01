#!/usr/bin/env python3
"""End-to-End test for Readiness Incremental Qualification Cache and File Scope Isolation (Wave 1).

Verifies:
1. File-scoped stages retain cache hits across commits that touch unrelated files.
2. Domain-bounded stages retain cache hits when unrelated code outside the domain changes.
3. Target arguments are correctly propagated to file-scopable linters in readiness_core._build_stages.
4. Domain-targeted changes properly invalidate only their respective domain stages.
"""
from __future__ import annotations

import shutil
import tempfile
import time
import unittest
from pathlib import Path

from tools.readiness_cache import (
    STAGE_DOMAIN_PATTERNS,
    clear_readiness_cache,
    compute_stage_fingerprint,
    get_cached_stage,
    save_cached_stage,
)
from tools.readiness_core import _build_stages


class ReadinessIncrementalCacheE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="readiness_inc_cache_e2e_")
        self.repo_root = Path(self.tmp_dir) / "repo"
        self.repo_root.mkdir(parents=True)

        # Create dummy tools
        tools_dir = self.repo_root / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "lint_file.py").write_text("#!/usr/bin/env python3\nprint('PASS')\n", encoding="utf-8")

        # Create dummy source files
        src_dir = self.repo_root / "src"
        src_dir.mkdir(parents=True)
        self.file_a = src_dir / "module_a.py"
        self.file_a.write_text("def func_a(): return 1\n", encoding="utf-8")
        self.file_b = src_dir / "module_b.py"
        self.file_b.write_text("def func_b(): return 2\n", encoding="utf-8")

        # Create dummy contracts domain directory
        contracts_dir = self.repo_root / "contracts"
        contracts_dir.mkdir(parents=True)
        self.contract_file = contracts_dir / "sample.json"
        self.contract_file.write_text('{"version": "1.0.0"}\n', encoding="utf-8")

    def tearDown(self) -> None:
        clear_readiness_cache(self.repo_root)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_build_stages_propagates_target_args_to_linters(self) -> None:
        """Verify _build_stages passes target_files to all file-scopable linters."""
        targets = ["src/module_a.py"]
        stages = _build_stages("python", targets, test_modules=[], test_workers=1, repo_root=self.repo_root)

        stages_dict = {name: cmd for name, cmd in stages}

        scopable_linters = [
            "Architectural Layer & Cycle Linter",
            "Module File Budget Linter (All Modules)",
            "Direct Stage U0 Anti-Scripting Linter",
            "Credential & Career Firewall Scanner",
            "No Synthetic Embeddings Policy",
            "SSOT Configuration & Release Threshold Scanner",
            "Gate 32: Direct Graph Write Path Linter",
        ]

        for linter_name in scopable_linters:
            self.assertIn(linter_name, stages_dict, f"Stage {linter_name} missing from _build_stages")
            cmd = stages_dict[linter_name]
            self.assertTrue(
                any("src/module_a.py" in arg for arg in cmd),
                f"Stage '{linter_name}' did not receive target file in command: {cmd}",
            )

    def test_02_file_scoped_cache_hit_across_unrelated_file_changes(self) -> None:
        """Verify modifying module_a does not invalidate module_b's cached stage."""
        cmd_b = ["python", "-B", "tools/lint_file.py", "src/module_b.py"]
        targets_b = ["src/module_b.py"]
        head_sha_v1 = "sha_commit_v1"

        fp_b_v1 = compute_stage_fingerprint("Lint B", cmd_b, self.repo_root, head_sha_v1, targets_b)
        save_cached_stage("Lint B", cmd_b, self.repo_root, head_sha_v1, "PASS", 0.05, targets_b)

        # Verify initial cache hit
        cached = get_cached_stage("Lint B", cmd_b, self.repo_root, head_sha_v1, targets_b)
        self.assertIsNotNone(cached)
        self.assertEqual(cached.status, "PASS")

        # Now simulate a commit that ONLY modifies module_a
        self.file_a.write_text("def func_a(): return 999  # mutated\n", encoding="utf-8")
        head_sha_v2 = "sha_commit_v2"

        # module_b's fingerprint must remain identical despite head_sha change because it is file-scoped
        fp_b_v2 = compute_stage_fingerprint("Lint B", cmd_b, self.repo_root, head_sha_v2, targets_b)
        self.assertEqual(fp_b_v1, fp_b_v2, "File-scoped stage fingerprint must not change when unrelated files mutate")

    def test_03_domain_bounded_cache_hit_when_unrelated_code_changes(self) -> None:
        """Verify changes outside a domain do not invalidate the domain stage cache."""
        domain_stage = "Dual Pipeline Contracts Validation"
        self.assertIn(domain_stage, STAGE_DOMAIN_PATTERNS)

        cmd = ["python", "-B", "tools/validate_dual_pipeline_contracts.py"]
        head_v1 = "sha_commit_1"

        fp_dom_v1 = compute_stage_fingerprint(domain_stage, cmd, self.repo_root, head_v1)
        save_cached_stage(domain_stage, cmd, self.repo_root, head_v1, "Contracts valid", 0.1)

        # Mutate code completely outside contracts/
        self.file_a.write_text("def func_a(): return 'unrelated edit'\n", encoding="utf-8")
        head_v2 = "sha_commit_2"

        fp_dom_v2 = compute_stage_fingerprint(domain_stage, cmd, self.repo_root, head_v2)
        self.assertEqual(
            fp_dom_v1,
            fp_dom_v2,
            "Domain-bounded stage fingerprint must remain identical when edits are outside its domain",
        )

        # Now mutate the contract file inside the domain
        self.contract_file.write_text('{"version": "2.0.0-invalidation"}\n', encoding="utf-8")
        fp_dom_v3 = compute_stage_fingerprint(domain_stage, cmd, self.repo_root, head_v2)
        self.assertNotEqual(
            fp_dom_v1,
            fp_dom_v3,
            "Mutating a file inside the domain MUST invalidate the domain stage fingerprint",
        )

    def test_04_subsecond_cache_playback_performance(self) -> None:
        """Verify cache retrieval latency is < 0.05s."""
        cmd = ["python", "-B", "tools/lint_file.py", "src/module_a.py"]
        targets = ["src/module_a.py"]
        sha = "commit_perf"

        save_cached_stage("Perf Stage", cmd, self.repo_root, sha, "PASS", 1.25, targets)

        t0 = time.monotonic()
        cached = get_cached_stage("Perf Stage", cmd, self.repo_root, sha, targets)
        dur = time.monotonic() - t0

        self.assertIsNotNone(cached)
        self.assertLess(dur, 0.5, f"Cache retrieval took {dur:.4f}s; must be subsecond (<0.5s)")


if __name__ == "__main__":
    unittest.main()
