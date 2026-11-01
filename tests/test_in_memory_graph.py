"""Unit tests for InMemoryGraphTopology (Tier 3 In-Memory Topological Accelerator)."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from dual_pipeline.in_memory_graph import InMemoryGraphTopology

ACTIVE_GRAPH = Path(
    ".local/main/runtime-bge-m3-sherpa-strategic-20260729/generations/generation-187f65c8102822a78233d1629169/graph.sqlite"
)


@pytest.fixture
def temp_graph_db(tmp_path: Path) -> Path:
    """Create a minimal SQLite graph database for deterministic unit testing."""
    db_path = tmp_path / "test_graph.sqlite"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute(
        """CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT NOT NULL,
            display_name TEXT,
            node_type TEXT NOT NULL,
            description TEXT,
            status TEXT DEFAULT 'active',
            active INTEGER DEFAULT 1
        )"""
    )

    cur.execute(
        """CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object_node_id TEXT,
            object_literal TEXT,
            assertion_type TEXT DEFAULT 'fact',
            atomicity_state TEXT DEFAULT 'atomic',
            canonical_status TEXT DEFAULT 'approved',
            retrieval_eligible INTEGER DEFAULT 1,
            materiality TEXT DEFAULT 'high',
            classification TEXT DEFAULT 'internal',
            allowed_audiences_json TEXT DEFAULT '["partner_advisory_consulting", "nee-engineering"]',
            hitl_required INTEGER DEFAULT 0,
            validation_priority TEXT,
            validation_owner TEXT,
            origin_type TEXT DEFAULT 'legacy_migration',
            extraction_run_id TEXT
        )"""
    )

    cur.execute(
        """CREATE TABLE assertion_evidence (
            assertion_id TEXT NOT NULL,
            evidence_id TEXT NOT NULL,
            evidence_relationship TEXT NOT NULL
        )"""
    )

    cur.execute(
        """CREATE VIEW v_canonical_edge AS
        SELECT a.assertion_id,
               a.subject_node_id AS from_node_id,
               'RELATES_TO' AS relationship_family,
               a.predicate AS edge_type,
               a.object_node_id AS to_node_id,
               a.materiality,
               'test_source' AS source_id,
               a.hitl_required
          FROM assertion a
         WHERE a.object_node_id IS NOT NULL"""
    )

    # Insert test nodes: A -> B -> C, plus isolated node D
    cur.executemany(
        """INSERT INTO node (node_id, canonical_name, display_name, node_type, active)
           VALUES (?, ?, ?, ?, 1)""",
        [
            ("node:A", "Entity Alpha", "Alpha", "initiative"),
            ("node:B", "Entity Beta", "Beta", "project"),
            ("node:C", "Entity Gamma", "Gamma", "risk"),
            ("node:D", "Entity Delta", "Delta", "metric"),
        ],
    )

    # Insert assertions:
    # assert:1: A -> B (high materiality, internal)
    # assert:2: B -> C (critical materiality, internal)
    # assert:3: A -> D (restricted audience mismatch)
    cur.executemany(
        """INSERT INTO assertion (
            assertion_id, subject_node_id, predicate, object_node_id,
            materiality, classification, allowed_audiences_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                "assert:1",
                "node:A",
                "DEPENDS_ON",
                "node:B",
                "high",
                "internal",
                '["partner_advisory_consulting", "nee-engineering"]',
            ),
            (
                "assert:2",
                "node:B",
                "CAUSES_RISK",
                "node:C",
                "critical",
                "internal",
                '["partner_advisory_consulting", "nee-engineering"]',
            ),
            (
                "assert:3",
                "node:A",
                "MEASURED_BY",
                "node:D",
                "medium",
                "confidential",
                '["internal_working_team"]',
            ),
        ],
    )

    cur.executemany(
        """INSERT INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship)
           VALUES (?, ?, ?)""",
        [
            ("assert:1", "ev:doc1:p1", "supports"),
            ("assert:2", "ev:doc2:p5", "supports"),
            ("assert:3", "ev:doc3:p2", "supports"),
        ],
    )

    conn.commit()
    conn.close()
    return db_path


