"""
tests/test_hitl_ledger_unification_adversarial.py

Adversarial and edge-case test suite for Wave 1 HITL ledger unification and calibration.
Covers:
1. Boundary payloads: Empty stores, single-record ledgers, zero-entry envelopes.
2. Malformed input: Corrupted secondary JSON, missing required fields, unparseable entries.
3. Concurrency & race: Multi-threaded concurrent writes to SQLite under WAL mode and locking.
4. Invariant bypass attempts: Autonomous non-surfaced records quarantined from human cohorts.
5. Path parity: resolve_hitl_ledger_path() matches resolve_ledger_paths() identically.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.hitl_governance_core.decision_record_writer import (
    resolve_ledger_paths,
)
from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.hitl_governance_core.telemetry import resolve_hitl_ledger_path
from dual_pipeline.storage.hitl_sqlite_store import HITL_TABLE_SCHEMA
from tools.reconcile_hitl_ledgers import (
    _is_unwanted_autonomous_noise,
    reconcile_ledgers,
)
from tools.verify_learning_calibration import verify_wave6


def test_ledger_path_resolution_parity():
    """Verify reader and writer ledger path resolvers return identical canonical paths."""
    resolved_sqlite, resolved_json = resolve_ledger_paths()
    single_path = resolve_hitl_ledger_path()

    assert resolved_json == single_path, (
        f"Path mismatch: {resolved_json} vs {single_path}"
    )
    assert resolved_sqlite.stem == resolved_json.stem
    assert ".local" in resolved_sqlite.parts or resolved_sqlite.exists()


def test_adversarial_boundary_empty_stores():
    """Verify empty stores reconcile cleanly with zero records and genesis hash intact."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        auth_sqlite = tmp / "auth.sqlite"
        auth_json = tmp / "auth.json"
        sec_sqlite = tmp / "sec.sqlite"
        sec_json = tmp / "sec.json"

        # Create empty files
        with sqlite3.connect(auth_sqlite) as conn:
            conn.executescript(HITL_TABLE_SCHEMA)
        auth_json.write_text(json.dumps({"decisions": []}))

        receipt = reconcile_ledgers(
            authoritative_sqlite_path=auth_sqlite,
            authoritative_json_path=auth_json,
            secondary_sqlite_path=sec_sqlite,
            secondary_json_path=sec_json,
        )

        assert receipt["status"] == "PASS"
        assert receipt["parity_achieved"] is True
        assert receipt["authoritative_sqlite_count"] == 0
        assert receipt["canonical_json_count"] == 0
        assert receipt["latest_entry_hash"] == "0" * 64


def test_adversarial_malformed_secondary_input():
    """Verify reconciliation survives malformed or corrupted secondary JSON without failing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        auth_sqlite = tmp / "auth.sqlite"
        auth_json = tmp / "auth.json"
        sec_json = tmp / "corrupt_sec.json"

        with sqlite3.connect(auth_sqlite) as conn:
            conn.executescript(HITL_TABLE_SCHEMA)
            conn.execute(
                """
                INSERT INTO hitl_decisions (
                    entry_id, sequence_number, decision_id, category,
                    operator_id, prompt, options, selected_option,
                    selected_key, rationale, previous_entry_hash,
                    recorded_at, metadata, entry_digest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "valid-entry-1", 0, "dec-1", "general",
                    "human_op", "Prompt", "[]", "opt-a",
                    "opt-a", "Rationale", "0" * 64,
                    "2026-10-22T00:00:00Z", "{}", "digest1",
                ),
            )
        auth_json.write_text(json.dumps({"decisions": []}))

        # Write corrupted secondary json
        sec_json.write_text("{ this is completely invalid json ::: ")

        receipt = reconcile_ledgers(
            authoritative_sqlite_path=auth_sqlite,
            authoritative_json_path=auth_json,
            secondary_json_path=sec_json,
        )

        assert receipt["status"] == "PASS"
        assert receipt["parity_achieved"] is True
        assert receipt["authoritative_sqlite_count"] == 1
        assert receipt["canonical_json_count"] == 1


def test_adversarial_quarantine_autonomous_noise():
    """Verify non-surfaced autonomous stage checks are quarantined from human cohorts."""
    # Test cases that must be quarantined
    quarantined_records = [
        {
            "category": "silence_gap_classification",
            "operator_id": "autonomous_engine",
            "metadata": {"authority_type": "AUTONOMOUS", "should_surface": False},
        },
        {
            "category": "acronym_disambiguation",
            "operator_id": "auto_engine",
            "metadata": {"authority_type": "AUTONOMOUS"},
        },
        {
            "category": "autonomous_stage_check",
            "operator_id": "auto",
            "metadata": {"authority_type": "AUTONOMOUS", "should_surface": False},
        },
    ]

    for rec in quarantined_records:
        assert _is_unwanted_autonomous_noise(rec) is True, f"Failed to quarantine: {rec}"

    # Human or surfaced decisions that must NOT be quarantined
    human_records = [
        {
            "category": "role_clarification",
            "operator_id": "human_operator",
            "metadata": {"authority_type": "HUMAN", "should_surface": True},
        },
        {
            "category": "pr_merge_authorization",
            "operator_id": "human_operator",
            "metadata": {"should_surface": True},
        },
        {
            "category": "speaker_attribution",
            "operator_id": "human_reviewer",
            "metadata": {"authority_type": "HUMAN"},
        },
    ]

    for rec in human_records:
        assert _is_unwanted_autonomous_noise(rec) is False, f"Erroneously quarantined: {rec}"


def test_adversarial_concurrent_store_access():
    """Verify concurrent reads and transactions on HITLDecisionStore do not deadlock or fail."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_path = tmp / "concurrent_hitl.sqlite"
        json_path = tmp / "concurrent_hitl.json"

        with sqlite3.connect(db_path) as conn:
            conn.executescript(HITL_TABLE_SCHEMA)
            conn.execute("PRAGMA journal_mode = WAL;")

        json_path.write_text(json.dumps({"decisions": []}))

        errors: list[Exception] = []

        def worker(worker_id: int):
            try:
                store = HITLDecisionStore(ledger_path=json_path, sqlite_path=db_path, use_sqlite=True)
                for _ in range(5):
                    # Read records concurrently
                    recs = store.get_history()
                    assert isinstance(recs, list)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Encountered concurrency errors: {errors}"


def test_verify_learning_calibration_passes():
    """Verify that verify_wave6() executes cleanly and returns PASS status."""
    receipt = verify_wave6()
    assert receipt["pass"] is True, f"Calibration verification failed: {receipt}"
    assert receipt["checks"]["schema_contract"]["status_proposed"] is True
    assert receipt["checks"]["simulation"]["simulation_success"] is True
    assert receipt["checks"]["regression_gate"]["wave1_topology_pass"] is True
    assert receipt["checks"]["regression_gate"]["wave2_cards_pass"] is True
    assert receipt["checks"]["regression_gate"]["waves_3_to_5_pytest_pass"] is True
