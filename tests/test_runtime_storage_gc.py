"""Unit tests for runtime_storage_gc generation retention planner."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from tools.runtime_storage_gc import run_generations_plan
from tools.storage_hygiene_core.generations import (
    ActivationInProgressError,
    plan_generations_retention,
)
from tools.storage_hygiene_core.pins import PinResolutionResult
from tools.storage_hygiene_core.policy import load_storage_retention_policy
from tools.storage_hygiene_core.receipt import verify_report_digest


class TestRuntimeStorageGC(unittest.TestCase):
    def test_mock_generation_classification_and_metrics(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rt_root = Path(tmpdir)
            gens_dir = rt_root / "generations"
            gens_dir.mkdir(parents=True)

            # Gen 1: Active
            g1 = gens_dir / "generation-active-1"
            g1.mkdir()
            (g1 / "graph.sqlite").write_text("data 1", encoding="utf-8")

            # Gen 2: Rollback
            g2 = gens_dir / "generation-rollback-2"
            g2.mkdir()
            (g2 / "graph.sqlite").write_text("data 2", encoding="utf-8")

            # Gen 3: Candidate (old)
            g3 = gens_dir / "generation-candidate-3"
            g3.mkdir()
            (g3 / "graph.sqlite").write_text("data 3", encoding="utf-8")
            old_time = time.time() - (10 * 86400)
            (g3 / "graph.sqlite").touch()
            os_mod_time = old_time
            import os
            os.utime(g3, (os_mod_time, os_mod_time))

            # Gen 4: Loose file
            (gens_dir / "forced_promotion_graph.sqlite").write_text("loose data", encoding="utf-8")

            pins = PinResolutionResult(
                active_generations={"generation-active-1"},
                rollback_generations={"generation-rollback-2"},
            )
            pol = load_storage_retention_policy()

            result = plan_generations_retention(runtime_root=rt_root, policy=pol, pins=pins)
            self.assertEqual(result.total_generations_count, 3)
            self.assertEqual(len(result.loose_files), 1)
            self.assertEqual(result.loose_files[0]["name"], "forced_promotion_graph.sqlite")

            cat_map = {c.generation_id: c.category for c in result.classifications}
            self.assertEqual(cat_map["generation-active-1"], "PINNED_ACTIVE")
            self.assertEqual(cat_map["generation-rollback-2"], "PINNED_ROLLBACK")
            self.assertEqual(cat_map["generation-candidate-3"], "CANDIDATE")
            self.assertEqual(result.candidate_count, 1)

    def test_active_pair_lock_blocks_planning(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rt_root = Path(tmpdir)
            (rt_root / "generations").mkdir(parents=True)
            lock_file = rt_root / "active_pair.lock"
            lock_file.write_text("locked", encoding="utf-8")

            pins = PinResolutionResult()
            pol = load_storage_retention_policy()
            with self.assertRaises(ActivationInProgressError):
                plan_generations_retention(runtime_root=rt_root, policy=pol, pins=pins)

    def test_real_repo_plan_generation(self):
        """Run plan on the actual workspace and verify integrity."""
        report = run_generations_plan(
            runtime_root=Path(__file__).resolve().parents[1] / ".local" / "dual_pipeline",
            scheduled=False,
        )
        self.assertNotIn("error", report)
        self.assertTrue(verify_report_digest(report))
        self.assertIn("generations_analysis", report)

        analysis = report["generations_analysis"]
        self.assertGreater(analysis["total_generations_count"], 0)
        self.assertGreater(analysis["pinned_count"], 0)

        # Active generation must never appear in candidates
        candidate_ids = {c["generation_id"] for c in analysis["candidates"]}
        active_ids = set(report["pins"]["active_generations"])
        self.assertEqual(candidate_ids.intersection(active_ids), set())


if __name__ == "__main__":
    unittest.main()
