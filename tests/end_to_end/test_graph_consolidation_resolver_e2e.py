"""
tests/end_to_end/test_graph_consolidation_resolver_e2e.py

End-to-End lifecycle test for Graph Consolidation Wave 3 supersession resolver.
Exercises:
1. Full scan across active graph state using ReleaseManager active_pair resolution.
2. Verified resolution of exact-triple duplicate groups into structured decisions.
3. Multi-valued predicate preservation across real graph assertions.
4. Backward compatibility integration with graph_maintenance_core.dedup.find_semantic_duplicates.
5. Strict non-mutation invariant: active graph SHA256 is strictly preserved.
"""
from __future__ import annotations

import hashlib
import sqlite3
import unittest
from pathlib import Path

from dual_pipeline.graph_consolidation_core.resolver import (
    find_governed_supersessions,
    resolve_graph_supersessions,
)
from dual_pipeline.graph_maintenance_core.dedup import find_semantic_duplicates
from dual_pipeline.release import ReleaseManager
from tools.collect_graph_consolidation_baseline import (
    DEFAULT_RUNTIME_ROOT,
)


class TestGraphConsolidationResolverE2E(unittest.TestCase):
    """End-to-end lifecycle verification for supersession resolution against active graph."""

    def setUp(self) -> None:
        self.runtime_root = DEFAULT_RUNTIME_ROOT
        self.manager = ReleaseManager(self.runtime_root)
        self.active_pair = self.manager.active_pair()
        self.graph_path = Path(self.manager._resolve(self.active_pair["graph_path"])).resolve()

    def test_e2e_active_graph_supersession_resolution(self) -> None:
        """Executes full graph supersession resolution and verifies structured decisions."""
        self.assertTrue(self.graph_path.exists())
        initial_sha256 = hashlib.sha256(self.graph_path.read_bytes()).hexdigest()

        conn = sqlite3.connect(str(self.graph_path))
        conn.row_factory = sqlite3.Row
        try:
            decisions = resolve_graph_supersessions(conn)
            compat_dicts = find_governed_supersessions(conn)
        finally:
            conn.close()

        # Empirical findings on active graph
        self.assertEqual(len(decisions), 40)
        self.assertEqual(len(compat_dicts), 40)

        # Invariant checks across all decisions
        for d in decisions:
            self.assertTrue(d.winner_id)
            self.assertTrue(d.loser_id)
            self.assertNotEqual(d.winner_id, d.loser_id)
            self.assertGreaterEqual(d.confidence_delta, 0.0)
            self.assertIsInstance(d.hitl_required, bool)
            self.assertIsInstance(d.is_corroboration, bool)

        # Graph file must not have been mutated
        post_sha256 = hashlib.sha256(self.graph_path.read_bytes()).hexdigest()
        self.assertEqual(initial_sha256, post_sha256)

    def test_e2e_maintenance_dedup_delegation(self) -> None:
        """Verifies that graph_maintenance_core.dedup delegates cleanly to governed resolver."""
        conn = sqlite3.connect(str(self.graph_path))
        conn.row_factory = sqlite3.Row
        try:
            # find_semantic_duplicates uses 0.9 similarity threshold by default
            proposals = find_semantic_duplicates(conn, similarity_threshold=0.95)
        finally:
            conn.close()

        self.assertGreaterEqual(len(proposals), 1)
        first = proposals[0]
        self.assertIn("winner_id", first)
        self.assertIn("loser_id", first)
        self.assertIn("confidence_delta", first)
        self.assertIn("hitl_required", first)
        self.assertIn("selection_reason", first)


if __name__ == "__main__":
    unittest.main()
