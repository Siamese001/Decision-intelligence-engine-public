"""
tests/performance/test_fallback_sla.py

Phase 3 Performance & Latency SLA Test Suite.
Enforces:
1. Cache-Miss SLA Gates & Fallback Budgets (Agent 8): Strict 1.0ms p95 latency ceiling on L2 disk/topology fallback.
2. L1 Cache Sub-Millisecond SLA (<0.5ms p95).
3. Telemetry and sliding-window fallback metrics verification.
"""
from __future__ import annotations

import statistics
import time
from pathlib import Path

import pytest

from dual_pipeline.live_meeting_recorder_core.copilot_circuit_breaker import (
    get_copilot_circuit_breaker,
    reset_copilot_circuit_breaker,
)
from dual_pipeline.live_meeting_recorder_core.copilot_lifecycle import reset_lifecycle_orchestrators
from dual_pipeline.live_meeting_recorder_core.copilot_neighborhood import (
    CopilotNeighborhoodStore,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
    load_copilot_graph,
)
from tests.fixtures.high_fidelity_graph import create_high_fidelity_sqlite_graph


@pytest.fixture(autouse=True)
def clean_state():
    reset_lifecycle_orchestrators()
    reset_copilot_circuit_breaker()
    yield
    reset_lifecycle_orchestrators()
    reset_copilot_circuit_breaker()


@pytest.fixture
def high_fidelity_graph_fixture(tmp_path: Path):
    runtime_dir = tmp_path / "perf_runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    db_path = runtime_dir / "graph.sqlite"
    create_high_fidelity_sqlite_graph(db_path, runtime_dir)
    return runtime_dir


class TestFallbackLatencySLA:
    """Agent 8: Latency benchmarks and SLA verification under cache-miss / fallback conditions."""

    def test_l1_cache_retrieval_sla(self):
        """Verifies that precomputed L1 cache lookups achieve sub-millisecond p95 latency (<0.2ms)."""
        store = CopilotNeighborhoodStore(engagement_id="project:project_jarvis")
        mock_chains = [
            {
                "chain_id": f"chain_{i}",
                "seed_id": "person:sharon_perez",
                "target_id": "system:sap_erp",
                "path": ["person:sharon_perez", "project:project_nexus", "system:sap_erp"],
                "predicates": ["MEMBER_OF", "OWNS_SYSTEM"],
                "chain_str": "Sharon Perez -> Member -> Project Nexus -> Owns -> SAP ERP",
                "last_claim": "Sharon Perez is technical lead for SAP ERP system",
                "length": 2,
            }
            for i in range(10)
        ]
        store.add_chains(mock_chains)

        latencies = []
        # Warmup
        for _ in range(20):
            store.get_chains_for_node("person:sharon_perez", limit=3)

        # Benchmark 300 iterations
        for _ in range(300):
            t0 = time.perf_counter()
            chains = store.get_chains_for_node("person:sharon_perez", limit=3)
            dt = time.perf_counter() - t0
            latencies.append(dt)
            assert len(chains) > 0

        p50 = statistics.median(latencies)
        p95 = statistics.quantiles(latencies, n=100)[94]

        # p95 must be strictly under 0.2ms (200 microseconds)
        assert p95 < 0.0002, f"L1 Cache p95 latency too high: {p95 * 1000:.3f}ms (p50: {p50 * 1000:.3f}ms)"

    def test_l2_disk_fallback_latency_budget(self, high_fidelity_graph_fixture: Path):
        """
        Verifies that L2 disk/topology fallback (with L1 cache intentionally disabled)
        strictly satisfies the <1.0ms p95 latency ceiling for live meeting operations.
        """
        runtime_dir = high_fidelity_graph_fixture
        status, in_mem, retriever = load_copilot_graph(
            attendees=[{"canonical_name": "Sharon Perez", "role": "attendee"}],
            runtime_root=runtime_dir,
            engagement_id="project:project_jarvis",
        )
        assert retriever is not None

        # Intentionally disable L1 neighborhood store to enforce L2 fallback path
        retriever.neighborhood_store = None

        queries = [
            "SAP ERP",
            "Project Jarvis",
            "Project Nexus",
            "Daniel Brakin",
            "Lone Star EMS",
        ]

        latencies = []
        # Warmup
        for q in queries:
            retriever.retrieve_structured_assertions(q, max_results=5)

        # Benchmark 100 queries across the disk fallback path
        for i in range(100):
            q = queries[i % len(queries)]
            t0 = time.perf_counter()
            results = retriever.retrieve_structured_assertions(q, max_results=5)
            dt = time.perf_counter() - t0
            latencies.append(dt)
            assert len(results) > 0

        p50 = statistics.median(latencies)
        p90 = statistics.quantiles(latencies, n=10)[8]
        p95 = statistics.quantiles(latencies, n=100)[94]

        # L2 fallback SLA ceiling: p95 must be < 1.0ms (0.0010s)
        assert p95 < 0.0010, f"L2 Fallback p95 latency {p95 * 1000:.3f}ms breached 1.0ms SLA ceiling! (p50={p50 * 1000:.3f}ms, p90={p90 * 1000:.3f}ms)"

    def test_fallback_budget_monitoring_and_telemetry(self, high_fidelity_graph_fixture: Path):
        """Verifies that disk fallback operations record metrics into CopilotCacheCircuitBreaker."""
        runtime_dir = high_fidelity_graph_fixture
        cb = get_copilot_circuit_breaker()
        cb.reset()

        status, in_mem, retriever = load_copilot_graph(
            attendees=[{"canonical_name": "Sharon Perez", "role": "attendee"}],
            runtime_root=runtime_dir,
            engagement_id="project:project_jarvis",
        )
        assert retriever is not None
        retriever.neighborhood_store = None  # Enforce fallback

        # Execute 5 queries
        for _ in range(5):
            retriever.retrieve_structured_assertions("SAP ERP", max_results=3)

        snap = cb.get_snapshot()
        assert snap.fallbacks >= 5
        assert snap.total_operations >= 5
        assert snap.fallback_rate > 0.0
