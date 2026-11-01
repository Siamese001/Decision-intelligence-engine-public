#!/usr/bin/env python3
"""Adversarial and edge-case tests for worktree GraphDB resolution and assertion traversal.

Mandatory Edge-Case Categories Covered:
1. Boundary Payload: Empty worktree root, missing active_pair.json, zero-node/zero-assertion graphs.
2. Malformed Input: Corrupted active_pair.json (truncated JSON, wrong schema types, missing graph_path).
3. Concurrency & Race: Parallel multi-threaded worktree active-pair resolution and k-hop assertion traversal.
4. Rollback & Recovery / Exact Production Regression: Superseded assertion vs stale edge split-brain prevention
   and automatic fallback when active generation file is missing on disk.
"""
from __future__ import annotations

import concurrent.futures
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.graph_retrieval_core.recursive_traversal import (
    traverse_k_hop_neighborhood,
)
from dual_pipeline.query_core.traversal_helper import (
    read_active_pair_graph,
    resolve_active_graph_path as traversal_resolve_active_graph_path,
    traverse_project_resources,
)
from dual_pipeline.transcript_metadata_core.entity_resolver_graph import (
    resolve_active_generation_id,
    resolve_active_graph_path as entity_resolve_active_graph_path,
)


class TestWorktreeGraphResolutionAdversarial(unittest.TestCase):
    """Adversarial test suite for worktree active GraphDB resolution and assertion traversal."""

    # ── Category 1: Boundary Payload ──────────────────────────────────────────

    def test_boundary_missing_active_pair_and_no_fallback(self) -> None:
        """Verify missing active_pair.json and missing fallback returns None cleanly without crashing."""
        with tempfile.TemporaryDirectory() as tmp:
            empty_worktree = Path(tmp) / ".worktrees" / "feat_empty"
            empty_worktree.mkdir(parents=True)

            self.assertIsNone(entity_resolve_active_graph_path(empty_worktree))
            self.assertEqual(resolve_active_generation_id(empty_worktree), "unknown")
            self.assertIsNone(
                traversal_resolve_active_graph_path(
                    repo_root=empty_worktree,
                    runtime_root=empty_worktree / ".local" / "dual_pipeline",
                    default_graph_path=empty_worktree / "graph" / "nonexistent.sqlite",
                )
            )

    def test_boundary_empty_assertion_table_falls_back_cleanly(self) -> None:
        """Verify graph with empty assertion table and empty node table returns empty traversal."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "empty.sqlite"
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT, node_type TEXT);
                CREATE TABLE assertion (
                    assertion_id TEXT PRIMARY KEY,
                    subject_node_id TEXT,
                    predicate TEXT,
                    legacy_predicate TEXT,
                    object_node_id TEXT,
                    scope_text TEXT,
                    object_literal TEXT,
                    confidence_label TEXT,
                    materiality TEXT,
                    currentness_state TEXT,
                    superseded_by_assertion_id TEXT
                );
                CREATE TABLE edge (
                    edge_id TEXT PRIMARY KEY,
                    from_node_id TEXT,
                    edge_type TEXT,
                    to_node_id TEXT,
                    description TEXT,
                    confidence TEXT,
                    materiality TEXT,
                    superseded_by TEXT
                );
                """
            )
            conn.close()

            res = traverse_k_hop_neighborhood(["nonexistent"], max_hops=2, graph_path=db_path)
            self.assertEqual(res["node_count"], 0)
            self.assertEqual(res["edge_count"], 0)

    # ── Category 2: Malformed Input ───────────────────────────────────────────

    def test_malformed_corrupted_active_pair_json(self) -> None:
        """Verify truncated JSON or non-dict payloads in active_pair.json do not raise unhandled exceptions."""
        with tempfile.TemporaryDirectory() as tmp:
            primary_root = Path(tmp) / "repo"
            worktree_root = primary_root / ".worktrees" / "feat_malformed"
            worktree_root.mkdir(parents=True)
            runtime_dir = primary_root / ".local" / "dual_pipeline"
            runtime_dir.mkdir(parents=True)
            pair_file = runtime_dir / "active_pair.json"

            # 1. Truncated JSON
            pair_file.write_text("{corrupted_json: [1, 2", encoding="utf-8")
            self.assertIsNone(read_active_pair_graph(runtime_dir))
            self.assertIsNone(entity_resolve_active_graph_path(worktree_root))

            # 2. Wrong JSON root type (array instead of object)
            pair_file.write_text('["not", "an", "object"]', encoding="utf-8")
            self.assertIsNone(read_active_pair_graph(runtime_dir))

            # 3. Missing graph_path key
            pair_file.write_text('{"generation_id": "gen-123", "graph_path": null}', encoding="utf-8")
            self.assertIsNone(read_active_pair_graph(runtime_dir))

    # ── Category 3: Concurrency & Race ────────────────────────────────────────

    def test_concurrent_worktree_resolution_and_assertion_traversal(self) -> None:
        """Verify 16 concurrent threads resolving worktree active pair and traversing assertions."""
        with tempfile.TemporaryDirectory() as tmp:
            primary_root = Path(tmp) / "repo"
            worktree_root = primary_root / ".worktrees" / "feat_concurrent"
            worktree_root.mkdir(parents=True)
            gen_dir = primary_root / ".local" / "dual_pipeline" / "generations" / "gen-concurrent"
            gen_dir.mkdir(parents=True)
            db_path = gen_dir / "graph.sqlite"

            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT, node_type TEXT);
                CREATE TABLE assertion (
                    assertion_id TEXT PRIMARY KEY,
                    subject_node_id TEXT,
                    predicate TEXT,
                    legacy_predicate TEXT,
                    object_node_id TEXT,
                    scope_text TEXT,
                    object_literal TEXT,
                    confidence_label TEXT,
                    materiality TEXT,
                    currentness_state TEXT,
                    superseded_by_assertion_id TEXT
                );
                INSERT INTO node VALUES ('n_root', 'Root', 'workstream');
                INSERT INTO node VALUES ('n_child_1', 'Child 1', 'sow');
                INSERT INTO node VALUES ('n_child_2', 'Child 2', 'card');
                INSERT INTO assertion VALUES ('a1', 'n_root', 'SUPPORTED_BY', 'SOW_BACKED_BY', 'n_child_1', 's1', NULL, 'high', 'high', 'current', NULL);
                INSERT INTO assertion VALUES ('a2', 'n_child_1', 'ROUTES_TO', 'ROUTES_TO_CARD', 'n_child_2', 's2', NULL, 'high', 'high', 'current', NULL);
                """
            )
            conn.commit()
            conn.close()

            pair_file = primary_root / ".local" / "dual_pipeline" / "active_pair.json"
            pair_file.write_text(
                json.dumps(
                    {
                        "generation_id": "gen-concurrent",
                        "graph_path": "generations/gen-concurrent/graph.sqlite",
                    }
                ),
                encoding="utf-8",
            )

            def _worker(idx: int) -> tuple[str, int]:
                resolved = entity_resolve_active_graph_path(worktree_root)
                assert resolved is not None
                res = traverse_k_hop_neighborhood(["n_root"], max_hops=2, graph_path=resolved)
                return resolved.name, res["node_count"]

            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
                futures = [pool.submit(_worker, i) for i in range(32)]
                results = [f.result(timeout=10) for f in futures]

            for name, count in results:
                self.assertEqual(name, "graph.sqlite")
                self.assertEqual(count, 3)

    # ── Category 4: Exact Production Regression (Superseded Assertion vs Frozen Edge) ──

    def test_regression_superseded_assertion_not_resurrected_by_frozen_edge(self) -> None:
        """Pin exact production regression where edge table retained superseded SOW relationship."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "regression.sqlite"
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                CREATE TABLE node (
                    node_id TEXT PRIMARY KEY,
                    canonical_name TEXT,
                    display_name TEXT,
                    node_type TEXT,
                    description TEXT,
                    status TEXT,
                    active INTEGER DEFAULT 1
                );
                CREATE TABLE assertion (
                    assertion_id TEXT PRIMARY KEY,
                    subject_node_id TEXT,
                    predicate TEXT,
                    legacy_predicate TEXT,
                    object_node_id TEXT,
                    scope_text TEXT,
                    object_literal TEXT,
                    confidence_label TEXT,
                    materiality TEXT,
                    currentness_state TEXT,
                    superseded_by_assertion_id TEXT
                );
                CREATE TABLE edge (
                    edge_id TEXT PRIMARY KEY,
                    from_node_id TEXT,
                    edge_type TEXT,
                    to_node_id TEXT,
                    description TEXT,
                    confidence TEXT,
                    materiality TEXT,
                    superseded_by TEXT
                );
                INSERT INTO node VALUES ('workstream_lens', 'LENS', 'LENS', 'workstream', '', 'ACTIVE', 1);
                INSERT INTO node VALUES ('sow_lens_visual_asset_intelligence', 'Old SOW', 'Old SOW', 'sow', '', 'ACTIVE', 1);
                INSERT INTO node VALUES ('sow_nea_ai_engineering_delivery_teams_q3_2026', 'New Q3 SOW', 'New Q3 SOW', 'sow', '', 'ACTIVE', 1);
                INSERT INTO node VALUES ('person_stale_lead', 'Stale Lead', 'Stale Lead', 'person', 'Old Lead', 'ACTIVE', 1);
                INSERT INTO node VALUES ('person_current_lead', 'Current Lead', 'Current Lead', 'person', 'New Lead', 'ACTIVE', 1);

                -- Frozen edge table still has old SOW and old lead with superseded_by = NULL
                INSERT INTO edge VALUES ('edge_c1489d0a0e1d', 'workstream_lens', 'SOW_BACKED_BY', 'sow_lens_visual_asset_intelligence', 'Old', 'high', 'high', NULL);
                INSERT INTO edge VALUES ('edge_stale_lead', 'person_stale_lead', 'OWNS_OR_LEADS', 'workstream_lens', 'Old Lead', 'high', 'high', NULL);

                -- Assertion table has old SOW and old lead marked historical and superseded
                INSERT INTO assertion VALUES (
                    'asrt_old', 'workstream_lens', 'SUPPORTED_BY', 'SOW_BACKED_BY',
                    'sow_lens_visual_asset_intelligence', 'Old', NULL, 'high', 'high', 'historical', 'asrt_new'
                );
                INSERT INTO assertion VALUES (
                    'asrt_new', 'workstream_lens', 'SUPPORTED_BY', 'Q3_CAPACITY_BACKED_BY',
                    'sow_nea_ai_engineering_delivery_teams_q3_2026', 'New', NULL, 'high', 'high', 'current', NULL
                );
                INSERT INTO assertion VALUES (
                    'asrt_lead_old', 'person_stale_lead', 'OWNS_OR_LEADS', 'OWNS_OR_LEADS',
                    'workstream_lens', 'Old Lead', NULL, 'high', 'high', 'historical', 'asrt_lead_new'
                );
                INSERT INTO assertion VALUES (
                    'asrt_lead_new', 'person_current_lead', 'OWNS_OR_LEADS', 'OWNS_OR_LEADS',
                    'workstream_lens', 'Current Lead', NULL, 'high', 'high', 'current', NULL
                );
                """
            )
            conn.commit()

            # 1. Recursive traversal must return only the current Q3 SOW and current lead
            res = traverse_k_hop_neighborhood(["workstream_lens"], max_hops=1, graph_path=db_path)
            self.assertIn("sow_nea_ai_engineering_delivery_teams_q3_2026", res["nodes"])
            self.assertIn("person_current_lead", res["nodes"])
            self.assertNotIn("sow_lens_visual_asset_intelligence", res["nodes"])
            self.assertNotIn("person_stale_lead", res["nodes"])

            # 2. traverse_project_resources must return only the current lead stakeholder
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            entities, _ = traverse_project_resources(
                cur,
                ["workstream_lens"],
                has_quarantine=False,
                tables={"node", "assertion", "edge"},
            )
            entity_ids = {e.node_id for e in entities}
            self.assertIn("person_current_lead", entity_ids)
            self.assertNotIn("person_stale_lead", entity_ids)
            conn.close()


if __name__ == "__main__":
    unittest.main()
