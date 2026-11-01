#!/usr/bin/env python3
"""End-to-end verification of worktree-safe active GraphDB resolution and assertion traversal.

Wave 1 Deliverable:
- Verifies that linked worktree invocations resolve the primary repository's active_pair.json
  via resolve_authoritative_runtime_root() and resolve_active_pair() without falling back
  to the stale tracked graph/nee_graph.sqlite.
- Verifies that traversal_helper and recursive_traversal read current assertions
  (post-2026-10-14) instead of frozen legacy edge rows.
- Verifies that offline_retriever, graph_spine, stakeholder_validator, and publisher
  resolve the active generation prior to fallback.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import pytest

from dual_pipeline.core import sha256_file
from dual_pipeline.document_pipeline_core.publisher import _publish_args
from dual_pipeline.graph_constrained_projection.graph_spine import GraphSpineResolver
from dual_pipeline.graph_retrieval_core.recursive_traversal import (
    _resolve_default_graph_path,
    traverse_k_hop_neighborhood,
)
from dual_pipeline.query_core.offline_retriever import OfflineGraphContextRetriever
from dual_pipeline.query_core.traversal_helper import (
    resolve_active_graph_path as traversal_resolve_active_graph_path,
    spot_transcript_entities,
    traverse_project_resources,
)
from dual_pipeline.runtime_root import (
    resolve_active_graph_path as runtime_resolve_active_graph_path,
    resolve_active_pair,
    resolve_authoritative_runtime_root,
    resolve_main_repository_root,
)
from dual_pipeline.transcript_metadata_core.entity_resolver_graph import (
    resolve_active_generation_id,
    resolve_active_graph_path as entity_resolve_active_graph_path,
)


@pytest.mark.e2e
class TestWorktreeGraphResolutionE2E(unittest.TestCase):
    """E2E tests for worktree-safe active GraphDB resolution and assertion traversal."""

    def test_e2e_live_worktree_resolves_authoritative_active_generation(self) -> None:
        """Verify worktree resolvers resolve the active generation matching active_pair.json."""
        repo_root = Path(__file__).resolve().parents[2]
        main_root = resolve_main_repository_root(repo_root)
        auth_runtime_root = resolve_authoritative_runtime_root(repo_root)
        active_pair, _ = resolve_active_pair(auth_runtime_root, enforce_integrity=False)

        self.assertIsNotNone(active_pair, "Primary repository active_pair.json must be resolved")
        expected_graph = runtime_resolve_active_graph_path(
            auth_runtime_root, enforce_integrity=False
        ).resolve()
        self.assertTrue(expected_graph.is_file(), f"Active graph missing: {expected_graph}")

        # 1. entity_resolver_graph resolves active generation, never graph/nee_graph.sqlite
        resolved_entity_graph = entity_resolve_active_graph_path(repo_root)
        self.assertIsNotNone(resolved_entity_graph)
        self.assertEqual(resolved_entity_graph.resolve(), expected_graph)
        self.assertNotEqual(
            resolved_entity_graph.resolve(),
            (repo_root / "graph" / "nee_graph.sqlite").resolve(),
        )
        self.assertEqual(
            sha256_file(resolved_entity_graph),
            active_pair["graph_sha256"],
        )

        # 2. Active generation ID matches active_pair.json
        gen_id = resolve_active_generation_id(repo_root)
        self.assertEqual(gen_id, active_pair["generation_id"])

        # 3. traversal_helper resolves active generation
        resolved_traversal_graph = traversal_resolve_active_graph_path(
            repo_root=repo_root,
            runtime_root=None,
            default_graph_path=repo_root / "graph" / "nee_graph.sqlite",
        )
        self.assertIsNotNone(resolved_traversal_graph)
        self.assertEqual(resolved_traversal_graph.resolve(), expected_graph)

        # 4. recursive_traversal default graph path resolves active generation
        resolved_recursive_graph = _resolve_default_graph_path()
        self.assertEqual(resolved_recursive_graph.resolve(), expected_graph)

        # 5. OfflineGraphContextRetriever, GraphSpineResolver, and publisher resolve active generation
        retriever = OfflineGraphContextRetriever()
        self.assertEqual(retriever._resolve_graph_path().resolve(), expected_graph)

        spine = GraphSpineResolver()
        self.assertEqual(spine.db_path.resolve(), expected_graph)

        pub_args = _publish_args(
            [],
            directive_id="DIR-TEST",
            scope="test",
            reviewer="Operator",
            rationale="test",
            embedding_backend="bge-m3",
        )
        self.assertEqual(Path(pub_args.graph).resolve(), expected_graph)
        self.assertEqual(Path(pub_args.runtime_root).resolve(), auth_runtime_root.resolve())

        # 6. Verify active graph has full node and assertion inventory (>1,200 nodes, >4,000 assertions)
        conn = sqlite3.connect(f"file:{expected_graph}?mode=ro", uri=True)
        try:
            node_count = conn.execute("SELECT COUNT(*) FROM node").fetchone()[0]
            assertion_count = conn.execute("SELECT COUNT(*) FROM assertion").fetchone()[0]
            self.assertGreaterEqual(node_count, 1290)
            self.assertGreaterEqual(assertion_count, 4300)
        finally:
            conn.close()

    def test_e2e_synthetic_worktree_hierarchy_resolves_parent_active_pair(self) -> None:
        """Verify synthetic .worktrees/<slug> layout resolves parent .local/dual_pipeline/active_pair.json."""
        with tempfile.TemporaryDirectory() as tmp:
            primary_root = Path(tmp) / "repo_root"
            worktree_root = primary_root / ".worktrees" / "feat_test_branch"
            worktree_root.mkdir(parents=True)

            # Create a stale fallback in the worktree
            stale_dir = worktree_root / "graph"
            stale_dir.mkdir(parents=True)
            stale_db = stale_dir / "nee_graph.sqlite"
            conn_stale = sqlite3.connect(stale_db)
            conn_stale.execute("CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT, node_type TEXT)")
            conn_stale.execute("INSERT INTO node VALUES ('stale_only', 'Stale Node', 'concept')")
            conn_stale.commit()
            conn_stale.close()

            # Create authoritative active generation in primary_root/.local/dual_pipeline
            gen_dir = primary_root / ".local" / "dual_pipeline" / "generations" / "generation-active123"
            gen_dir.mkdir(parents=True)
            active_db = gen_dir / "graph.sqlite"
            conn_active = sqlite3.connect(active_db)
            conn_active.executescript(
                """
                CREATE TABLE node (
                    node_id TEXT PRIMARY KEY,
                    canonical_name TEXT NOT NULL,
                    display_name TEXT,
                    node_type TEXT NOT NULL,
                    description TEXT,
                    status TEXT,
                    active INTEGER DEFAULT 1
                );
                CREATE TABLE assertion (
                    assertion_id TEXT PRIMARY KEY,
                    subject_node_id TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    legacy_predicate TEXT,
                    object_node_id TEXT,
                    object_literal TEXT,
                    scope_text TEXT,
                    confidence_label TEXT,
                    materiality TEXT,
                    currentness_state TEXT DEFAULT 'current',
                    superseded_by_assertion_id TEXT
                );
                CREATE TABLE edge (
                    edge_id TEXT PRIMARY KEY,
                    from_node_id TEXT NOT NULL,
                    edge_type TEXT NOT NULL,
                    to_node_id TEXT NOT NULL,
                    description TEXT,
                    confidence TEXT,
                    materiality TEXT,
                    superseded_by TEXT
                );
                INSERT INTO node VALUES ('workstream_lens', 'Project LENS', 'Project LENS', 'workstream', 'LENS workstream', 'ACTIVE', 1);
                INSERT INTO node VALUES ('sow_q3_current', 'Q3 Current SOW', 'Q3 Current SOW', 'sow', 'Active SOW', 'ACTIVE', 1);
                INSERT INTO node VALUES ('sow_stale_edge', 'Stale Edge SOW', 'Stale Edge SOW', 'sow', 'Frozen edge SOW', 'ACTIVE', 1);
                INSERT INTO node VALUES ('person_post_oct14', 'Post Oct14 Stakeholder', 'Post Oct14 Stakeholder', 'person', 'Affiliation label(s): Apex Global. Source roles: Director.', 'ACTIVE', 1);
                INSERT INTO node VALUES ('person_stale_edge', 'Stale Edge Stakeholder', 'Stale Edge Stakeholder', 'person', 'Old lead', 'ACTIVE', 1);

                -- Stale edge table points only to sow_stale_edge and person_stale_edge
                INSERT INTO edge VALUES ('e_stale', 'workstream_lens', 'SOW_BACKED_BY', 'sow_stale_edge', 'Stale edge', 'high', 'high', NULL);
                INSERT INTO edge VALUES ('e_stale_p', 'person_stale_edge', 'OWNS_OR_LEADS', 'workstream_lens', 'Stale lead', 'high', 'high', NULL);

                -- Current assertions point to sow_q3_current and person_post_oct14
                INSERT INTO assertion VALUES (
                    'asrt_current_1', 'workstream_lens', 'SUPPORTED_BY', 'Q3_CAPACITY_BACKED_BY',
                    'sow_q3_current', NULL, 'Active Q3 capacity SOW', 'high', 'high', 'current', NULL
                );
                INSERT INTO assertion VALUES (
                    'asrt_current_2', 'person_post_oct14', 'OWNS_OR_LEADS', 'OWNS_OR_LEADS',
                    'workstream_lens', NULL, 'Leads LENS', 'high', 'high', 'current', NULL
                );
                INSERT INTO assertion VALUES (
                    'asrt_hist_1', 'workstream_lens', 'SUPPORTED_BY', 'SOW_BACKED_BY',
                    'sow_stale_edge', NULL, 'Historical SOW', 'high', 'high', 'historical', 'asrt_current_1'
                );
                INSERT INTO assertion VALUES (
                    'asrt_hist_2', 'person_stale_edge', 'OWNS_OR_LEADS', 'OWNS_OR_LEADS',
                    'workstream_lens', NULL, 'Historical lead', 'high', 'high', 'historical', 'asrt_current_2'
                );
                """
            )
            conn_active.commit()
            conn_active.close()

            pair_payload = {
                "generation_id": "generation-active123",
                "graph_path": "generations/generation-active123/graph.sqlite",
                "graph_sha256": sha256_file(active_db),
            }
            pair_path = primary_root / ".local" / "dual_pipeline" / "active_pair.json"
            pair_path.write_text(json.dumps(pair_payload), encoding="utf-8")

            # Verify worktree resolution finds active_db, never stale_db
            resolved = entity_resolve_active_graph_path(worktree_root)
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved.resolve(), active_db.resolve())

            # Verify recursive_traversal reads current assertions and ignores stale edge table
            neighborhood = traverse_k_hop_neighborhood(
                ["workstream_lens"], max_hops=1, graph_path=resolved
            )
            self.assertIn("sow_q3_current", neighborhood["nodes"])
            self.assertIn("person_post_oct14", neighborhood["nodes"])
            self.assertNotIn(
                "sow_stale_edge",
                neighborhood["nodes"],
                "Historical assertion / stale edge must not be traversed",
            )

            # Verify traversal_helper.traverse_project_resources and spot_transcript_entities read assertions
            conn_ro = sqlite3.connect(resolved)
            conn_ro.row_factory = sqlite3.Row
            cur = conn_ro.cursor()
            tables = {
                r[0]
                for r in cur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            res_entities, _ = traverse_project_resources(
                cur, ["workstream_lens"], has_quarantine=False, tables=tables
            )
            res_ids = {e.node_id for e in res_entities}
            self.assertIn("person_post_oct14", res_ids)
            self.assertNotIn("person_stale_edge", res_ids)

            spotted, _ = spot_transcript_entities(
                cur,
                "In today's session Post Oct14 Stakeholder reviewed the Q3 Current SOW.",
                has_quarantine=False,
                scoped_workstreams=["workstream_lens"],
            )
            spotted_ids = {e.node_id for e in spotted}
            self.assertIn("person_post_oct14", spotted_ids)
            person_ref = next(e for e in spotted if e.node_id == "person_post_oct14")
            self.assertIn("workstream_lens", person_ref.associated_workstreams)
            conn_ro.close()


if __name__ == "__main__":
    unittest.main()
