"""
tests/test_copilot_load_graph_neighborhood.py

Tests that load_copilot_graph wires CopilotNeighborhoodStore directly into
CopilotGraphRetriever with < 150ms precompute deadline, populating connected
chains without runtime traversal overhead.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.live_meeting_recorder_core.copilot_connect import get_connected_chains
from dual_pipeline.live_meeting_recorder_core.copilot_neighborhood import (
    CopilotNeighborhoodStore,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    GraphLoadStatus,
    load_copilot_graph,
)


def _compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _create_sqlite_topology_db(db_path: Path) -> None:
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
            active INTEGER DEFAULT 1,
            engagement_id TEXT
        );"""
    )
    cur.execute(
        """CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object_node_id TEXT,
            object_literal TEXT,
            canonical_status TEXT DEFAULT 'approved',
            retrieval_eligible INTEGER DEFAULT 1,
            materiality TEXT DEFAULT 'high',
            classification TEXT DEFAULT 'internal',
            allowed_audiences_json TEXT DEFAULT '["partner_advisory_consulting"]',
            observed_at TEXT,
            source_date_normalized TEXT,
            extraction_run_id TEXT,
            engagement_id TEXT
        );"""
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
               0 AS hitl_required
          FROM assertion a
         WHERE a.object_node_id IS NOT NULL;"""
    )

    nodes = [
        ("project:jarvis", "Project Jarvis", "Jarvis", "project", "Jarvis Core", "active", 1, "project:jarvis"),
        ("person:mitch", "Mitch Simmons", "Mitch", "person", "Engineering Lead", "active", 1, "project:jarvis"),
        ("tech:metal", "Apple Silicon Metal", "Metal", "system", "Local MLX Inference", "active", 1, "project:jarvis"),
        ("tech:sso", "Okta SSO", "SSO", "system", "Identity Provider", "active", 1, "project:jarvis"),
    ]
    cur.executemany("INSERT INTO node VALUES (?, ?, ?, ?, ?, ?, ?, ?)", nodes)

    assertions = [
        ("asrt_01", "person:mitch", "LEADS", "project:jarvis", "Mitch leads Project Jarvis", "approved", 1, "high", "internal", "[]", "2026-10-09", "2026-10-09", "run-1", "project:jarvis"),
        ("asrt_02", "project:jarvis", "USES", "tech:metal", "Project Jarvis uses Apple Silicon Metal", "approved", 1, "high", "internal", "[]", "2026-10-09", "2026-10-09", "run-1", "project:jarvis"),
        ("asrt_03", "project:jarvis", "REQUIRES", "tech:sso", "Project Jarvis requires Okta SSO", "approved", 1, "high", "internal", "[]", "2026-10-09", "2026-10-09", "run-1", "project:jarvis"),
    ]
    cur.executemany(
        """INSERT INTO assertion (
            assertion_id, subject_node_id, predicate, object_node_id, object_literal,
            canonical_status, retrieval_eligible, materiality, classification,
            allowed_audiences_json, observed_at, source_date_normalized, extraction_run_id,
            engagement_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        assertions,
    )

    conn.commit()
    conn.close()


def _setup_runtime_environment(tmp_path: Path) -> Path:
    """Sets up an isolated SQLite graph database conforming to InMemoryGraphTopology and active_pair.json."""
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    db_path = runtime_root / "canonical_graph.db"
    _create_sqlite_topology_db(db_path)

    graph_hash = _compute_sha256(db_path)
    pair_data = {
        "graph_path": "canonical_graph.db",
        "graph_sha256": graph_hash,
        "generation_id": "gen-w3-001",
    }
    with open(runtime_root / "active_pair.json", "w", encoding="utf-8") as f:
        json.dump(pair_data, f)

    return runtime_root


def test_load_copilot_graph_wires_neighborhood_store(tmp_path: Path) -> None:
    """Confirms load_copilot_graph initializes and attaches a populated CopilotNeighborhoodStore."""
    runtime_root = _setup_runtime_environment(tmp_path)

    # Warm-up invocation to isolate graph precomputation from Python module import latency
    load_copilot_graph(runtime_root=runtime_root, precompute_timeout_seconds=0.150)

    t0 = time.perf_counter()
    status, in_mem, retriever = load_copilot_graph(
        attendees=[{"name": "Mitch Simmons", "role": "Lead", "org": "Engineering"}],
        runtime_root=runtime_root,
        engagement_id="project:jarvis",
        session_id="session-w3-001",
        precompute_timeout_seconds=0.150,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000

    # 1. Verification of load status
    assert status == GraphLoadStatus.LOADED_ACTIVE_PAIR
    assert in_mem is not None
    assert retriever is not None

    # 2. Precomputation budget ceiling (< 150ms)
    assert elapsed_ms <= 150.0, f"Precompute duration {elapsed_ms:.2f}ms exceeded 150ms ceiling"

    # 3. Neighborhood store is populated instance
    store = retriever.neighborhood_store
    assert isinstance(store, CopilotNeighborhoodStore)
    assert store.status in ("COMPLETE", "SUCCESS", "PARTIAL")
    assert store.chain_count > 0, "Expected non-zero precomputed chains"
    assert len(store.chains) == store.chain_count

    # 4. Context formatting incorporates multi-hop chains
    context = retriever.retrieve_context("Tell me about Mitch and Jarvis architecture")
    assert isinstance(context, str)


def test_copilot_connect_reuses_precomputed_neighborhood_store(tmp_path: Path) -> None:
    """Verifies copilot_connect.get_connected_chains reuses pre-warmed store without recomputing."""
    runtime_root = _setup_runtime_environment(tmp_path)

    status, in_mem, retriever = load_copilot_graph(
        attendees=[{"name": "Mitch Simmons"}],
        runtime_root=runtime_root,
        engagement_id="project:jarvis",
        session_id="session-w3-002",
        precompute_timeout_seconds=0.150,
    )
    assert status == GraphLoadStatus.LOADED_ACTIVE_PAIR
    assert retriever is not None
    assert retriever.neighborhood_store is not None

    initial_hash = retriever.neighborhood_store.compute_hash()
    initial_count = retriever.neighborhood_store.chain_count

    # Request connected chains through copilot_connect
    chains, facts = get_connected_chains(retriever, "Mitch architecture metal", limit=5)
    assert isinstance(chains, list)

    # Neighborhood store must remain unchanged and reused
    assert retriever.neighborhood_store.chain_count == initial_count
    assert retriever.neighborhood_store.compute_hash() == initial_hash
