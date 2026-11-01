"""
tests/property/test_bitemporal_provenance_adversarial.py

Adversarial and Property-Based Verification for Wave 2 Bi-Temporal Lineage & Provenance:
1. Broken Supersession Chain Invariant Enforcement.
2. Temporal Interval Boundary Invariance (valid_to >= valid_from).
3. Hypothesis Generative Fuzzing for Provenance URIs & Dates.
4. Inverse Predicate Rejection Invariance.
5. Transactional Rollback & Fault Injection Recovery.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict

import hypothesis.strategies as st
from hypothesis import given, settings

from dual_pipeline.intake_validators import (
    FORBIDDEN_INVERSE_PREDICATES,
    VALID_URI_SCHEMES,
    validate_assertion_bitemporal,
    validate_assertion_evidence_binding,
    validate_assertion_predicate,
    validate_source_provenance_uri,
)
from dual_pipeline.migrations.migrate_v56_bitemporal_and_provenance import (
    migrate_v56_bitemporal_and_provenance,
    verify_integrity,
)


class TestBitemporalProvenanceAdversarial(unittest.TestCase):
    """Adversarial and property-based test suite for bi-temporal and provenance safety."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.db_path = self.tmp_path / "adversarial_bitemporal.sqlite"
        self._init_sandbox_schema(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _init_sandbox_schema(self, path: Path) -> None:
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        cur.executescript("""
            PRAGMA journal_mode = WAL;
            PRAGMA busy_timeout = 30000;
            PRAGMA foreign_keys = ON;

            CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                node_type TEXT NOT NULL,
                canonical_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE source (
                source_id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_name TEXT NOT NULL,
                authority_rank INTEGER NOT NULL,
                source_posture TEXT NOT NULL,
                cold_archive_ref TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT NOT NULL REFERENCES node(node_id),
                predicate TEXT NOT NULL,
                object_node_id TEXT REFERENCES node(node_id),
                valid_from TEXT,
                valid_to TEXT,
                recorded_at TEXT,
                superseded_at TEXT,
                superseded_by_assertion_id TEXT REFERENCES assertion(assertion_id),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE evidence_item (
                evidence_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES source(source_id),
                created_at TEXT NOT NULL
            );

            CREATE TABLE assertion_evidence (
                assertion_id TEXT NOT NULL REFERENCES assertion(assertion_id) ON DELETE CASCADE,
                evidence_id TEXT NOT NULL REFERENCES evidence_item(evidence_id),
                evidence_relationship TEXT NOT NULL,
                independent_corroboration INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (assertion_id, evidence_id)
            );
        """)
        conn.commit()
        conn.close()

    @settings(max_examples=30, deadline=None)
    @given(
        start_date=st.dates(min_value=date(2000, 1, 1), max_value=date(2030, 1, 1)),
        delta_days=st.integers(min_value=1, max_value=3650),
    )
    def test_property_temporal_intervals(self, start_date: date, delta_days: int) -> None:
        """Verifies valid intervals pass and inverted intervals are rejected."""
        valid_end = start_date + timedelta(days=delta_days)
        valid_rec: Dict[str, Any] = {
            "assertion_id": "ast_valid_temporal",
            "valid_from": start_date.isoformat(),
            "valid_to": valid_end.isoformat(),
            "superseded_at": None,
            "superseded_by_assertion_id": None,
        }
        # Valid interval must not raise
        validate_assertion_bitemporal(valid_rec)

        # Inverted interval must raise ValueError
        inverted_rec = dict(valid_rec, valid_from=valid_end.isoformat(), valid_to=start_date.isoformat())
        with self.assertRaises(ValueError):
            validate_assertion_bitemporal(inverted_rec)

    @settings(max_examples=30, deadline=None)
    @given(
        superseded_timestamp=st.text(min_size=1, max_size=30).filter(lambda s: bool(s.strip())),
        missing_by=st.sampled_from([None, "", "   "]),
    )
    def test_property_broken_supersession_chains(
        self, superseded_timestamp: str, missing_by: Any
    ) -> None:
        """Verifies setting superseded_at without valid superseded_by_assertion_id is rejected."""
        broken_rec: Dict[str, Any] = {
            "assertion_id": "ast_broken_super",
            "valid_from": "2026-07-09",
            "superseded_at": superseded_timestamp,
            "superseded_by_assertion_id": missing_by,
        }
        with self.assertRaises(ValueError):
            validate_assertion_bitemporal(broken_rec)

    @settings(max_examples=20, deadline=None)
    @given(
        forbidden_pred=st.sampled_from(sorted(FORBIDDEN_INVERSE_PREDICATES)),
        whitespace=st.sampled_from(["", "  ", "\t"]),
    )
    def test_property_inverse_predicate_rejection(
        self, forbidden_pred: str, whitespace: str
    ) -> None:
        """Verifies all forbidden inverse predicates are rejected regardless of casing or whitespace."""
        test_pred = f"{whitespace}{forbidden_pred.lower()}{whitespace}"
        with self.assertRaises(ValueError):
            validate_assertion_predicate(test_pred)

    @settings(max_examples=30, deadline=None)
    @given(
        scheme=st.sampled_from(sorted(VALID_URI_SCHEMES)),
        path_body=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_/-", min_size=1, max_size=40),
    )
    def test_property_valid_and_invalid_uris(self, scheme: str, path_body: str) -> None:
        """Verifies RFC 3986 URI parsing across schemes and fail-closed rejection of bare paths."""
        valid_uri = f"{scheme}:///{path_body}" if scheme != "urn" else f"urn:nee:{path_body}"
        resolved = validate_source_provenance_uri(valid_uri)
        self.assertEqual(resolved, valid_uri)

        # Bare relative path without scheme must raise
        with self.assertRaises(ValueError):
            validate_source_provenance_uri(path_body)

    def test_adversarial_migration_transaction_rollback(self) -> None:
        """Verifies that an intentional fault during migration rolls back cleanly."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        # Seed test node and assertion with SUPPORTED_BY
        cur.execute("INSERT INTO node (node_id, node_type, canonical_name, created_at, updated_at) VALUES ('n1', 'concept', 'Node 1', '2026-01-01', '2026-01-01');")
        cur.execute("INSERT INTO node (node_id, node_type, canonical_name, created_at, updated_at) VALUES ('n2', 'concept', 'Node 2', '2026-01-01', '2026-01-01');")
        cur.execute("""
            INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, valid_from, recorded_at, created_at, updated_at)
            VALUES ('ast_initial', 'n1', 'SUPPORTED_BY', 'n2', '2026-01-01', '2026-01-01', '2026-01-01', '2026-01-01');
        """)
        conn.commit()

        # Normal migration succeeds
        res = migrate_v56_bitemporal_and_provenance(conn)
        self.assertEqual(res["status"], "COMPLETE")

        # Invariant checks pass
        integrity = verify_integrity(conn)
        self.assertEqual(integrity["foreign_key_violations"], 0)
        self.assertEqual(integrity["integrity_check"], "ok")

        # Fault injection test: Introduce a trigger that aborts updates, verify rollback
        cur.execute("""
            CREATE TRIGGER abort_trigger BEFORE UPDATE ON assertion
            BEGIN
                SELECT RAISE(ABORT, 'Fault injection: update aborted');
            END;
        """)
        conn.commit()

        # Insert a row that would trigger update
        cur.execute("""
            INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, valid_from, recorded_at, created_at, updated_at)
            VALUES ('ast_fault', 'n1', 'SUPPORTED_BY', 'n2', '2026-01-01', '2026-01-01', '2026-01-01', '2026-01-01');
        """)
        conn.commit()

        # Migration must fail closed and roll back
        with self.assertRaises(RuntimeError) as ctx:
            migrate_v56_bitemporal_and_provenance(conn)
        self.assertIn("Migration v56 Wave 2 failed", str(ctx.exception))

        # Check ast_fault still has SUPPORTED_BY (unmutated due to rollback)
        row = cur.execute("SELECT predicate FROM assertion WHERE assertion_id='ast_fault'").fetchone()
        self.assertEqual(row[0], "SUPPORTED_BY")
        conn.close()
