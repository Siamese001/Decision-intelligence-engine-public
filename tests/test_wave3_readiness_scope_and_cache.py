"""Wave 3 tests verifying readiness scope collector, stage domain patterns, and file-scoped gates."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from tools.coverage_report_reuse import compute_coverage_domain_digest
from tools.readiness_cache_core.fingerprint import STAGE_DOMAIN_PATTERNS, compute_stage_fingerprint
from tools.readiness_core import _build_stages
from tools.readiness_scope_collector import FILE_SCOPED_PYTHON_GATES, is_empty_scope_skippable


class TestWave3ReadinessScopeAndCache(unittest.TestCase):
    """Verify Wave 3 scope pruning, target_args propagation, and STAGE_DOMAIN_PATTERNS alignment."""

    def test_stage_domain_patterns_include_gate31_and_domain_gates(self) -> None:
        """Gate 31 and newly added domain gates must match exact stage names in _build_stages."""
        stages = _build_stages(
            py="python3",
            target_files=["tools/readiness_core.py"],
            test_modules=["tests.test_wave3_readiness_scope_and_cache"],
        )
        stage_names = {name for name, _ in stages}

        expected_domain_keys = {
            "Gate 31: Copilot Active Graph Smoke Gate",
            "Workspace Rules Budget & >90% Deduplication Gate",
            "Grounded Delivery & Claim Manifest Verification",
            "Gate 32: Direct Graph Write Path Linter",
            "Test & Healer Ratchet Linter",
        }
        for key in expected_domain_keys:
            self.assertIn(key, STAGE_DOMAIN_PATTERNS)
            self.assertIn(key, stage_names)

    def test_is_empty_scope_skippable_for_docs_only_branch(self) -> None:
        """Docs-only branch should skip Python-only and domain-unrelated gates."""
        docs_targets = ["docs/architecture_notes.md"]
        for py_gate in (2, 3, 5, 6, 7, 8, 13, 14, 18, 19, 21, 25, 27, 33, 34):
            self.assertIn(py_gate, FILE_SCOPED_PYTHON_GATES)
            self.assertTrue(
                is_empty_scope_skippable(py_gate, docs_targets, full=False),
                f"Expected stage {py_gate} to be skippable on docs-only branch",
            )
        for domain_gate in (12, 22, 23, 24, 31, 32):
            self.assertTrue(
                is_empty_scope_skippable(domain_gate, docs_targets, full=False),
                f"Expected domain stage {domain_gate} to be skippable on docs-only branch",
            )
        # Full mode never skips
        self.assertFalse(is_empty_scope_skippable(21, docs_targets, full=True))
        self.assertFalse(is_empty_scope_skippable(32, docs_targets, full=True))

    def test_gate31_fingerprint_isolated_from_docs_changes(self) -> None:
        """Gate 31 fingerprint should not change when only docs/ files are modified."""
        tmp_dir = Path(tempfile.mkdtemp(prefix="wave3_fp_test_"))
        try:
            (tmp_dir / "dual_pipeline").mkdir(parents=True)
            (tmp_dir / "dual_pipeline" / "copilot.py").write_text("X = 1\n", encoding="utf-8")
            (tmp_dir / "docs").mkdir(parents=True)
            (tmp_dir / "docs" / "note.md").write_text("v1\n", encoding="utf-8")

            stage_name = "Gate 31: Copilot Active Graph Smoke Gate"
            cmd = ["python3", "-B", "tools/verify_copilot_smoke.py"]
            fp1 = compute_stage_fingerprint(stage_name, cmd, tmp_dir, "sha1")

            (tmp_dir / "docs" / "note.md").write_text("v2 updated docs\n", encoding="utf-8")
            fp2 = compute_stage_fingerprint(stage_name, cmd, tmp_dir, "sha2")
            self.assertEqual(fp1, fp2)

            (tmp_dir / "dual_pipeline" / "copilot.py").write_text("X = 2\n", encoding="utf-8")
            fp3 = compute_stage_fingerprint(stage_name, cmd, tmp_dir, "sha3")
            self.assertNotEqual(fp1, fp3)

            digest = compute_coverage_domain_digest(tmp_dir)
            self.assertEqual(len(digest), 64)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
