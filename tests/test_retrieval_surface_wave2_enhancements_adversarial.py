"""Adversarial & Concurrency Stress Tests for Wave 2 High-ROI Retrieval Enhancements (ENH-01 to ENH-03)."""

from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from dual_pipeline.live_meeting_recorder_core.copilot_retriever import search_session_context
from dual_pipeline.semantic_cache_core.chain_cache import RunChainDigestCache
from dual_pipeline.token_optimization.multi_tier_cache import (
    ExactResponseDigestCache,
    PersistentNegativeFailureCache,
    SubgraphReadMemoCache,
    TurnWindowIncrementalCache,
)


class Wave2RetrievalEnhancementsAdversarialTests(unittest.TestCase):
    def test_exact_production_regression_recreated_db_file_reinitializes_schema(self) -> None:
        """Category 1 (exact_production_regression): if a multi-tier cache SQLite file is deleted and recreated at the same path, _connect_cache_db detects the inode/mtime change and re-initializes schema."""
        with tempfile.TemporaryDirectory(prefix="adv-recreate-db-") as tmpdir:
            db_path = Path(tmpdir) / "cache.sqlite"
            cache = ExactResponseDigestCache(db_path)
            cache.put(model="m1", effort="low", messages="hello", response={"v": 1})
            self.assertEqual({"v": 1}, cache.get(model="m1", effort="low", messages="hello"))

            # Delete the underlying SQLite file (+ WAL/SHM if present) and touch a 0-byte or fresh empty DB
            for p in Path(tmpdir).glob("cache.sqlite*"):
                p.unlink()
            sqlite3.connect(db_path).close()

            # Must not fail with OperationalError: no such table: exact_response_cache
            self.assertIsNone(cache.get(model="m1", effort="low", messages="hello"))
            cache.put(model="m1", effort="low", messages="hello", response={"v": 2})
            self.assertEqual({"v": 2}, cache.get(model="m1", effort="low", messages="hello"))

    def test_invariant_bypass_attempt_subgraph_generation_invalidation_and_expired_entries(self) -> None:
        """Category 2 (invariant_bypass_attempt): stale generation subgraph memos are purged on new generation put, and expired exact/negative cache entries fail closed."""
        with tempfile.TemporaryDirectory(prefix="adv-bypass-db-") as tmpdir:
            db_path = Path(tmpdir) / "cache.sqlite"
            exact = ExactResponseDigestCache(db_path, default_ttl_seconds=-10.0)
            exact.put(model="m", effort="high", messages="expired", response={"stale": True})
            self.assertIsNone(exact.get(model="m", effort="high", messages="expired"))

            neg = PersistentNegativeFailureCache(db_path, max_failures=1, ttl_seconds=-10.0)
            neg.record_failure(stage="L1", payload_text="p", defect_code="D", rationale="R")
            is_q, info = neg.is_quarantined(stage="L1", payload_text="p")
            self.assertFalse(is_q)
            self.assertIsNone(info)

            memo = SubgraphReadMemoCache(db_path)
            memo.put(generation_id="gen-old", query_signature="sig-1", payload={"old": 1})
            memo.put(generation_id="gen-new", query_signature="sig-1", payload={"new": 2})
            self.assertIsNone(memo.get(generation_id="gen-old", query_signature="sig-1"))
            self.assertEqual({"new": 2}, memo.get(generation_id="gen-new", query_signature="sig-1"))

    def test_boundary_and_malformed_inputs_across_chain_cache_and_session_context(self) -> None:
        """Category 3 (boundary_and_malformed_input): max_size=1 RunChainDigestCache and short/empty session turns."""
        cache = RunChainDigestCache(max_size=1)
        cache.set("r1", "c1", True)
        self.assertTrue(cache.get("r1", "c1"))
        cache.set("r1", "c1", False)
        self.assertEqual(1, cache.size())
        self.assertFalse(cache.get("r1", "c1"))
        cache.set("r2", "c2", True)
        self.assertEqual(1, cache.size())
        self.assertIsNone(cache.get("r1", "c1"))
        self.assertTrue(cache.get("r2", "c2"))

        self.assertEqual("", search_session_context([], "query"))
        self.assertEqual("", search_session_context([{"text": "a"}] * 4, "query"))
        self.assertEqual("", search_session_context([{"text": "a"}] * 6, "   "))

    def test_concurrency_16_worker_stress_across_multi_tier_and_chain_caches(self) -> None:
        """Category 4 (concurrency_or_state_isolation): 16-worker concurrent stress across MultiTierCache SQLite and RunChainDigestCache without database lock errors."""
        with tempfile.TemporaryDirectory(prefix="adv-16worker-") as tmpdir:
            db_path = Path(tmpdir) / "stress_multi_tier.sqlite"
            exact = ExactResponseDigestCache(db_path)
            turns = TurnWindowIncrementalCache(db_path, window_size=2)
            neg = PersistentNegativeFailureCache(db_path, max_failures=2)
            memo = SubgraphReadMemoCache(db_path)
            chain = RunChainDigestCache(max_size=8)
            errors: list[Exception] = []

            def _worker(worker_id: int) -> None:
                try:
                    for step in range(4):
                        key_msg = f"msg-{worker_id}-{step}"
                        exact.put(model="m", effort="high", messages=key_msg, response={"w": worker_id, "s": step})
                        got = exact.get(model="m", effort="high", messages=key_msg)
                        assert got == {"w": worker_id, "s": step}

                        turns.store_window(f"mtg-{worker_id}", step, [key_msg], [key_msg])
                        res_w = turns.resolve_cached_or_dirty_windows(f"mtg-{worker_id}", [key_msg])
                        assert res_w["cached_window_count"] >= 0

                        neg.record_failure(stage="S", payload_text=key_msg, defect_code="C", rationale="R")
                        neg.is_quarantined(stage="S", payload_text=key_msg)

                        memo.put(generation_id="gen-stress", query_signature=key_msg, payload={"ok": step})
                        memo.get(generation_id="gen-stress", query_signature=key_msg)

                        chain.set(f"run-{worker_id}-{step}", "chain", True)
                        chain.get(f"run-{worker_id}-0", "chain")
                except Exception as exc:  # pragma: no cover
                    errors.append(exc)

            threads = [threading.Thread(target=_worker, args=(i,)) for i in range(16)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual([], errors)
            self.assertLessEqual(chain.size(), 8)


if __name__ == "__main__":
    unittest.main()
