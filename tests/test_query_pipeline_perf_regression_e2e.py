#!/usr/bin/env python3
"""End-to-end performance regression and SLA budget enforcement suite (Wave 10).

Verifies across parameterized query route types:
1. Exact lookup, current state, gap risk, synthesis, and challenger queries meet SLA budgets.
2. Per-stage latency budgets conform to config/performance/wave_4_benchmarks.yaml.
3. Total retrieval latency satisfies Gate 10 hard ceiling (< 7,500 ms).
4. Overall end-to-end reasoning latency remains < 60 seconds.
"""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.cli.engine import _backend  # noqa: E402
from dual_pipeline.cli.model_resolution import auto_resolve_backend_config  # noqa: E402
from dual_pipeline.core import read_json  # noqa: E402
from dual_pipeline.l2 import OpenAIProviderFactory, load_l2_config  # noqa: E402
from dual_pipeline.query import QueryEngine  # noqa: E402
from tools.profile_query_pipeline import load_sla_budgets, profile_query_engine  # noqa: E402


@pytest.mark.e2e
class TestQueryPipelinePerfRegressionE2E(unittest.TestCase):
    """End-to-end SLA regression test suite parameterized across query routes."""

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
        cls.engine = QueryEngine(
            cls.runtime_root,
            cls.graph_path,
            cls.backend,
            provider_factory=OpenAIProviderFactory(cls.l2_config),
            l2_config=cls.l2_config,
        )
        cls.engine.mock_mode = True
        cls.budgets = load_sla_budgets()

    def setUp(self) -> None:
        if not self.has_active_pair or not self.graph_path.is_file():
            self.skipTest("Active graph/index pair not available for E2E perf regression test")

    def _execute_repetitions(
        self, question: str, query_type: str, repetitions: int = 3
    ) -> Dict[str, Any]:
        retrieval_durations: List[float] = []
        total_durations: List[float] = []
        last_receipt = None

        for _ in range(repetitions):
            _, receipt = profile_query_engine(
                self.engine,
                question,
                query_type=query_type,
                budgets=self.budgets,
            )
            retrieval_durations.append(receipt.retrieval_duration_ms)
            total_durations.append(receipt.total_duration_ms)
            last_receipt = receipt

        return {
            "p50_total_ms": float(np.percentile(total_durations, 50)),
            "p95_total_ms": float(np.percentile(total_durations, 95)),
            "p50_retrieval_ms": float(np.percentile(retrieval_durations, 50)),
            "p95_retrieval_ms": float(np.percentile(retrieval_durations, 95)),
            "last_receipt": last_receipt,
        }

    def test_e2e_exact_lookup_route_sla(self) -> None:
        """Verify exact_lookup query satisfies Gate 10 hard ceiling and SLA budgets."""
        stats = self._execute_repetitions(
            question="Find exact decisions on Project Nexus SOW boundaries",
            query_type="exact_lookup",
            repetitions=2,
        )
        # Gate 10 Hard Ceiling: retrieval must be < 7,500 ms
        self.assertLessEqual(
            stats["p95_retrieval_ms"],
            7500.0,
            f"Exact lookup p95 retrieval {stats['p95_retrieval_ms']}ms exceeded 7,500ms ceiling",
        )
        # End-to-end total must be < 60s
        self.assertLess(stats["p95_total_ms"], 60000.0)

    def test_e2e_current_state_route_sla(self) -> None:
        """Verify current_state query satisfies Gate 10 hard ceiling and SLA budgets."""
        stats = self._execute_repetitions(
            question="What is the current delivery status and phase alignment of Project Nexus?",
            query_type="current_state_corroboration",
            repetitions=2,
        )
        self.assertLessEqual(
            stats["p95_retrieval_ms"],
            7500.0,
            f"Current state p95 retrieval {stats['p95_retrieval_ms']}ms exceeded 7,500ms ceiling",
        )
        self.assertLess(stats["p95_total_ms"], 60000.0)

    def test_e2e_gap_risk_route_sla(self) -> None:
        """Verify gap_risk query satisfies Gate 10 hard ceiling and SLA budgets."""
        stats = self._execute_repetitions(
            question="What architectural risks or dependencies exist for Project Nexus ingestion?",
            query_type="gap_risk_inconsistency",
            repetitions=2,
        )
        self.assertLessEqual(
            stats["p95_retrieval_ms"],
            7500.0,
            f"Gap risk p95 retrieval {stats['p95_retrieval_ms']}ms exceeded 7,500ms ceiling",
        )
        self.assertLess(stats["p95_total_ms"], 60000.0)

    def test_e2e_synthesis_route_sla(self) -> None:
        """Verify synthesis query satisfies Gate 10 hard ceiling and SLA budgets."""
        stats = self._execute_repetitions(
            question="Synthesize workstream milestones and stakeholder commitments for Project Nexus",
            query_type="cross_workstream_synthesis",
            repetitions=2,
        )
        self.assertLessEqual(
            stats["p95_retrieval_ms"],
            7500.0,
            f"Synthesis p95 retrieval {stats['p95_retrieval_ms']}ms exceeded 7,500ms ceiling",
        )
        self.assertLess(stats["p95_total_ms"], 60000.0)

    def test_e2e_relationship_route_sla(self) -> None:
        """Verify relationship_explanation query satisfies Gate 10 hard ceiling and SLA budgets."""
        stats = self._execute_repetitions(
            question="Explain the relationship between Project Nexus architecture and delivery workstreams",
            query_type="relationship_explanation",
            repetitions=2,
        )
        self.assertLessEqual(
            stats["p95_retrieval_ms"],
            7500.0,
            f"Relationship p95 retrieval {stats['p95_retrieval_ms']}ms exceeded 7,500ms ceiling",
        )
        self.assertLess(stats["p95_total_ms"], 60000.0)


if __name__ == "__main__":
    unittest.main()
