#!/usr/bin/env python3
"""
Tests for Wave 4 modularity readiness hardening:
1. Full-graph Tarjan SCC cycle detection when running lint_layer_imports.py on a single file.
2. Config-only commits triggering modularity gates (not skipped as empty Python scope).
3. Readiness cache fingerprint invalidation on UTC date rollover or config change.
4. Escalation of executable Python files inside Lane 0 directories (docs/, archive/, reports/) to Lane 1.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.change_lane_classifier import classify_file, load_policy
from tools.lint_layer_imports import run_linter
from tools.readiness_cache_core.manifest import _compute_env_digest
from tools.readiness_planner import check_disjoint_base_advance_equivalence
from tools.readiness_scope_collector import is_empty_scope_skippable


class TestModularityReadinessHardening(unittest.TestCase):
    def test_01_single_file_cli_detects_multi_hop_circular_import_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pkg = root / "dual_pipeline"
            pkg.mkdir(parents=True, exist_ok=True)
            (pkg / "__init__.py").write_text("", encoding="utf-8")

            mod_a = pkg / "cycle_alpha.py"
            mod_b = pkg / "cycle_beta.py"
            mod_a.write_text("import dual_pipeline.cycle_beta\n", encoding="utf-8")
            mod_b.write_text("import dual_pipeline.cycle_alpha\n", encoding="utf-8")

            # Pass only mod_a on explicit_files; mod_b is untouched on disk
            rc = run_linter(root, staged=False, explicit_files=[str(mod_a)])
            self.assertEqual(rc, 1)

    def test_02_config_only_modification_does_not_skip_modularity_gates(self) -> None:
        config_targets = ["config/governance/file_budget_exclusions.json"]
        for stage_idx in (2, 3, 18, 19):
            self.assertFalse(
                is_empty_scope_skippable(stage_idx, config_targets, full=False),
                f"Stage {stage_idx} must not be skipped when modularity config changes",
            )
        # Non-modularity Python gate can still skip when only budget exclusions json changed
        self.assertTrue(is_empty_scope_skippable(5, config_targets, full=False))

    def test_03_readiness_cache_env_digest_binds_utc_date_and_config_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg_dir = root / "config" / "governance"
            cfg_dir.mkdir(parents=True, exist_ok=True)
            excl_file = cfg_dir / "file_budget_exclusions.json"
            excl_file.write_text('{"exclusions": []}\n', encoding="utf-8")

            with mock.patch.dict(os.environ, {"NEE_READINESS_UTC_DATE": "2026-10-25"}):
                d1 = _compute_env_digest(root)
            with mock.patch.dict(os.environ, {"NEE_READINESS_UTC_DATE": "2026-10-26"}):
                d2 = _compute_env_digest(root)
            self.assertNotEqual(d1, d2)

            with mock.patch.dict(os.environ, {"NEE_READINESS_UTC_DATE": "2026-10-26"}):
                excl_file.write_text('{"exclusions": [{"path": "x.py"}]}\n', encoding="utf-8")
                d3 = _compute_env_digest(root)
            self.assertNotEqual(d2, d3)

    def test_04_python_files_in_lane0_directories_escalate_to_lane1(self) -> None:
        policy = load_policy()
        for p in ("docs/sneaky_helper.py", "archive/legacy_runner.py", "reports/exploit.py"):
            lane, reason = classify_file(p, policy)
            self.assertGreaterEqual(lane, 1, f"{p} must not remain in Lane 0")
            self.assertIn("Lane 1", reason)

        # Markdown in docs remains Lane 0
        md_lane, _ = classify_file("docs/plans/clean_plan.md", policy)
        self.assertEqual(md_lane, 0)

    def test_05_disjoint_base_advance_rejects_concurrent_python_changes(self) -> None:
        with mock.patch(
            "tools.readiness_planner.get_mainline_diff_files",
            return_value=["dual_pipeline/main_changed.py"],
        ):
            ok, reason = check_disjoint_base_advance_equivalence(
                "aaaa1111",
                "bbbb2222",
                ["dual_pipeline/branch_changed.py"],
            )
            self.assertFalse(ok)
            self.assertIn("Stage 2", reason)


if __name__ == "__main__":
    unittest.main()
