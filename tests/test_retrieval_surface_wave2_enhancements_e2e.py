"""End-to-End Tests for Wave 2 High-ROI Retrieval Enhancements (ENH-01 to ENH-03)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dual_pipeline.live_meeting_recorder_core.copilot_retriever as copilot_retriever_mod
import dual_pipeline.token_optimization.multi_tier_cache as multi_tier_mod
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import search_session_context
from dual_pipeline.semantic_cache_core.chain_cache import RunChainDigestCache
from dual_pipeline.token_optimization.multi_tier_cache import (
    ExactResponseDigestCache,
    PersistentNegativeFailureCache,
    SubgraphReadMemoCache,
    TurnWindowIncrementalCache,
    _connect_cache_db,
)


class Wave2RetrievalEnhancementsE2ETests(unittest.TestCase):
    def test_enh01_multi_tier_cache_initializes_schema_once_per_db_file_e2e(self) -> None:
        """ENH-01 E2E: Multi-tier caches initialize _MULTI_TIER_SCHEMA once per DB file and execute zero redundant DDL calls on subsequent get/put."""
        with tempfile.TemporaryDirectory(prefix="enh01-multitier-") as tmpdir:
            db_path = Path(tmpdir) / "multi_tier.sqlite"
            exact = ExactResponseDigestCache(db_path)
            turns = TurnWindowIncrementalCache(db_path, window_size=2)
            neg = PersistentNegativeFailureCache(db_path, max_failures=2)
            memo = SubgraphReadMemoCache(db_path)

            ddl_count = 0
            orig_connect = multi_tier_mod.GovernedStorageContext.connect

            class _ConnProxy:
                def __init__(self, real_conn):
                    self._real = real_conn

                def executescript(self, sql: str):
                    nonlocal ddl_count
                    ddl_count += 1
                    return self._real.executescript(sql)

                def __getattr__(self, name: str):
                    return getattr(self._real, name)

                def __setattr__(self, name: str, value):
                    if name == "_real":
                        super().__setattr__(name, value)
                    else:
                        setattr(self._real, name, value)

            def _wrapped_connect(path: Path):
                return _ConnProxy(orig_connect(path))

            with patch.object(multi_tier_mod.GovernedStorageContext, "connect", side_effect=_wrapped_connect):
                # 1. ExactResponseDigestCache put + get
                exact.put(model="gpt-5.4", effort="high", messages=[{"role": "user", "content": "hi"}], response={"ok": True})
                self.assertEqual({"ok": True}, exact.get(model="gpt-5.4", effort="high", messages=[{"role": "user", "content": "hi"}]))
                # 2. TurnWindowIncrementalCache store + resolve
                turns.store_window("mtg-1", 0, ["t1", "t2"], ["n1", "n2"])
                resolved = turns.resolve_cached_or_dirty_windows("mtg-1", ["t1", "t2"])
                self.assertEqual(1, resolved["cached_window_count"])
                # 3. PersistentNegativeFailureCache record + check
                neg.record_failure(stage="L2", payload_text="bad", defect_code="ERR", rationale="fail")
                neg.record_failure(stage="L2", payload_text="bad", defect_code="ERR", rationale="fail")
                is_q, info = neg.is_quarantined(stage="L2", payload_text="bad")
                self.assertTrue(is_q)
                self.assertIsNotNone(info)
                # 4. SubgraphReadMemoCache put + get
                memo.put(generation_id="gen-1", query_signature="q-sig", payload={"nodes": 3})
                self.assertEqual({"nodes": 3}, memo.get(generation_id="gen-1", query_signature="q-sig"))

            # Schema DDL must be executed exactly ONCE when the DB file is first created!
            self.assertEqual(1, ddl_count)

    def test_enh02_run_chain_digest_cache_promotes_hits_and_updates_to_mru_e2e(self) -> None:
        """ENH-02 E2E: RunChainDigestCache promotes accessed keys on get() and refreshed keys on set() to MRU before evicting."""
        cache = RunChainDigestCache(max_size=3)
        cache.set("run-1", "chain-a", True)
        cache.set("run-2", "chain-b", True)
        cache.set("run-3", "chain-c", False)

        # Access run-1 so it becomes MRU; run-2 is now LRU
        self.assertTrue(cache.get("run-1", "chain-a"))
        # Update run-2 via set() so it also becomes MRU; run-3 is now LRU
        cache.set("run-2", "chain-b", True)

        # Insert run-4 at capacity=3 -> must evict run-3 (the true LRU), preserving run-1 and run-2
        cache.set("run-4", "chain-d", True)
        self.assertEqual(3, cache.size())
        self.assertIsNone(cache.get("run-3", "chain-c"))
        self.assertTrue(cache.get("run-1", "chain-a"))
        self.assertTrue(cache.get("run-2", "chain-b"))
        self.assertTrue(cache.get("run-4", "chain-d"))

    def test_enh03_search_session_context_tokenizes_each_turn_once_e2e(self) -> None:
        """ENH-03 E2E: search_session_context calls _tokenize_text at most once per turn outside the recent window."""
        session_turns = [
            {"timestamp": "10:00", "speaker": "Alice", "text": "Category Brain architecture latency metrics"},
            {"timestamp": "10:01", "speaker": "Bob", "text": "Unrelated lunch order"},
            {"timestamp": "10:02", "speaker": "Carol", "text": "Category Brain latency target approved"},
            {"timestamp": "10:03", "speaker": "Dave", "text": "Recent turn 1"},
            {"timestamp": "10:04", "speaker": "Eve", "text": "Recent turn 2"},
            {"timestamp": "10:05", "speaker": "Frank", "text": "Recent turn 3"},
            {"timestamp": "10:06", "speaker": "Grace", "text": "Recent turn 4"},
        ]
        call_count = 0
        orig_tokenize = copilot_retriever_mod._tokenize_text

        def _counting_tokenize(text: str):
            nonlocal call_count
            call_count += 1
            return orig_tokenize(text)

        with patch.object(copilot_retriever_mod, "_tokenize_text", side_effect=_counting_tokenize):
            out = search_session_context(session_turns, "Category Brain latency", top_k=2)

        self.assertIn("10:00 Alice", out)
        self.assertIn("10:02 Carol", out)
        # Exactly 3 turns are in session_turns[:-4], so _tokenize_text must be called exactly 3 times (not 5 or 6)
        self.assertEqual(3, call_count)


if __name__ == "__main__":
    unittest.main()
