"""End-to-end tests for Wave 9 singleton runtime root and inventory caching.

Verifies:
1. Exactly one authoritative runtime root resolved across main repo and worktrees.
2. active_pair.json resolution via config and read-only pointers.
3. Inventory completes in < 3 s with 0 issues.
4. Generation retention policy protects active pair and receipt-referenced assets.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from dual_pipeline.retrieval_release_core.retention import evaluate_generation_retention
from dual_pipeline.runtime_evidence_core.discovery import discover_inventory
from dual_pipeline.runtime_root import (
    resolve_active_pair,
    resolve_authoritative_runtime_root,
    resolve_main_repository_root,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


class TestRuntimeRootSingletonE2E(unittest.TestCase):
    def test_main_repo_resolution_from_worktree(self) -> None:
        """Verify worktree resolution correctly identifies common main repo."""
        resolved = resolve_main_repository_root(_REPO_ROOT)
        self.assertTrue(resolved.is_dir())
        self.assertTrue((resolved / ".git").exists())

    def test_authoritative_runtime_root_singleton(self) -> None:
        """Verify singleton authoritative runtime root points to .local/dual_pipeline."""
        runtime_root = resolve_authoritative_runtime_root(_REPO_ROOT)
        self.assertTrue(runtime_root.is_dir())
        self.assertTrue((runtime_root / "active_pair.json").is_file())
        self.assertEqual(runtime_root.name, "dual_pipeline")
        self.assertEqual(runtime_root.parent.name, ".local")

    def test_runtime_root_env_override(self) -> None:
        """Verify NEE_RUNTIME_ROOT environment variable override takes precedence."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            custom_root = Path(tmp_dir) / "custom_runtime"
            custom_root.mkdir()
            old_val = os.environ.get("NEE_RUNTIME_ROOT")
            try:
                os.environ["NEE_RUNTIME_ROOT"] = str(custom_root)
                resolved = resolve_authoritative_runtime_root(_REPO_ROOT)
                self.assertEqual(resolved, custom_root.resolve())
            finally:
                if old_val is not None:
                    os.environ["NEE_RUNTIME_ROOT"] = old_val
                else:
                    os.environ.pop("NEE_RUNTIME_ROOT", None)

    def test_active_pair_pointer_resolution(self) -> None:
        """Verify pointer active_pair.json resolves target transparently."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            target_dir = Path(tmp_dir) / "authoritative"
            target_dir.mkdir()
            target_pair = target_dir / "active_pair.json"
            target_payload = {
                "schema_version": "1.0.0",
                "generation_id": "generation-target-12345",
                "embedding_backend": "BAAI/bge-m3",
                "model_id": "BAAI/bge-m3",
                "model_revision": "5617a9f61b028005a4858fdac845db406aefb181",
                "graph_path": "graph.sqlite",
                "sidecar_path": "sidecar.sqlite",
            }
            target_pair.write_text(json.dumps(target_payload), encoding="utf-8")

            # Create pointer file
            pointer_dir = Path(tmp_dir) / "worktree_view"
            pointer_dir.mkdir()
            pointer_file = pointer_dir / "active_pair.json"
            pointer_payload = {
                "$schema": "runtime-pointer:1.0.0",
                "target": str(target_pair),
                "read_only": True,
            }
            pointer_file.write_text(json.dumps(pointer_payload), encoding="utf-8")

            resolved_data, resolved_path = resolve_active_pair(pointer_dir)
            self.assertEqual(resolved_data["generation_id"], "generation-target-12345")
            self.assertEqual(resolved_path, target_pair.resolve())

    def test_inventory_performance_and_zero_issues(self) -> None:
        """Acceptance Criteria: Inventory < 3 s with 0 issues on authoritative root."""
        start_time = time.monotonic()
        inventory = discover_inventory(_REPO_ROOT, scope="main", use_cache=True)
        elapsed = time.monotonic() - start_time

        self.assertLess(elapsed, 3.0, f"Cold inventory took {elapsed:.2f}s, expected < 3.0s")
        self.assertEqual(
            len(inventory["issues"]), 0, f"Expected 0 issues, got: {inventory['issues']}"
        )
        self.assertEqual(len(inventory["runtime_pairs"]), 1)
        self.assertEqual(
            inventory["runtime_pairs"][0]["active_pair_path"],
            str((resolve_authoritative_runtime_root(_REPO_ROOT) / "active_pair.json").resolve()),
        )

        # Test cached invocation (must be fast)
        cached_start = time.monotonic()
        cached_inv = discover_inventory(_REPO_ROOT, scope="main", use_cache=True)
        cached_elapsed = time.monotonic() - cached_start

        self.assertLess(
            cached_elapsed, 1.0, f"Cached inventory took {cached_elapsed:.2f}s, expected < 1.0s"
        )
        self.assertEqual(inventory["inventory_digest"], cached_inv["inventory_digest"])

    def test_generation_retention_policy_protection(self) -> None:
        """Verify generation retention protects active pair and receipt-referenced generations."""
        report = evaluate_generation_retention(repo_root=_REPO_ROOT, keep_releases=5)
        self.assertEqual(report["status"], "PASS")
        self.assertIsNotNone(report["active_generation"])
        self.assertIn(report["active_generation"], report["retained"])
        self.assertGreater(report["receipt_generations_count"], 0)
        self.assertGreater(report["retained_count"], 0)
        self.assertIsInstance(report["eligible_for_archival"], list)


if __name__ == "__main__":
    unittest.main()
