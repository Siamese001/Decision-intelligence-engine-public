"""Adversarial edge-case tests for the 13-Agent Council and 100% Itemized HITL Mutation Contract Verifier.

Covers:
1. Boundary payloads (empty findings/clusters, missing tables).
2. Malformed inputs / survivor reversal tampering.
3. Invariant bypass attempts (skipped deduplication, mutated excluded pair, premature commit in apply_supersessions).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from dual_pipeline.graph_maintenance_core.dedup import apply_supersessions
from dual_pipeline.graph_maintenance_core.hygiene_mutation_agents import (
    PromisedMutation,
    compile_promised_mutations,
    verify_hitl_cluster_mutations_applied,
)


def _create_sandbox_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT,
            canonical_name TEXT,
            display_name TEXT,
            normalized_name TEXT,
            status TEXT DEFAULT 'canonical',
            active INTEGER DEFAULT 1,
            updated_at TEXT
        );
        CREATE TABLE entity_redirect (
            from_node_id TEXT PRIMARY KEY,
            to_node_id TEXT,
            merge_event_id TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_node_id TEXT,
            scope_text TEXT,
            canonical_status TEXT DEFAULT 'canonical',
            canonical_eligible INTEGER DEFAULT 1,
            retrieval_eligible INTEGER DEFAULT 1,
            currentness_state TEXT DEFAULT 'current',
            validation_state TEXT DEFAULT 'accepted_legacy',
            superseded_by_assertion_id TEXT,
            superseded_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE assertion_evidence (
            assertion_id TEXT,
            evidence_id TEXT,
            evidence_relationship TEXT DEFAULT 'supports',
            created_at TEXT,
            PRIMARY KEY (assertion_id, evidence_id)
        );
        CREATE TABLE search_document (
            search_document_id TEXT PRIMARY KEY,
            object_type TEXT,
            object_id TEXT,
            title TEXT,
            body TEXT,
            canonical_status TEXT,
            currentness_state TEXT,
            retrieval_eligible INTEGER,
            assertion_id TEXT,
            updated_at TEXT
        );
        CREATE TABLE graph_quality_exception (
            exception_id TEXT PRIMARY KEY,
            metric_name TEXT,
            object_type TEXT,
            object_id TEXT,
            exception_type TEXT,
            reason TEXT,
            control_action TEXT,
            owner TEXT,
            approved_reference TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT
        );
        CREATE TABLE audit_event (
            id TEXT PRIMARY KEY,
            event_type TEXT,
            actor TEXT,
            payload_json TEXT,
            created_at TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO node (node_id, node_type, canonical_name, display_name, normalized_name, active) VALUES (?, ?, ?, ?, ?, 1)",
        [
            ("person_survivor", "stakeholder", "Jane Doe", "Jane Doe", "jane doe"),
            ("entity_retired", "stakeholder", "J. Doe", "J. Doe", "jane doe"),
            ("role_generic", "role_group", "Technical Lead", "Technical Lead", "technical lead"),
            ("role_project", "role_group", "Atlas Technical Lead", "Atlas Technical Lead", "atlas technical lead"),
        ],
    )
    conn.executemany(
        "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, scope_text) VALUES (?, ?, ?, ?, ?)",
        [
            ("asrt-win", "person_survivor", "HAS_ROLE", "role_generic", "Jane Doe is Technical Lead."),
            ("asrt-lose", "person_survivor", "HAS_ROLE", "role_generic", "Jane Doe serves as Technical Lead."),
        ],
    )
    conn.commit()
    conn.close()


def test_adversarial_boundary_empty_promised_mutations(tmp_path: Path) -> None:
    """Boundary payload: empty package compiles 0 promised mutations and verifies PASS with coverage 1.0."""
    db_path = tmp_path / "empty_contract.sqlite"
    _create_sandbox_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        promised = compile_promised_mutations(conn, {"findings": [], "excluded_findings": []})
    assert promised == []
    res = verify_hitl_cluster_mutations_applied(db_path, promised)
    assert res["status"] == "PASS"
    assert res["total_promised"] == 0
    assert res["coverage_ratio"] == 1.0


def test_adversarial_survivor_reversal_and_unapplied_mutations_fail_closed(tmp_path: Path) -> None:
    """Invariant bypass attempt: if survivor/retired order is reversed or an excluded pair is merged, verifier fails closed."""
    db_path = tmp_path / "tampered.sqlite"
    _create_sandbox_db(db_path)

    pkg = {
        "findings": [
            {
                "detective": "StructuralValidator",
                "severity": "HIGH",
                "description": "Merge entity_retired into person_survivor",
                "entity_references": ["person_survivor", "entity_retired"],
                "proposed_fix": "merge_entities",
                "raw_data": {
                    "survivor_node_id": "person_survivor",
                    "retired_node_id": "entity_retired",
                    "node_id_a": "person_survivor",
                    "node_id_b": "entity_retired",
                },
            },
            {
                "detective": "StructuralValidator",
                "severity": "MEDIUM",
                "description": "Deduplicate asrt-lose into asrt-win",
                "entity_references": ["asrt-lose", "asrt-win"],
                "proposed_fix": "deduplicate_assertions",
                "raw_data": {"winner_id": "asrt-win", "loser_id": "asrt-lose"},
            },
        ],
        "excluded_findings": [
            {
                "proposed_fix": "merge_entities",
                "entity_references": ["role_generic", "role_project"],
                "raw_data": {"node_id_a": "role_generic", "node_id_b": "role_project"},
                "exclusion_reason": "Keep distinct",
            }
        ],
    }

    with sqlite3.connect(str(db_path)) as conn:
        promised = compile_promised_mutations(conn, pkg)
    assert len(promised) == 3

    #Tamper DB: reverse the merge (retire person_survivor instead of entity_retired), leave asrt-lose current, and merge excluded role_project!
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE node SET active=0, status='merged' WHERE node_id='person_survivor'")
        conn.execute("INSERT INTO entity_redirect VALUES ('person_survivor', 'entity_retired', 'm-1', 1, '2026-10-27')")
        conn.execute("UPDATE node SET active=0, status='merged' WHERE node_id='role_project'")
        conn.commit()

    res = verify_hitl_cluster_mutations_applied(db_path, promised)
    assert res["status"] == "FAIL"
    assert res["failed_count"] == 3
    failed_actions = {f["action"] for f in res["failures"]}
    assert "merge_entities" in failed_actions
    assert "deduplicate_assertions" in failed_actions


def test_adversarial_apply_supersessions_auto_commit_false_preserves_tx(tmp_path: Path) -> None:
    """Concurrency & rollback: apply_supersessions(..., auto_commit=False) must not commit the caller's open transaction."""
    db_path = tmp_path / "tx_rollback.sqlite"
    _create_sandbox_db(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.execute("BEGIN IMMEDIATE")
    count = apply_supersessions(
        conn,
        [{"winner_id": "asrt-win", "loser_id": "asrt-lose", "hitl_required": False}],
        now_iso="2026-10-27T00:00:00Z",
        include_hitl_approved=True,
        auto_commit=False,
    )
    assert count == 1
    assert conn.in_transaction, "apply_supersessions committed prematurely despite auto_commit=False!"
    conn.rollback()
    conn.close()

    with sqlite3.connect(str(db_path)) as ro_conn:
        state = ro_conn.execute("SELECT currentness_state FROM assertion WHERE assertion_id='asrt-lose'").fetchone()[0]
        assert state == "current", "Transaction rollback failed because of premature commit!"
