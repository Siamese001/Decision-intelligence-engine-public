"""E2E test suite for governed SQLite GraphDB mutation and BGE-M3 vector indexing."""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest

from dual_pipeline.vector_indexing.bge_m3_org_indexer import (
    apply_visual_org_projection_to_graph,
    inspect_org_graph_integrity,
)
from storage.connection import configure_pragmas, create_sqlite_conn

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_GRAPH = ROOT / "graph" / "nee_graph.sqlite"
PROJ_DIR = ROOT / "reports" / "visual_projections"


@pytest.fixture
def isolated_graph_db(tmp_path: Path) -> Path:
    """Provision a fresh isolated SQLite database from template under WAL mode."""
    target_db = tmp_path / "test_graph.sqlite"
    shutil.copy2(TEMPLATE_GRAPH, target_db)
    conn = create_sqlite_conn(target_db, readonly=False)
    configure_pragmas(conn, readonly=False)
    conn.close()
    return target_db


def test_graphdb_mutation_from_visual_projections_e2e(isolated_graph_db: Path):
    """Verify all 8 visual projections mutate SQLite graph correctly under WAL mode."""
    proj_files = sorted(PROJ_DIR.glob("*_projection.json"))
    assert len(proj_files) >= 8, f"Expected at least 8 projection files, found {len(proj_files)}"

    total_nodes_upserted = 0
    total_edges_upserted = 0

    for pf in proj_files:
        proj = json.loads(pf.read_text(encoding="utf-8"))
        res = apply_visual_org_projection_to_graph(isolated_graph_db, proj)
        total_nodes_upserted += res["nodes_upserted"]
        total_edges_upserted += res["edges_upserted"]

    # Verify counts and integrity
    stats = inspect_org_graph_integrity(isolated_graph_db)
    assert stats["stakeholder_nodes"] >= 25, f"Expected >= 25 stakeholder nodes, got {stats['stakeholder_nodes']}"
    assert stats["org_nodes"] >= 5, f"Expected >= 5 org nodes, got {stats['org_nodes']}"
    assert stats["reports_to_edges"] >= 10, f"Expected >= 10 REPORTS_TO edges, got {stats['reports_to_edges']}"
    assert stats["leadership_edges"] >= 5, f"Expected >= 5 leadership edges, got {stats['leadership_edges']}"
    assert stats["orphan_stakeholders"] == 0, "No orphaned stakeholders allowed"
    assert stats["integrity_passed"] is True


def test_graphdb_mutation_idempotence(isolated_graph_db: Path):
    """Verify that re-applying the same projections achieves strict idempotency with +0 growth."""
    proj_files = sorted(PROJ_DIR.glob("*_projection.json"))
    for pf in proj_files:
        proj = json.loads(pf.read_text(encoding="utf-8"))
        apply_visual_org_projection_to_graph(isolated_graph_db, proj)

    stats_first = inspect_org_graph_integrity(isolated_graph_db)

    # Re-apply all projections a second time
    for pf in proj_files:
        proj = json.loads(pf.read_text(encoding="utf-8"))
        apply_visual_org_projection_to_graph(isolated_graph_db, proj)

    stats_second = inspect_org_graph_integrity(isolated_graph_db)
    assert stats_first["stakeholder_nodes"] == stats_second["stakeholder_nodes"], "Replay must not duplicate stakeholder nodes"
    assert stats_first["org_nodes"] == stats_second["org_nodes"], "Replay must not duplicate org nodes"
    assert stats_first["reports_to_edges"] == stats_second["reports_to_edges"], "Replay must not duplicate edges"



def test_wal_concurrency_zero_lockups(isolated_graph_db: Path):
    """Verify zero database locks during concurrent reads and writes across worker threads."""
    proj_files = sorted(PROJ_DIR.glob("*_projection.json"))
    projections = [json.loads(pf.read_text(encoding="utf-8")) for pf in proj_files]

    def _worker(idx: int):
        proj = projections[idx % len(projections)]
        # Read operation
        conn_r = create_sqlite_conn(isolated_graph_db, readonly=True)
        configure_pragmas(conn_r, readonly=True)
        cur = conn_r.cursor()
        cur.execute("SELECT count(*) FROM node")
        _ = cur.fetchone()[0]
        conn_r.close()

        # Write operation
        apply_visual_org_projection_to_graph(isolated_graph_db, proj)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_worker, i) for i in range(16)]
        for f in futures:
            f.result()  # Will raise OperationalError if locks occur


def test_hierarchical_neighborhood_query(isolated_graph_db: Path):
    """Verify multi-hop reporting hierarchy queries via relational edges."""
    proj_files = sorted(PROJ_DIR.glob("*_projection.json"))
    for pf in proj_files:
        proj = json.loads(pf.read_text(encoding="utf-8"))
        apply_visual_org_projection_to_graph(isolated_graph_db, proj)

    conn = create_sqlite_conn(isolated_graph_db, readonly=True)
    try:
        cur = conn.cursor()
        # Who does Scott Herold report to?
        cur.execute(
            """SELECT n.canonical_name, e.edge_type, m.canonical_name
               FROM edge e
               JOIN node n ON n.node_id = e.from_node_id
               JOIN node m ON m.node_id = e.to_node_id
               WHERE e.from_node_id = 'person:scott-herold' AND e.edge_type = 'REPORTS_TO'"""
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "Scott Herold"
        assert row[2] == "Rianne Donst"

        # 2-hop traversal: Scott Herold -> Rianne Donst -> Chakra Sankaraiah
        cur.execute(
            """SELECT m2.canonical_name
               FROM edge e1
               JOIN edge e2 ON e2.from_node_id = e1.to_node_id AND e2.edge_type = 'REPORTS_TO'
               JOIN node m2 ON m2.node_id = e2.to_node_id
               WHERE e1.from_node_id = 'person:scott-herold' AND e1.edge_type = 'REPORTS_TO'"""
        )
        row2 = cur.fetchone()
        assert row2 is not None
        assert row2[0] == "Chakra Sankaraiah"
    finally:
        conn.close()


def test_vector_sidecar_synchronization_e2e(isolated_graph_db: Path, tmp_path: Path):
    """Verify BGE-M3 sidecar synchronization over mutated knowledge graph."""
    import os
    if os.environ.get("NEE_RUN_BGE_TESTS") != "1":
        pytest.skip("Set NEE_RUN_BGE_TESTS=1 to run full real BGE-M3 sidecar build")

    from dual_pipeline.vector_indexing.bge_m3_org_indexer import synchronize_bge_m3_org_sidecar
    from tests.embedding_fixture import BgeM3TestBackend

    proj_files = sorted(PROJ_DIR.glob("*_projection.json"))
    for pf in proj_files:
        proj = json.loads(pf.read_text(encoding="utf-8"))
        apply_visual_org_projection_to_graph(isolated_graph_db, proj)

    sidecar_path = tmp_path / "sidecar.sqlite"
    backend = BgeM3TestBackend()
    receipt = synchronize_bge_m3_org_sidecar(
        isolated_graph_db, sidecar_path, generation_id="gen-visual-org-test", backend=backend
    )

    assert receipt["validation"]["status"] == "PASS"
    assert receipt["validation"]["integrity"]["integrity_check"] == "ok"
    assert int(receipt["validation"]["metadata"]["dense_dimensions"]) == 1024
    assert int(receipt["validation"]["metadata"]["colbert_dimensions"]) == 1024
    assert sidecar_path.exists()

