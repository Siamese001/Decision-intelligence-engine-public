"""Adversarial and boundary tests for Wave 5 CLI operational commands.

Covers:
1. Non-existent, corrupted, or missing inputs for graph-diff, graph-stats, graph-gc.
2. Idempotent no-op and identical-snapshot diff detection.
3. Read-only connection safety and lock contention boundaries.
4. GC dry-run vs execute deletion semantics.
5. Ingestion --mutate-graph notice emission when false.
"""
from __future__ import annotations

import io
import sqlite3
import sys
from pathlib import Path

import pytest

from dual_pipeline.cli.operational import (
    _resolve_graph_path,
    execute_graph_diff,
    execute_graph_gc,
    execute_graph_stats,
)
from dual_pipeline.cli.parser import build_parser


def _create_minimal_test_graph(db_path: Path) -> Path:
    """Create a minimal valid graph database with nodes and assertions."""
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT,
            node_type TEXT,
            active INTEGER DEFAULT 1
        )
    """)
    cur.execute("""
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_node_id TEXT,
            claim_text TEXT,
            canonical_status TEXT DEFAULT 'canonical',
            currentness_state TEXT DEFAULT 'current',
            assertion_type TEXT DEFAULT 'confirmed_canonical_fact',
            confidence_score REAL DEFAULT 0.95,
            superseded_by_assertion_id TEXT,
            superseded_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE edge (
            edge_id TEXT PRIMARY KEY,
            from_node_id TEXT,
            to_node_id TEXT,
            edge_type TEXT,
            superseded_by TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY
        )
    """)
    cur.execute("""
        CREATE TABLE source_version (
            source_version_id TEXT PRIMARY KEY
        )
    """)
    cur.execute("""
        CREATE TABLE extraction_run (
            run_id TEXT PRIMARY KEY
        )
    """)
    cur.execute("""
        CREATE TABLE evidence_item (
            evidence_id TEXT PRIMARY KEY
        )
    """)
    cur.execute("""
        CREATE TABLE assertion_evidence (
            assertion_id TEXT,
            evidence_id TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE assertion_conflict (
            conflict_id TEXT PRIMARY KEY
        )
    """)
    cur.execute("""
        CREATE TABLE assertion_predicate_registry (
            predicate TEXT PRIMARY KEY,
            active INTEGER DEFAULT 1
        )
    """)
    cur.execute("""
        CREATE TABLE edge_type_registry (
            edge_type TEXT PRIMARY KEY,
            registry_state TEXT DEFAULT 'active'
        )
    """)
    cur.execute("""
        CREATE TABLE edge_type_domain_range (
            id TEXT PRIMARY KEY
        )
    """)
    cur.execute("""
        CREATE TABLE search_document (
            document_id TEXT PRIMARY KEY,
            object_type TEXT,
            canonical_status TEXT DEFAULT 'candidate',
            retrieval_eligible INTEGER DEFAULT 1
        )
    """)
    cur.execute("""
        CREATE TABLE canonical_alias (
            alias_id TEXT PRIMARY KEY,
            ambiguity_state TEXT DEFAULT 'unique'
        )
    """)
    cur.execute("""
        CREATE TABLE entity_merge_event (id TEXT PRIMARY KEY)
    """)
    cur.execute("""
        CREATE TABLE entity_redirect (id TEXT PRIMARY KEY)
    """)
    cur.execute("""
        CREATE TABLE entity_resolution_candidate (
            id TEXT PRIMARY KEY,
            status TEXT DEFAULT 'pending_human_review'
        )
    """)
    cur.execute("""
        CREATE TABLE node_state (
            node_id TEXT PRIMARY KEY,
            intentional_orphan INTEGER DEFAULT 0,
            approval_state TEXT DEFAULT 'APPROVED'
        )
    """)
    cur.execute("""
        CREATE TABLE claim (
            claim_id TEXT PRIMARY KEY,
            superseded_by TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE card (card_id TEXT PRIMARY KEY)
    """)
    cur.execute("""
        CREATE TABLE node_card_map (id TEXT PRIMARY KEY)
    """)
    cur.execute("""
        CREATE TABLE ingestion_run (id TEXT PRIMARY KEY)
    """)
    cur.execute("""
        CREATE TABLE promotion_event (id TEXT PRIMARY KEY)
    """)
    cur.execute("""
        CREATE TABLE audit_event (event_id TEXT PRIMARY KEY)
    """)
    cur.execute("""
        CREATE TABLE governance_approval (approval_id TEXT PRIMARY KEY)
    """)
    cur.execute("""
        CREATE TABLE validation_queue (id TEXT PRIMARY KEY, status TEXT DEFAULT 'open')
    """)
    cur.execute("""
        CREATE TABLE quarantine_item (id TEXT PRIMARY KEY, status TEXT DEFAULT 'quarantined')
    """)
    cur.execute("""
        CREATE TABLE candidate_entity (entity_id TEXT PRIMARY KEY)
    """)
    cur.execute("""
        CREATE TABLE candidate_assertion (assertion_id TEXT PRIMARY KEY)
    """)
    cur.execute("""
        CREATE TABLE decisions (
            decision_id TEXT PRIMARY KEY,
            stale_after TEXT
        )
    """)

    # Seed data
    cur.execute("INSERT INTO node VALUES ('node_1', 'Node One', 'org', 1)")
    cur.execute("INSERT INTO node VALUES ('node_orphan', 'Orphan Node', 'org', 1)")
    cur.execute("INSERT INTO node_state VALUES ('node_1', 0, 'APPROVED')")
    cur.execute("INSERT INTO node_state VALUES ('node_orphan', 0, 'APPROVED')")
    cur.execute("INSERT INTO assertion VALUES ('asrt_1', 'node_1', 'HAS_TYPE', NULL, 'Enterprise', 'canonical', 'current', 'confirmed_canonical_fact', 0.95, NULL, NULL)")
    cur.execute("INSERT INTO decisions VALUES ('dec_stale', '2020-01-01')")
    conn.commit()
    conn.close()
    return db_path


def test_resolve_graph_path_missing_fails_closed(tmp_path: Path):
    """Missing explicit graph path must raise FileNotFoundError."""
    missing = tmp_path / "non_existent.sqlite"
    with pytest.raises(FileNotFoundError, match="Graph database file not found"):
        _resolve_graph_path(missing)


def test_graph_diff_missing_candidate_fails_closed(tmp_path: Path):
    """graph-diff with missing candidate must raise FileNotFoundError."""
    base = _create_minimal_test_graph(tmp_path / "base.sqlite")
    missing_candidate = tmp_path / "missing_cand.sqlite"

    parser = build_parser()
    args = parser.parse_args(["graph-diff", "--baseline", str(base), "--candidate", str(missing_candidate)])
    with pytest.raises(FileNotFoundError, match="Graph database file not found"):
        execute_graph_diff(args)


def test_graph_diff_identical_graphs_is_idempotent_noop(tmp_path: Path):
    """Diffing identical graph snapshots returns is_noop=True with matching hashes."""
    base = _create_minimal_test_graph(tmp_path / "base.sqlite")
    parser = build_parser()
    args = parser.parse_args(["graph-diff", "--baseline", str(base), "--candidate", str(base), "--json"])
    result = execute_graph_diff(args)

    assert result["status"] == "PASS"
    assert result["is_noop"] is True
    assert result["graph_sha256_before"] == result["graph_sha256_after"]
    for row in result["granular_delta_table"]:
        assert row["delta"] == 0


def test_graph_stats_missing_graph_fails_closed(tmp_path: Path):
    """graph-stats on non-existent file fails closed."""
    missing = tmp_path / "absent.sqlite"
    parser = build_parser()
    args = parser.parse_args(["graph-stats", "--graph", str(missing)])
    with pytest.raises(FileNotFoundError):
        execute_graph_stats(args)


def test_graph_stats_read_only_execution(tmp_path: Path):
    """graph-stats operates read-only and does not modify source database."""
    base = _create_minimal_test_graph(tmp_path / "base.sqlite")
    initial_mtime = base.stat().st_mtime_ns

    parser = build_parser()
    args = parser.parse_args(["graph-stats", "--graph", str(base), "--json"])
    result = execute_graph_stats(args)

    assert result["status"] == "PASS"
    assert result["table_counts"]["node"]["row_count"] == 2
    assert result["table_counts"]["assertion"]["row_count"] == 1
    assert base.stat().st_mtime_ns == initial_mtime


def test_graph_gc_dry_run_preserves_entities(tmp_path: Path):
    """graph-gc in dry-run mode identifies orphans and stale entities but does not delete them."""
    base = _create_minimal_test_graph(tmp_path / "gc_test.sqlite")
    parser = build_parser()
    args = parser.parse_args(["graph-gc", "--graph", str(base), "--dry-run", "--as-of", "2026-09-01"])
    result = execute_graph_gc(args)

    assert result["status"] == "PASS"
    assert result["dry_run"] is True
    assert result["stale_count"] == 1
    assert result["orphan_nodes_count"] == 1

    # Verify rows still exist in graph
    conn = sqlite3.connect(str(base))
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM node WHERE node_id = 'node_orphan'")
    assert cur.fetchone()[0] == 1
    cur.execute("SELECT COUNT(*) FROM decisions WHERE decision_id = 'dec_stale'")
    assert cur.fetchone()[0] == 1
    conn.close()


def test_graph_gc_execute_archives_entities(tmp_path: Path):
    """graph-gc with --execute removes stale and orphan entities and records them in archive tables."""
    base = _create_minimal_test_graph(tmp_path / "gc_exec.sqlite")
    parser = build_parser()
    args = parser.parse_args(["graph-gc", "--graph", str(base), "--execute", "--as-of", "2026-09-01"])
    result = execute_graph_gc(args)

    assert result["status"] == "PASS"
    assert result["dry_run"] is False
    assert result["total_archived"] == 2

    conn = sqlite3.connect(str(base))
    cur = conn.cursor()
    # Should be deleted from primary tables
    cur.execute("SELECT COUNT(*) FROM node WHERE node_id = 'node_orphan'")
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT COUNT(*) FROM decisions WHERE decision_id = 'dec_stale'")
    assert cur.fetchone()[0] == 0
    # Should be present in archive tables
    cur.execute("SELECT COUNT(*) FROM archived_node WHERE node_id = 'node_orphan'")
    assert cur.fetchone()[0] == 1
    cur.execute("SELECT COUNT(*) FROM archived_decisions WHERE decision_id = 'dec_stale'")
    assert cur.fetchone()[0] == 1
    conn.close()


def test_ingest_dry_run_notice_emission_on_false_mutate(monkeypatch):
    """When --mutate-graph is False, ingestion must visibly emit dry-run notice to stderr."""
    import argparse

    from dual_pipeline.cli.ingest import ingest_canonical

    parser = argparse.ArgumentParser()
    parser.add_argument("--mutate-graph", action="store_true", default=False)
    args = parser.parse_args([])
    args.input = "dummy.txt"

    captured_stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", captured_stderr)

    with pytest.raises(Exception):
        ingest_canonical(args)

    assert "[NOTICE] --mutate-graph is FALSE. Running in dry-run/preview mode." in captured_stderr.getvalue()
