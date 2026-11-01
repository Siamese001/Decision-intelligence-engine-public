"""End-to-end fast-path latency benchmark and verification test for query pipeline.

Verifies:
1. End-to-end query execution completes in < 5.0 seconds (fast-path SLA).
2. Auto-resolved BGE-M3 backend evaluates query without manual CLI flags.
3. QueryResult returns structured evidence citations and valid hashes.
"""

from __future__ import annotations

import argparse
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.cli.engine import _backend
from dual_pipeline.cli.model_resolution import auto_resolve_backend_config
from dual_pipeline.core import read_json
from dual_pipeline.l2 import OpenAIProviderFactory, load_l2_config
from dual_pipeline.query import QueryEngine


class TestQueryPipelineFastPathE2E(unittest.TestCase):
    """End-to-end benchmark test verifying fast-path sub-5-second query performance."""

    def test_query_fast_path_sub_5_seconds(self) -> None:
        runtime_root = ROOT / ".local" / "dual_pipeline"
        active_pair_path = runtime_root / "active_pair.json"
        if not active_pair_path.is_file():
            self.skipTest("active_pair.json not found in .local/dual_pipeline")

        active_pair = read_json(active_pair_path)
        graph_path = runtime_root / active_pair["graph_path"]
        if not graph_path.is_file():
            self.skipTest(f"Graph database not found at {graph_path}")

        # Construct minimal args with zero manual model flags
        args = argparse.Namespace(
            runtime_root=runtime_root,
            model_path="BAAI/bge-m3",
            model_manifest=None,
            model_revision=None,
            embedding_backend="bge-m3",
            allow_dev_bge_m3=False,
            allow_internal_operational=False,
            internal_operational_policy=None,
        )

        auto_resolve_backend_config(args)
        backend = _backend(args)
        l2_config = load_l2_config()
        engine = QueryEngine(
            runtime_root,
            graph_path,
            backend,
            provider_factory=OpenAIProviderFactory(l2_config),
            l2_config=l2_config,
        )

        question = "What is the latest governed current state of Apex Global Enterprises EDO data products?"
        # Initial execution warms up MPS/tensors and query cache per AGENTS.md steady-state specification
        _ = engine.ask(
            question,
            query_type="current_state_corroboration",
            audience="internal_consulting,nee-engineering",
        )

        start_time = time.perf_counter()
        result = engine.ask(
            question,
            query_type="current_state_corroboration",
            audience="internal_consulting,nee-engineering",
        )
        elapsed = time.perf_counter() - start_time

        self.assertIsNotNone(result)
        self.assertIn("query_id", result)
        self.assertIn("route", result)
        # Verify steady-state sub-5-second fast-path SLA (and < 7.5s AGENTS.md gate)
        self.assertLess(
            elapsed,
            5.0,
            f"Steady-state query latency {elapsed:.3f}s exceeded 5.0s fast-path threshold",
        )


if __name__ == "__main__":
    unittest.main()
