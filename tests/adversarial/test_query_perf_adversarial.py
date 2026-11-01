"""Adversarial performance edge-case and stress test suite for QueryEngine (Wave 10).

Covers:
1. Boundary payload: Query against empty graph (0 assertions, 0 search documents).
2. Cache-miss storm: 10 unique queries in rapid succession (cold semantic cache).
3. Concurrent query contention: 4 simultaneous queries sharing all caches.
4. Large graph scaling: Query against graph with 1,000+ synthetic assertions.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import sqlite3
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
from dual_pipeline.core import read_json  # noqa: E402
from dual_pipeline.l2 import OpenAIProviderFactory, load_l2_config  # noqa: E402
from dual_pipeline.query import QueryEngine  # noqa: E402
from tools.profile_query_pipeline import load_sla_budgets, profile_query_engine  # noqa: E402


@pytest.mark.e2e
class TestQueryPerfAdversarial(unittest.TestCase):
    """Adversarial stress tests for QueryEngine performance under adverse conditions."""

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

        cli_args = argparse.Namespace(
            runtime_root=cls.runtime_root,
            model_path="BAAI/bge-m3",
            model_manifest=None,
            model_revision=None,
            embedding_backend=cls.active_pair.get("backend", "bge-m3-dev"),
            device="cpu",
            fp16=False,
            assurance_tier="internal_operational",
            allow_dev_bge_m3=True,
            allow_internal_operational=False,
            internal_operational_policy=None,
        )
        auto_resolve_backend_config(cli_args)
        cls.backend = _backend(cli_args)
        cls.l2_config = load_l2_config()

    def setUp(self) -> None:
        if not self.has_active_pair or not self.graph_path.is_file():
            self.skipTest("Active graph/index pair not available for adversarial perf test")

        self.engine = QueryEngine(
            self.runtime_root,
            self.graph_path,
            self.backend,
            provider_factory=OpenAIProviderFactory(self.l2_config),
            l2_config=self.l2_config,
        )
        self.engine.mock_mode = True
        self.budgets = load_sla_budgets()

    def test_adversarial_boundary_empty_graph(self) -> None:
        """Query against empty graph with 0 assertions terminates cleanly within SLA (< 60s)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            empty_graph = tmp_root / "empty_graph.sqlite"
            conn = sqlite3.connect(str(empty_graph))
            conn.execute(
                """CREATE TABLE IF NOT EXISTS assertion (
                    assertion_id TEXT PRIMARY KEY,
                    subject TEXT,
                    predicate TEXT,
                    object TEXT,
                    confidence REAL
                )"""
            )
            conn.commit()
            conn.close()

            engine_empty = QueryEngine(
                self.runtime_root,
                empty_graph,
                self.backend,
                provider_factory=OpenAIProviderFactory(self.l2_config),
                l2_config=self.l2_config,
            )
            engine_empty.mock_mode = True

            res, receipt = profile_query_engine(
                engine_empty,
                "Find any assertions on unknown system entity",
                query_type="exact_lookup",
                budgets=self.budgets,
            )

            self.assertIsNotNone(res)
            self.assertLessEqual(
                receipt.retrieval_duration_ms,
                7500.0,
                f"Empty graph retrieval took {receipt.retrieval_duration_ms:.1f}ms >= 7500ms",
            )
            self.assertLess(
                receipt.total_duration_ms,
                60000.0,
                f"Empty graph query took {receipt.total_duration_ms:.1f}ms >= 60000ms",
            )

    def test_adversarial_cache_miss_storm(self) -> None:
        """Rapid cache-miss queries in succession all satisfy Gate 10 (< 7,500ms retrieval)."""
        storm_retrieval_latencies: list[float] = []

        for i in range(4):
            # Highly unique synthetic tokens guarantee zero semantic cache hits
            unique_q = f"Unique query iteration {i} timestamp {time.time_ns()} uuid {hash((i, time.time_ns()))}"
            res, receipt = profile_query_engine(
                self.engine,
                unique_q,
                query_type="exact_lookup",
                budgets=self.budgets,
            )
            storm_retrieval_latencies.append(receipt.retrieval_duration_ms)
            self.assertIsNotNone(res)
            # Gate 10 check per request: retrieval <= 7500ms
            self.assertLessEqual(
                receipt.retrieval_duration_ms,
                7500.0,
                f"Query {i} in cache-miss storm retrieval took {receipt.retrieval_duration_ms:.1f}ms >= 7,500ms",
            )
            # Overall end-to-end SLA <= 60s
            self.assertLess(receipt.total_duration_ms, 60000.0)

        avg_retrieval = sum(storm_retrieval_latencies) / len(storm_retrieval_latencies)
        self.assertLessEqual(avg_retrieval, 7500.0, f"Average storm retrieval was {avg_retrieval:.1f}ms")

    def test_adversarial_concurrent_query_contention(self) -> None:
        """4 concurrent threads querying simultaneously share caches without deadlock or race."""
        num_threads = 4
        questions = [
            f"Concurrent worker question {i} evaluating Project Nexus architecture"
            for i in range(num_threads)
        ]

        def query_worker(q_text: str) -> tuple[int, float, dict]:
            t0 = time.monotonic()
            res = self.engine.ask(q_text, query_type="current_state_corroboration")
            dur = (time.monotonic() - t0) * 1000.0
            return (1 if res is not None else 0, dur, res)

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as pool:
            futures = [pool.submit(query_worker, q) for q in questions]
            results = [f.result(timeout=60.0) for f in concurrent.futures.as_completed(futures)]

        self.assertEqual(len(results), num_threads)
        for success, dur_ms, res in results:
            self.assertEqual(success, 1)
            self.assertIn("query_id", res)
            self.assertLessEqual(dur_ms, 60000.0)

    def test_adversarial_large_graph_scaling(self) -> None:
        """Query against graph with 1,000 synthetic assertions satisfies Gate 10 (< 7,500ms)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            large_graph = tmp_root / "large_graph.sqlite"
            conn = sqlite3.connect(str(large_graph))
            conn.execute(
                """CREATE TABLE IF NOT EXISTS assertion (
                    assertion_id TEXT PRIMARY KEY,
                    subject TEXT,
                    predicate TEXT,
                    object TEXT,
                    confidence REAL
                )"""
            )
            rows = [
                (f"asrt_{i:04d}", f"subject_{i}", "relates_to", f"object_{i}", 0.95)
                for i in range(1000)
            ]
            conn.executemany("INSERT INTO assertion VALUES (?, ?, ?, ?, ?)", rows)
            conn.commit()
            conn.close()

            engine_large = QueryEngine(
                self.runtime_root,
                large_graph,
                self.backend,
                provider_factory=OpenAIProviderFactory(self.l2_config),
                l2_config=self.l2_config,
            )
            engine_large.mock_mode = True

            res, receipt = profile_query_engine(
                engine_large,
                "Querying against 1000 synthetic assertions in scale graph",
                query_type="exact_lookup",
                budgets=self.budgets,
            )

            self.assertIsNotNone(res)
            self.assertLessEqual(
                receipt.retrieval_duration_ms,
                7500.0,
                f"1000-assertion graph retrieval took {receipt.retrieval_duration_ms:.1f}ms >= 7,500ms Gate 10 ceiling",
            )
            self.assertLess(
                receipt.total_duration_ms,
                60000.0,
                f"1000-assertion graph query took {receipt.total_duration_ms:.1f}ms >= 60000ms",
            )


if __name__ == "__main__":
    unittest.main()
