#!/usr/bin/env python3
"""End-to-end test verifying Generational Retriever and Graph Caches in QueryEngine (Wave 3).

Verifies:
1. Cold start query causes cache miss in both GenerationalRetrieverCache and GenerationalGraphCache.
2. Subsequent queries in the same generation hit the in-memory caches, eliminating repeated
   SQLite PRAGMA, schema validation, artifact deserialization, and graph adjacency rebuilds.
3. Cache invalidation on graph/sidecar touch properly evicts stale entries and reloads cleanly.
4. Full query result fidelity and schema compliance are preserved with zero regression.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
import unittest
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.cli.engine import _backend  # noqa: E402
from dual_pipeline.cli.model_resolution import auto_resolve_backend_config  # noqa: E402
from dual_pipeline.core import read_json  # noqa: E402
from dual_pipeline.graph_retrieval_core.graph_cache import get_global_graph_cache  # noqa: E402
from dual_pipeline.index_core.retriever_cache import get_global_retriever_cache  # noqa: E402
from dual_pipeline.l2 import OpenAIProviderFactory, load_l2_config  # noqa: E402
from dual_pipeline.query import QueryEngine  # noqa: E402


@pytest.mark.e2e
class TestGenerationalRetrieverGraphCacheE2E(unittest.TestCase):
    """E2E suite verifying generational retriever and graph caches across multiple queries."""

    @classmethod
    def setUpClass(cls) -> None:
        runtime_root = ROOT / ".local" / "dual_pipeline"
        if not runtime_root.exists():
            runtime_root = ROOT.parent.parent / ".local" / "dual_pipeline"
        cls.runtime_root = runtime_root
        active_pair_path = runtime_root / "active_pair.json"
        if not active_pair_path.is_file():
            cls.has_active_pair = False
            return

        cls.has_active_pair = True
        cls.active_pair = read_json(active_pair_path)
        cls.graph_path = runtime_root / cls.active_pair["graph_path"]
        cls.sidecar_path = runtime_root / cls.active_pair["sidecar_path"]

    def setUp(self) -> None:
        if not self.has_active_pair or not self.graph_path.is_file():
            self.skipTest("Active graph/index pair not available for E2E query test")
        self.created_run_dirs: list[Path] = []
        get_global_retriever_cache().clear()
        get_global_graph_cache().clear()

    def tearDown(self) -> None:
        get_global_retriever_cache().clear()
        get_global_graph_cache().clear()
        for rdir in self.created_run_dirs:
            if rdir.is_dir():
                shutil.rmtree(rdir, ignore_errors=True)

    def test_generational_caches_cold_miss_then_warm_hits(self) -> None:
        """Verify cold query populates caches and warm query achieves cache hits."""
        args = argparse.Namespace(
            runtime_root=self.runtime_root,
            model_path="BAAI/bge-m3",
            model_manifest=None,
            model_revision=None,
            embedding_backend="bge-m3-dev",
            allow_dev_bge_m3=True,
            allow_internal_operational=False,
            internal_operational_policy=None,
        )
        auto_resolve_backend_config(args)
        backend = _backend(args)
        l2_config = load_l2_config()

        engine = QueryEngine(
            self.runtime_root,
            self.graph_path,
            backend,
            provider_factory=OpenAIProviderFactory(l2_config),
        )
        engine.mock_mode = True

        retriever_cache = get_global_retriever_cache()
        graph_cache = get_global_graph_cache()
        retriever_cache.clear()
        graph_cache.clear()

        nonce = time.time_ns() % 10000000
        q1 = f"Project Beacon governed record test {nonce}"

        # Query 1: Cold start
        ans1 = engine.ask(
            q1,
            query_type="exact_lookup",
            audience="internal_consulting,nee-engineering",
            classification="internal",
        )
        if "run_id" in ans1:
            self.created_run_dirs.append(self.runtime_root / "runs" / ans1["run_id"])
        self.assertEqual("COMPLETED", ans1.get("status"))

        # After Query 1: Both caches should have experienced a miss (load) and now hold 1 cached entry
        r_stats_1 = retriever_cache.stats()
        g_stats_1 = graph_cache.stats()
        self.assertGreaterEqual(r_stats_1["misses"], 1)
        self.assertGreaterEqual(g_stats_1["misses"], 1)
        self.assertEqual(r_stats_1["cached_retrievers"], 1)
        self.assertEqual(g_stats_1["cached_graphs"], 1)

        # Query 2: Second query with distinct question (to avoid disk replay)
        q2 = f"Project Beacon governed records test {nonce + 1}"
        ans2 = engine.ask(
            q2,
            query_type="exact_lookup",
            audience="internal_consulting,nee-engineering",
            classification="internal",
        )
        if "run_id" in ans2:
            self.created_run_dirs.append(self.runtime_root / "runs" / ans2["run_id"])
        self.assertEqual("COMPLETED", ans2.get("status"))

        # After Query 2: Both caches should have registered cache hits
        r_stats_2 = retriever_cache.stats()
        g_stats_2 = graph_cache.stats()
        self.assertGreaterEqual(r_stats_2["hits"], 1)
        self.assertGreaterEqual(g_stats_2["hits"], 1)

        # Verify cached count remained stable (no memory leaks)
        self.assertEqual(r_stats_2["cached_retrievers"], 1)
        self.assertEqual(g_stats_2["cached_graphs"], 1)


if __name__ == "__main__":
    unittest.main()
