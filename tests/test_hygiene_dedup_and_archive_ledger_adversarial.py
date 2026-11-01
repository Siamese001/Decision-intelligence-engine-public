"""Adversarial and edge-case tests for Graph Hygiene Deduplication & Graph-vs-Archive Posture Ledger."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from dual_pipeline.graph_maintenance_core.posture_ledger import (
    build_deduplication_ledger,
    compute_graph_vs_archive_transition,
    render_dedup_and_archive_markdown,
    snapshot_graph_vs_archive_posture,
)


def test_adversarial_boundary_empty_or_missing_tables(tmp_path: Path) -> None:
    """Category 1 (Boundary payload): Snapshot and ledger handle None connections and empty SQLite schemas without tables."""
    snap_none = snapshot_graph_vs_archive_posture(None)
    assert snap_none["nodes"]["total"] == 0
    assert snap_none["assertions"]["total"] == 0

    db_path = tmp_path / "empty_schema.sqlite"
    conn = sqlite3.connect(str(db_path))
    snap_empty = snapshot_graph_vs_archive_posture(conn)
    assert snap_empty["nodes"]["active_graph"] == 0
    assert snap_empty["search_documents"]["historical_archive"] == 0

    transition = compute_graph_vs_archive_transition(snap_none, snap_empty)
    ledger = build_deduplication_ledger(conn, {"findings": []})
    md = render_dedup_and_archive_markdown(transition, ledger)
    assert "Zero deduplications in this run" in md
    conn.close()


def test_adversarial_malformed_findings_in_deduplication_ledger(tmp_path: Path) -> None:
    """Category 2 (Malformed input): Self-merges, missing IDs, and duplicate entries in findings are safely ignored."""
    db_path = tmp_path / "malformed.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE node (node_id TEXT, canonical_name TEXT, node_type TEXT, active INTEGER)")
    conn.execute("CREATE TABLE assertion (assertion_id TEXT, scope_text TEXT, object_literal TEXT, predicate TEXT)")
    pkg = {
        "findings": [
            {"proposed_fix": "merge_entities", "raw_data": {"node_id_a": "same", "node_id_b": "same"}},
            {"proposed_fix": "merge_entities", "raw_data": {"node_id_a": "", "node_id_b": "b"}},
            {"proposed_fix": "deduplicate_assertions", "raw_data": {"winner_id": "w1", "loser_id": "w1"}},
            {"proposed_fix": "deduplicate_assertions", "raw_data": {"winner_id": "w1", "loser_id": "l1"}},
            {"proposed_fix": "deduplicate_assertions", "raw_data": {"winner_id": "w1", "loser_id": "l1"}},
            {"proposed_fix": "supersede_assertion", "raw_data": {"assertion_id_older": "old1", "assertion_id_newer": "new1", "claim_older": "Old", "claim_newer": "New"}},
        ]
    }
    ledger = build_deduplication_ledger(conn, pkg)
    assert ledger["counts"]["entity_pairs_deduplicated"] == 0
    assert ledger["counts"]["assertions_deduplicated"] == 1
    assert ledger["counts"]["temporal_contradictions_superseded"] == 1
    md = render_dedup_and_archive_markdown(compute_graph_vs_archive_transition({}, {}), ledger)
    assert "Timeline Supersession" in md
    conn.close()


def test_adversarial_minimal_legacy_columns_posture_partition(tmp_path: Path) -> None:
    """Category 5 (Invariant bypass / legacy schema): Tables missing status/currentness columns still satisfy total == active + staging + archive."""
    db_path = tmp_path / "legacy_cols.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE node (node_id TEXT, active INTEGER)")
    conn.execute("INSERT INTO node VALUES ('n1', 1), ('n2', 0)")
    conn.execute("CREATE TABLE assertion (assertion_id TEXT)")
    conn.execute("INSERT INTO assertion VALUES ('a1'), ('a2')")
    conn.execute("CREATE TABLE search_document (search_document_id TEXT)")
    conn.execute("INSERT INTO search_document VALUES ('s1')")
    conn.commit()

    snap = snapshot_graph_vs_archive_posture(conn)
    assert snap["nodes"]["active_graph"] == 1
    assert snap["nodes"]["historical_archive"] == 1
    assert snap["nodes"]["total"] == 2
    assert snap["assertions"]["active_graph"] == 2
    assert snap["search_documents"]["active_graph"] == 1
    conn.close()
