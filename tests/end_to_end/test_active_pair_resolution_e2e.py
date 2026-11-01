"""End-to-End test suite for single active-pair resolution.

Verifies:
1. The authoritative active pair resolves identically across subsystems.
2. Single-hop runtime pointers resolve to target generation transparently.
3. Query, Copilot, and Ingestion subsystems resolve the exact same graph path.
4. ReleaseManager and ReadOnlyGraphSnapshot handle pointer-based runtimes.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from dual_pipeline.canonical_pipeline.publication_bindings import (
    canonical_publication_runtime,
)
from dual_pipeline.cli.operational import _resolve_graph_path
from dual_pipeline.grounding.snapshot import ReadOnlyGraphSnapshot, resolve_scoped_active_pair
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    GraphLoadStatus,
    load_copilot_graph,
)
from dual_pipeline.release_core.manager import ReleaseManager
from dual_pipeline.runtime_root import (
    _compute_sha256,
    resolve_active_graph_path,
    resolve_active_pair,
    resolve_authoritative_runtime_root,
)


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_authoritative_active_pair_resolves(repo_root: Path) -> None:
    """Verify that default resolve_active_pair resolves the live .local active pair."""
    pair_data, pair_path = resolve_active_pair()
    assert pair_path.is_file()
    assert pair_data["generation_id"]
    assert pair_data["graph_sha256"]

    graph_file = resolve_active_graph_path()
    assert graph_file.is_file()
    assert _compute_sha256(graph_file) == pair_data["graph_sha256"]


def test_subsystems_resolve_same_graph_path(repo_root: Path) -> None:
    """Acceptance criterion: query, copilot, and ingest-document resolve the same graph path."""
    active_pair_data, _ = resolve_active_pair()
    expected_sha = active_pair_data["graph_sha256"]

    # 1. Operational / Query graph resolution
    query_graph_path = _resolve_graph_path(None, allow_seed=False)
    assert query_graph_path.is_file()
    assert _compute_sha256(query_graph_path) == expected_sha

    # 2. Ingestion publication runtime resolution
    pub_runtime = canonical_publication_runtime(repo_root)
    pub_graph_path = resolve_active_graph_path(pub_runtime)
    assert pub_graph_path.resolve() == query_graph_path.resolve()

    # 3. Copilot graph resolution
    status, in_mem, retriever = load_copilot_graph()
    assert status == GraphLoadStatus.LOADED_ACTIVE_PAIR
    assert retriever is not None
    assert retriever.graph_sha256 == expected_sha


def test_single_hop_pointer_resolution_e2e() -> None:
    """Verify end-to-end resolution through a single-hop runtime pointer."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_root = Path(tmp_dir)
        target_root = tmp_root / "authoritative_store"
        target_root.mkdir(parents=True)
        gen_dir = target_root / "generations" / "gen-e2e-01"
        gen_dir.mkdir(parents=True)

        graph_db = gen_dir / "graph.sqlite"
        conn = sqlite3.connect(graph_db)
        conn.execute("CREATE TABLE test_node (id TEXT PRIMARY KEY, label TEXT);")
        conn.execute("INSERT INTO test_node VALUES ('n1', 'Test Node 1');")
        conn.commit()
        conn.close()

        graph_sha = _compute_sha256(graph_db)
        target_pair = {
            "schema_version": "1.0.0",
            "generation_id": "gen-e2e-01",
            "graph_path": "generations/gen-e2e-01/graph.sqlite",
            "graph_sha256": graph_sha,
            "sidecar_path": None,
            "embedding_backend": "flagembedding-bge-m3-dev",
        }
        (target_root / "active_pair.json").write_text(json.dumps(target_pair), encoding="utf-8")

        # Create secondary runtime containing a pointer
        pointer_root = tmp_root / "secondary_runtime"
        pointer_root.mkdir(parents=True)
        pointer_data = {
            "$schema": "runtime-pointer:1.0.0",
            "target": str(target_root / "active_pair.json"),
        }
        (pointer_root / "active_pair.json").write_text(json.dumps(pointer_data), encoding="utf-8")

        # Resolve through pointer
        resolved_data, resolved_file = resolve_active_pair(pointer_root)
        assert resolved_data["generation_id"] == "gen-e2e-01"
        assert resolved_file == (target_root / "active_pair.json").resolve()

        resolved_graph = resolve_active_graph_path(pointer_root)
        assert resolved_graph == graph_db.resolve()

        # ReleaseManager resolves through pointer
        rm = ReleaseManager(pointer_root)
        assert rm.seed_graph == graph_db.resolve()

        # ReadOnlyGraphSnapshot works through pointer
        snapshot = ReadOnlyGraphSnapshot(pointer_root, enforce_pair_integrity=True)
        assert snapshot.graph_path == graph_db.resolve()
        conn = snapshot.connect()
        cur = conn.cursor()
        cur.execute("SELECT label FROM test_node WHERE id='n1';")
        row = cur.fetchone()
        assert row[0] == "Test Node 1"
        conn.close()
