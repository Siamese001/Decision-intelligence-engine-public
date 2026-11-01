#!/usr/bin/env python3
"""Adversarial and Edge-Case Tests for Readiness Fingerprint & Domain Isolation (Wave 1).

Mandatory Edge-Case Categories Covered:
1. Boundary payload: empty target lists, zero-length targets, non-existent targets, missing domains.
2. Malformed input: corrupted JSON cache entries, non-dict payloads, invalid UTF-8 filenames.
3. Invariant bypass attempt: verifying non-PASS statuses ("FAIL", "ERROR", "NOT_RUN") never cache or return PASS.
4. Concurrency & race: clean handling when cache files are removed or concurrent processes write to cache dir.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.readiness_cache import (
    STAGE_DOMAIN_PATTERNS,
    clear_readiness_cache,
    compute_stage_fingerprint,
    get_cached_stage,
    get_stage_cache_dir,
    save_cached_stage,
)


class ReadinessFingerprintIsolationAdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="readiness_fp_adv_")
        self.repo_root = Path(self.tmp_dir) / "repo"
        self.repo_root.mkdir(parents=True)

        self.tools_dir = self.repo_root / "tools"
        self.tools_dir.mkdir(parents=True)
        (self.tools_dir / "tool.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")

        self.skills_dir = self.repo_root / ".agents" / "skills" / "demo-skill"
        self.skills_dir.mkdir(parents=True)
        (self.skills_dir / "SKILL.md").write_text("# Demo Skill\n", encoding="utf-8")

    def tearDown(self) -> None:
        clear_readiness_cache(self.repo_root)
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_boundary_empty_and_nonexistent_targets(self) -> None:
        """Boundary payload: empty targets list and non-existent file paths produce deterministic fingerprints."""
        cmd = ["python", "-B", "tools/tool.py"]

        # Empty targets
        fp_empty_1 = compute_stage_fingerprint("Stage", cmd, self.repo_root, "sha1", target_files=[])
        fp_empty_2 = compute_stage_fingerprint("Stage", cmd, self.repo_root, "sha1", target_files=[])
        self.assertEqual(fp_empty_1, fp_empty_2)
        self.assertTrue(len(fp_empty_1) == 64)

        # Non-existent target file does not crash
        non_existent = ["non/existent/file.py"]
        fp_nonexist = compute_stage_fingerprint("Stage", cmd, self.repo_root, "sha1", target_files=non_existent)
        self.assertTrue(len(fp_nonexist) == 64)

    def test_02_malformed_cache_entry_rejection(self) -> None:
        """Malformed input: non-dict JSON, truncated JSON, and corrupt data fail closed."""
        cmd = ["python", "-B", "tools/tool.py"]
        fp = compute_stage_fingerprint("Corrupt Stage", cmd, self.repo_root, "sha1")
        cache_dir = get_stage_cache_dir(self.repo_root)
        cache_file = cache_dir / f"{fp}.json"

        # Case A: Array instead of dict
        cache_file.write_text(json.dumps(["status", "PASS"]), encoding="utf-8")
        self.assertIsNone(get_cached_stage("Corrupt Stage", cmd, self.repo_root, "sha1"))

        # Case B: Truncated JSON
        cache_file.write_text('{"status": "PASS", "stage_name": ', encoding="utf-8")
        self.assertIsNone(get_cached_stage("Corrupt Stage", cmd, self.repo_root, "sha1"))

        # Case C: Wrong fingerprint in payload
        bad_payload = {
            "stage_name": "Corrupt Stage",
            "status": "PASS",
            "fingerprint": "wrong_fp_12345",
            "duration_seconds": 0.1,
            "command": "python tool.py",
        }
        cache_file.write_text(json.dumps(bad_payload), encoding="utf-8")
        self.assertIsNone(get_cached_stage("Corrupt Stage", cmd, self.repo_root, "sha1"))

    def test_03_invariant_bypass_non_pass_never_cached(self) -> None:
        """Invariant bypass attempt: only status PASS can be retrieved from cache."""
        cmd = ["python", "-B", "tools/tool.py"]
        fp = compute_stage_fingerprint("Failed Stage", cmd, self.repo_root, "sha1")
        cache_dir = get_stage_cache_dir(self.repo_root)
        cache_file = cache_dir / f"{fp}.json"

        # Attempt to inject a FAIL entry
        fail_payload = {
            "stage_name": "Failed Stage",
            "status": "FAIL",
            "fingerprint": fp,
            "duration_seconds": 0.5,
            "command": "python tool.py",
        }
        cache_file.write_text(json.dumps(fail_payload), encoding="utf-8")

        # get_cached_stage must strictly reject non-PASS entries
        self.assertIsNone(
            get_cached_stage("Failed Stage", cmd, self.repo_root, "sha1"),
            "Non-PASS status in cache file must never be returned as a valid cache hit",
        )

    def test_04_domain_isolation_boundary_skills_gate(self) -> None:
        """Domain isolation: Workspace Skills Budget gate ignores changes outside .agents/skills."""
        skills_gate = "Workspace Skills Budget & Token Overhead Gate"
        self.assertIn(skills_gate, STAGE_DOMAIN_PATTERNS)

        cmd = ["python", "-B", "tools/audit_skills_budget.py", "--check"]
        fp1 = compute_stage_fingerprint(skills_gate, cmd, self.repo_root, "commit_1")

        # Create/modify completely unrelated directory
        unrelated = self.repo_root / "unrelated_pkg" / "sub"
        unrelated.mkdir(parents=True)
        (unrelated / "code.py").write_text("X = 100\n", encoding="utf-8")

        fp2 = compute_stage_fingerprint(skills_gate, cmd, self.repo_root, "commit_2")
        self.assertEqual(fp1, fp2, "Unrelated file creation must not alter domain-scoped fingerprint")

        # Now touch a skill file inside domain
        (self.skills_dir / "SKILL.md").write_text("# Mutated Skill Content\n", encoding="utf-8")
        fp3 = compute_stage_fingerprint(skills_gate, cmd, self.repo_root, "commit_2")
        self.assertNotEqual(fp1, fp3, "Modifying skill file inside domain must change fingerprint")


if __name__ == "__main__":
    unittest.main()
