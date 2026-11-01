"""Adversarial and edge-case tests for Wave 6: Identity Resolution, Fingerprint Dedup & Proof Gate Rollback."""
from __future__ import annotations

from pathlib import Path
import pytest
from storage.database import sqlite3

from dual_pipeline.graph_writer_core.assertions import compute_cross_source_scope_signature
from dual_pipeline.graph_writer_core.proof_gate import run_with_proof_gate
from dual_pipeline.identity_core.matching import determine_resolution_outcome
from dual_pipeline.release_core.delta_rebase import detect_mutation_conflicts


def test_boundary_and_malformed_cross_source_scope_signature() -> None:
    """Category 1 (Boundary payload) & Category 2 (Malformed input): empty/null strings and unicode punctuation."""
    sig_empty_1 = compute_cross_source_scope_signature(None, None, None, "")
    sig_empty_2 = compute_cross_source_scope_signature(None, "HAS_CLAIM_TEXT", None, "   . ; - ")
    assert sig_empty_1 == sig_empty_2

    sig_unicode = compute_cross_source_scope_signature(
        "ent_1", "OWNS", "ent_2", "Client\u2019s cutover \u2014 phase 1 in Sprint Review."
    )
    sig_ascii = compute_cross_source_scope_signature(
        "ent_1", "owns", "ent_2", "Client's cutover - phase 1."
    )
    assert sig_unicode == sig_ascii


def test_invariant_bypass_conflicting_entity_type_blocks_idempotent_rebase(tmp_path: Path) -> None:
    """Category 5 (Invariant bypass attempt): idempotent_upsert must still block when node_type or canonical_name differs."""
    db_path = tmp_path / "graph.sqlite"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT, node_type TEXT, active INTEGER)"
        )
        conn.execute(
            "CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY, currentness_state TEXT, superseded_at TEXT, superseded_by_assertion_id TEXT)"
        )
        conn.execute("INSERT INTO node VALUES ('ent_person_1', 'Alex Rivera', 'person', 1)")
        conn.commit()
    finally:
        conn.close()

    spoofed_change_set = {
        "operations": [
            {
                "operation_id": "op_spoof",
                "operation": "create_entity",
                "target_id": "ent_person_1",
                "idempotent_upsert": True,
            }
        ],
        "extensions": {
            "com.partner_advisory.nee.operation-payloads": {
                "op_spoof": {
                    "entity_kind": "internal",
                    "entity": {
                        "entity_id": "ent_person_1",
                        "canonical_name": "Alex Rivera",
                        "node_type": "system",
                    },
                }
            }
        },
    }
    conflicts = detect_mutation_conflicts(spoofed_change_set, db_path)
    assert len(conflicts) == 1
    assert "already exists" in conflicts[0]


def test_rollback_and_recovery_proof_gate_truncates_wal_and_restores_bytes(tmp_path: Path) -> None:
    """Category 6 (Rollback & recovery): run_with_proof_gate restores pre-mutation bytes and cleans up WAL/SHM sidecars."""
    db_path = tmp_path / "rollback_test.sqlite"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT)")
        conn.execute("INSERT INTO node VALUES ('n1', 'Original Name')")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()

    pre_bytes = db_path.read_bytes()

    def _failing_mutation(_guard):
        c = sqlite3.connect(str(db_path))
        try:
            c.execute("INSERT INTO node VALUES ('n2', 'Uncommitted Mutation')")
            c.commit()
        finally:
            c.close()
        raise RuntimeError("Simulated mid-write proof failure")

    with pytest.raises(RuntimeError, match="Simulated mid-write proof failure"):
        run_with_proof_gate(db_path, "test-rollback", _failing_mutation)

    assert db_path.read_bytes() == pre_bytes
    assert not db_path.with_name(db_path.name + "-wal").exists() or db_path.with_name(db_path.name + "-wal").stat().st_size == 0

    check_conn = sqlite3.connect(str(db_path))
    try:
        rows = check_conn.execute("SELECT node_id FROM node").fetchall()
        assert rows == [("n1",)]
    finally:
        check_conn.close()


def test_auto_link_high_confidence_opt_in_vs_default_isolation() -> None:
    """Verify resolve_or_create stays conservative by default and auto-links when allow_high_confidence_link is enabled."""
    alternatives = [
        {
            "entity_id": "ent_1",
            "canonical_name": "Conduit Platform",
            "match_score": 0.95,
            "margin_over_next": 0.40,
            "supporting_context": ["Reported tokens occur in the canonical name"],
            "identity_conflicts": [],
        }
    ]
    outcome_default, _ = determine_resolution_outcome(
        type_conflict=False,
        exact_matches=[],
        profile={"entity_intent": "resolve_or_create", "mutation_intent": "CREATE"},
        declared_type_valid=True,
        stable_identifier="system:conduit-platform",
        alternatives=alternatives,
    )
    assert outcome_default == "CREATE_NEW_ENTITY_PROPOSAL"

    outcome_opt_in, _ = determine_resolution_outcome(
        type_conflict=False,
        exact_matches=[],
        profile={
            "entity_intent": "resolve_or_create",
            "mutation_intent": "CREATE",
            "allow_high_confidence_link": True,
        },
        declared_type_valid=True,
        stable_identifier="system:conduit-platform",
        alternatives=alternatives,
    )
    assert outcome_opt_in == "AUTO_LINK_HIGH_CONFIDENCE"
