"""Adversarial & Edge-Case tests for Wave 5: Graph Writer FTS Sync, Proof Gate & Yield Gate.

Covers mandatory edge-case categories:
1. Boundary payload: header-heavy transcript rejected by _validate_semantic_yield when dialogue body < min_words.
2. Rollback & recovery / state sync: build_graph_mutation_summary computes distinct extraction_run, assertion_evidence, and search_fts deltas.
3. Invariant bypass attempt: MutationGuard table-specific readback probes fail closed when target assertion or node row is missing from primary table despite audit_event presence.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
import pytest

from dual_pipeline.exceptions import ZeroSignalError
from dual_pipeline.graph_delta_core.summary import build_graph_mutation_summary
from dual_pipeline.graph_mutation_proof import GraphMutationProofError, MutationGuard
from tools.prepare_evidence_envelope_intake import _validate_semantic_yield


def test_adversarial_boundary_header_heavy_transcript_rejected_by_semantic_yield(tmp_path: Path) -> None:
    """Boundary payload: transcript with >50 header words but <20 dialogue words must fail closed."""
    header_heavy = (
        "# Executive Architecture Sync Meeting Transcript With Very Long Header Title Words And Extra Tokens\n"
        "**Date:** 04/06/2026\n"
        "**Attendees:** Alice Smith, Bob Jones, Charlie Brown, Diana Prince, Evan Wright, Frank Miller, Grace Hopper\n"
        "**Project:** Enterprise Knowledge Graph Platform Architecture Modernization And Governance Program\n"
        "| Column A | Column B | Column C |\n"
        "|---|---|---|\n"
        "**Alice:** Hello team.\n"
    )
    src_path = tmp_path / "header_heavy.md"
    src_path.write_text(header_heavy, encoding="utf-8")
    with pytest.raises(ZeroSignalError, match="below minimum semantic yield threshold"):
        _validate_semantic_yield(src_path, min_words=20)


def test_adversarial_mutation_guard_table_specific_probes_fail_when_primary_row_missing(tmp_path: Path) -> None:
    """Invariant bypass attempt: audit_event exists for target_id, but node/assertion row was not written."""
    db_path = tmp_path / "test_graph.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE audit_event (audit_event_id TEXT PRIMARY KEY, record_id TEXT)")
    conn.execute("CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT)")
    conn.execute("CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY, predicate TEXT)")
    conn.commit()
    conn.close()

    guard = MutationGuard(db_path, label="test-bypass-probe")
    conn = sqlite3.connect(str(db_path))
    # Insert ONLY into audit_event, omitting the actual node row!
    conn.execute("INSERT INTO audit_event VALUES ('aud-1', 'entity-missing-node-001')")
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()

    with pytest.raises(GraphMutationProofError, match="PHYSICAL_PROOF_FAILED"):
        guard.finalize(
            record_ids=["entity-missing-node-001"],
            audit_events=1,
            operation_targets=[("create_entity", "entity-missing-node-001")],
        )


def test_adversarial_mutation_guard_passes_when_primary_and_audit_rows_exist(tmp_path: Path) -> None:
    """Verify MutationGuard passes when both audit_event and primary table rows exist on disk."""
    db_path = tmp_path / "test_graph_valid.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE audit_event (audit_event_id TEXT PRIMARY KEY, record_id TEXT)")
    conn.execute("CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT)")
    conn.execute("CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY, predicate TEXT)")
    conn.commit()
    conn.close()

    guard = MutationGuard(db_path, label="test-valid-probe")
    conn = sqlite3.connect(str(db_path))
    conn.execute("INSERT INTO audit_event VALUES ('aud-1', 'entity-valid-node-001')")
    conn.execute("INSERT INTO node VALUES ('entity-valid-node-001', 'Valid Node')")
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()

    proof = guard.finalize(
        record_ids=["entity-valid-node-001"],
        audit_events=1,
        operation_targets=[("create_entity", "entity-valid-node-001")],
    )
    assert proof["overall"] == "PASS"


def test_adversarial_graph_mutation_summary_measures_distinct_table_deltas(tmp_path: Path) -> None:
    """Verify build_graph_mutation_summary computes distinct extraction_run, assertion_evidence, and search_fts deltas."""
    before_db = tmp_path / "before.sqlite"
    after_db = tmp_path / "after.sqlite"

    for p in (before_db, after_db):
        c = sqlite3.connect(str(p))
        c.execute("CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT, node_type TEXT)")
        c.execute("CREATE TABLE source (source_id TEXT PRIMARY KEY, source_name TEXT)")
        c.execute("CREATE TABLE extraction_run (run_id TEXT PRIMARY KEY)")
        c.execute("CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT)")
        c.execute("CREATE TABLE search_document (search_document_id TEXT PRIMARY KEY)")
        c.execute("CREATE TABLE search_fts (rowid INTEGER, title TEXT, body TEXT, object_type TEXT, object_id TEXT)")
        c.commit()
        c.close()

    c = sqlite3.connect(str(after_db))
    c.execute("INSERT INTO extraction_run VALUES ('run-1')")
    c.execute("INSERT INTO assertion_evidence VALUES ('a-1', 'ev-1')")
    c.execute("INSERT INTO assertion_evidence VALUES ('a-1', 'ev-2')")
    c.execute("INSERT INTO assertion_evidence VALUES ('a-2', 'ev-3')")
    c.execute("INSERT INTO search_document VALUES ('sd-1')")
    c.execute("INSERT INTO search_document VALUES ('sd-2')")
    c.execute("INSERT INTO search_fts VALUES (1, 't1', 'b1', 'assertion', 'a-1')")
    c.execute("INSERT INTO search_fts VALUES (2, 't2', 'b2', 'assertion', 'a-2')")
    c.commit()
    c.close()

    actual_graph_delta = {
        "table": [
            {"component_key": "source", "actual_delta": 1},
            {"component_key": "source_version", "actual_delta": 2},
            {"component_key": "evidence", "actual_delta": 5},
            {"component_key": "canonical_assertion", "actual_delta": 2},
            {"component_key": "candidate_search_document", "actual_delta": 0},
            {"component_key": "audit_event", "actual_delta": 4},
        ]
    }
    summary = build_graph_mutation_summary(
        before_graph_path=before_db,
        after_graph_path=after_db,
        actual_graph_delta=actual_graph_delta,
        total_operations=4,
    )
    assert summary["net_changes"]["extraction_runs"] == 1
    assert summary["net_changes"]["assertion_evidence"] == 3
    assert summary["net_changes"]["search_documents"] == 2
    assert summary["net_changes"]["search_fts"] == 2
