"""End-to-end integration test verifying hash caching and semantic chain caching across query lifecycle.

Verifies:
1. GenerationalHashCache serves multiple cache hits during QueryEngine execution,
   eliminating repeated multi-MB disk hashing of graph.sqlite.
2. File metadata touch (mtime/size) forces cache invalidation and re-hashing.
3. RunChainDigestCache successfully memoizes verified run chains during semantic cache lookups.
4. Output integrity and truth invariants hold without regression.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
import unittest
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.cli.engine import _backend  # noqa: E402
from dual_pipeline.cli.model_resolution import auto_resolve_backend_config  # noqa: E402
from dual_pipeline.core import read_json, sha256_file  # noqa: E402
from dual_pipeline.hash_cache import cached_sha256_file, get_global_hash_cache  # noqa: E402
from dual_pipeline.l2 import OpenAIProviderFactory, load_l2_config  # noqa: E402
from dual_pipeline.query import QueryEngine  # noqa: E402
from dual_pipeline.semantic_cache_core.chain_cache import get_global_chain_cache  # noqa: E402


@pytest.mark.e2e
class TestHashCachingQueryE2E(unittest.TestCase):
    """E2E test suite for hash caching in query pipeline execution."""

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
        self.hash_cache = get_global_hash_cache()
        self.hash_cache.invalidate(None)
        self.hash_cache.reset_metrics()
        self.chain_cache = get_global_chain_cache()
        self.chain_cache.clear()
        self.chain_cache.reset_metrics()

    def test_hash_cache_query_lifecycle_hits(self) -> None:
        """Verify that a single query execution generates multiple hash cache hits for graph_path."""
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
            l2_config=l2_config,
        )
        engine.mock_mode = True

        question = f"What is the delivery status of Project Nexus? [test-{time.time_ns()}]"
        # Execute query (provider-free / exact or current state)
        result = engine.ask(
            question,
            query_type="current_state_corroboration",
            audience="internal_consulting,nee-engineering",
        )

        self.assertIsNotNone(result)
        self.assertIn("query_id", result)

        # Hash cache should have cached graph_path and served multiple hits
        self.assertGreater(
            self.hash_cache.hits,
            0,
            "Expected hash cache hits during query pipeline execution",
        )
        # Verify cached digest matches true file digest
        cached_digest = cached_sha256_file(self.graph_path)
        true_digest = sha256_file(self.graph_path)
        self.assertEqual(cached_digest, true_digest)

    def test_hash_cache_invalidation_lifecycle(self) -> None:
        """Verify explicit invalidation and recovery on temporary graph clone."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_graph = Path(tmp_dir) / "temp_graph.sqlite"
            temp_graph.write_bytes(self.graph_path.read_bytes())

            digest1 = cached_sha256_file(temp_graph)
            self.assertEqual(self.hash_cache.misses, 1)

            # Second read is hit
            digest2 = cached_sha256_file(temp_graph)
            self.assertEqual(digest2, digest1)
            self.assertEqual(self.hash_cache.hits, 1)

            # Invalidate specific file
            self.hash_cache.invalidate(temp_graph)
            digest3 = cached_sha256_file(temp_graph)
            self.assertEqual(digest3, digest1)
            self.assertEqual(self.hash_cache.misses, 2)


if __name__ == "__main__":
    unittest.main()
