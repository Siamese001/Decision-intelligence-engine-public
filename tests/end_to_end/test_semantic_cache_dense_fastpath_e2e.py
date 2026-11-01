#!/usr/bin/env python3
"""End-to-end test verifying dense-only encoding fastpath for semantic cache (Wave 2).

Verifies:
1. On cache miss: encode_dense_only is called for cache lookup, and full encode is called
   once in baseline retrieval to populate hybrid retrieval quad-vectors.
2. On cache hit: encode_dense_only is called for cache lookup, and full encode is NEVER called,
   eliminating sparse + ColBERT computation entirely on cache hits.
3. Output integrity, status, and answer equality between initial run and replay.
4. Fallback compatibility when a backend only provides encode() without encode_dense_only.
"""

from __future__ import annotations

import argparse
import sys
import time
import unittest
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.cli.engine import _backend
from dual_pipeline.cli.model_resolution import auto_resolve_backend_config
from dual_pipeline.core import read_json
from dual_pipeline.l2 import OpenAIProviderFactory, load_l2_config
from dual_pipeline.query import QueryEngine
from dual_pipeline.semantic_cache import SemanticCache, configure_semantic_cache


class _TrackingBackendProxy:
    """Delegating proxy that counts calls to encode and encode_dense_only."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.encode_count = 0
        self.encode_dense_only_count = 0

    def encode(self, text: str) -> dict[str, Any]:
        self.encode_count += 1
        return self._inner.encode(text)

    def encode_dense_only(self, text: str) -> list[float]:
        self.encode_dense_only_count += 1
        return self._inner.encode_dense_only(text)

    def encode_many_dense_only(
        self, texts: Any, *, batch_size: int | None = None
    ) -> list[list[float]]:
        texts_list = list(texts)
        self.encode_dense_only_count += len(texts_list)
        return self._inner.encode_many_dense_only(texts_list, batch_size=batch_size)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _LegacyBackendProxy:
    """Proxy that conceals encode_dense_only to test fallback behavior."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.encode_count = 0

    def encode(self, text: str) -> dict[str, Any]:
        self.encode_count += 1
        return self._inner.encode(text)

    def __getattr__(self, name: str) -> Any:
        if name in ("encode_dense_only", "encode_many_dense_only"):
            raise AttributeError(name)
        return getattr(self._inner, name)


@pytest.mark.e2e
class TestSemanticCacheDenseFastpathE2E(unittest.TestCase):
    """E2E suite verifying dense-only encoding optimization across cache miss and hit."""

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
        self.created_entry_ids: list[str] = []
        self.created_run_dirs: list[Path] = []

    def tearDown(self) -> None:
        import shutil

        if hasattr(self, "created_entry_ids") and self.created_entry_ids:
            try:
                sc = SemanticCache(self.runtime_root)
                with sc._connection() as conn:
                    for eid in self.created_entry_ids:
                        conn.execute(
                            "DELETE FROM semantic_cache_entry WHERE entry_id = ?",
                            (eid,),
                        )
                    conn.commit()
            except Exception:
                pass
        if hasattr(self, "created_run_dirs"):
            for rdir in self.created_run_dirs:
                if rdir.is_dir():
                    shutil.rmtree(rdir, ignore_errors=True)

    def test_cache_miss_then_hit_fastpath(self) -> None:
        """Verify encode_dense_only on cache miss + hit, and zero encode() on hit."""
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
        real_backend = _backend(args)
        tracking_backend = _TrackingBackendProxy(real_backend)
        l2_config = load_l2_config()

        # Ensure read_write semantic cache
        configure_semantic_cache(self.runtime_root, "read_write")

        engine = QueryEngine(
            self.runtime_root,
            self.graph_path,
            tracking_backend,
            provider_factory=OpenAIProviderFactory(l2_config),
            l2_config=l2_config,
        )
        engine.mock_mode = True

        unique_nonce = time.time_ns() % 100000000
        q1 = f"Project Beacon governed record semantic cache test {unique_nonce}"
        q2 = f"Project Beacon governed records semantic cache test {unique_nonce}"

        # --- Phase 1: Cold Cache Miss ---
        initial_encode = tracking_backend.encode_count
        initial_dense_only = tracking_backend.encode_dense_only_count

        res1 = engine.ask(
            q1,
            query_type="exact_lookup",
            audience="internal_consulting,nee-engineering",
        )
        if "run_id" in res1:
            self.created_run_dirs.append(self.runtime_root / "runs" / res1["run_id"])
        eid = res1.get("semantic_cache", {}).get("writeback", {}).get("entry_id")
        if eid:
            self.created_entry_ids.append(eid)

        self.assertEqual("COMPLETED", res1["status"])
        self.assertFalse(res1["semantic_cache"]["lookup_hit"])

        # Miss path: 1 dense-only call for cache lookup + 1 full encode for retrieval
        self.assertEqual(tracking_backend.encode_dense_only_count - initial_dense_only, 1)
        self.assertEqual(tracking_backend.encode_count - initial_encode, 1)

        # --- Phase 2: Warm Cache Hit ---
        before_replay_encode = tracking_backend.encode_count
        before_replay_dense_only = tracking_backend.encode_dense_only_count

        res2 = engine.ask(
            q2,
            query_type="exact_lookup",
            audience="internal_consulting,nee-engineering",
        )
        if "run_id" in res2:
            self.created_run_dirs.append(self.runtime_root / "runs" / res2["run_id"])

        self.assertEqual("COMPLETED", res2["status"])
        self.assertTrue(res2["semantic_cache"]["lookup_hit"])
        self.assertTrue(res2["semantic_cache"]["replayed_output"])

        # Hit path: exactly 1 dense-only call, ZERO full encode calls!
        self.assertEqual(
            tracking_backend.encode_dense_only_count - before_replay_dense_only, 1
        )
        self.assertEqual(tracking_backend.encode_count - before_replay_encode, 0)

        # Parity check: answers match
        self.assertEqual(res1["answer"], res2["answer"])

    def test_fallback_when_backend_lacks_dense_only(self) -> None:
        """Verify query pipeline operates normally when backend lacks encode_dense_only."""
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
        real_backend = _backend(args)
        legacy_backend = _LegacyBackendProxy(real_backend)
        l2_config = load_l2_config()

        configure_semantic_cache(self.runtime_root, "read_write")

        engine = QueryEngine(
            self.runtime_root,
            self.graph_path,
            legacy_backend,
            provider_factory=OpenAIProviderFactory(l2_config),
            l2_config=l2_config,
        )
        engine.mock_mode = True

        unique_nonce = time.time_ns() % 100000000
        question = f"Project Beacon fallback verification test {unique_nonce}"

        res = engine.ask(
            question,
            query_type="exact_lookup",
            audience="internal_consulting,nee-engineering",
        )
        if "run_id" in res:
            self.created_run_dirs.append(self.runtime_root / "runs" / res["run_id"])
        eid = res.get("semantic_cache", {}).get("writeback", {}).get("entry_id")
        if eid:
            self.created_entry_ids.append(eid)

        self.assertEqual("COMPLETED", res["status"])
        self.assertGreaterEqual(legacy_backend.encode_count, 1)


if __name__ == "__main__":
    unittest.main()