def test_in_memory_graph_hydration_and_metrics(temp_graph_db: Path) -> None:
    t0 = time.perf_counter()
    topology = InMemoryGraphTopology.load_from_sqlite(temp_graph_db)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert elapsed_ms < 50.0  # Fast sub-50ms hydration requirement
    assert len(topology.nodes) == 4
    assert len(topology.assertions) == 3
    assert len(topology.canonical_edges) == 3

    assert topology.node_out_degree["node:A"] == 2
    assert topology.node_in_degree["node:B"] == 1
    assert topology.node_out_degree["node:B"] == 1
    assert topology.node_in_degree["node:C"] == 1
    assert topology.node_out_degree["node:D"] == 0

    assert topology.supporting_evidence["assert:1"] == ["ev:doc1:p1"]


def test_causal_path_discovery(temp_graph_db: Path) -> None:
    topology = InMemoryGraphTopology.load_from_sqlite(temp_graph_db)

    # 2-hop causal path from A to C: A -> B -> C
    paths = topology.find_causal_paths(
        source_ids=["node:A"],
        target_ids=["node:C"],
        max_hops=3,
        direction="forward",
    )
    assert len(paths) == 1
    p = paths[0]
    assert p["hop_count"] == 2
    assert p["node_ids"] == ["node:A", "node:B", "node:C"]
    assert p["assertion_ids"] == ["assert:1", "assert:2"]
    assert p["source_id"] == "node:A"
    assert p["target_id"] == "node:C"


def test_clearance_filtering_blocks_restricted_edges(temp_graph_db: Path) -> None:
    topology = InMemoryGraphTopology.load_from_sqlite(temp_graph_db)

    # When querying with audience "nee-engineering", assert:3 (which requires "internal_working_team")
    # should be filtered out because confidential record cannot be exposed to client audience without proper clearance
    paths_allowed = topology.find_causal_paths(
        source_ids=["node:A"],
        target_ids=["node:D"],
        allowed_audiences=["internal_working_team"],
    )
    # Allowed when matching the audience
    assert len(paths_allowed) == 1

    # Blocked when audience doesn't intersect
    paths_blocked = topology.find_causal_paths(
        source_ids=["node:A"],
        target_ids=["node:D"],
        allowed_audiences=["client_approved_external"],
    )
    assert len(paths_blocked) == 0


def test_personalized_pagerank(temp_graph_db: Path) -> None:
    topology = InMemoryGraphTopology.load_from_sqlite(temp_graph_db)

    # Seed on node:A
    ppr = topology.personalized_pagerank(seed_node_ids=["node:A"], alpha=0.85)
    assert len(ppr) == 4
    # Scores must sum to ~1.0
    assert abs(sum(ppr.values()) - 1.0) < 1e-4

    # Seed node A should have highest or near-highest score, followed by direct neighbors
    assert ppr["node:A"] > ppr["node:C"]
    assert ppr["node:B"] > ppr["node:C"]


def test_extract_induced_subgraph(temp_graph_db: Path) -> None:
    topology = InMemoryGraphTopology.load_from_sqlite(temp_graph_db)

    subgraph = topology.extract_induced_subgraph(["node:A", "node:B", "node:C"])
    assert subgraph["node_count"] == 3
    assert subgraph["edge_count"] == 2
    assert "assert:1" in subgraph["assertion_ids"]
    assert "assert:2" in subgraph["assertion_ids"]
    assert "ev:doc1:p1" in subgraph["evidence_ids"]
    assert "ev:doc2:p5" in subgraph["evidence_ids"]


def test_hydration_on_active_production_generation() -> None:
    if not ACTIVE_GRAPH.exists():
        pytest.skip(f"Active graph not found at {ACTIVE_GRAPH}")

    # Cold hydration with full file SHA-256 computation (< 150ms)
    t0 = time.perf_counter()
    topology_cold = InMemoryGraphTopology.load_from_sqlite(ACTIVE_GRAPH)
    cold_elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert cold_elapsed_ms < 150.0
    summary = topology_cold.summary()
    assert summary["node_count"] >= 680
    assert summary["assertion_count"] >= 1600
    assert summary["canonical_edge_count"] >= 350

    # Fast hydration with known sha256 (< 50ms)
    known_sha256 = summary["graph_sha256"]
    t1 = time.perf_counter()
    topology_fast = InMemoryGraphTopology.load_from_sqlite(
        ACTIVE_GRAPH, expected_sha256=known_sha256
    )
    fast_elapsed_ms = (time.perf_counter() - t1) * 1000.0
    assert fast_elapsed_ms < 60.0

    # Test PPR computation on active graph
    sample_seed = next(iter(topology_fast.nodes.keys()))
    ppr = topology_fast.personalized_pagerank([sample_seed])
    assert len(ppr) == len(topology_fast.nodes)
    assert abs(sum(ppr.values()) - 1.0) < 1e-3
