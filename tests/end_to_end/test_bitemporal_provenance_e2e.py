"""
tests/end_to_end/test_bitemporal_provenance_e2e.py

End-to-End lifecycle verification for Wave 2 Bi-Temporal Lineage & Provenance:
1. Migration execution repairing broken supersessions and backfilling valid_from.
2. Canonical predicate normalization (SUPPORTED_BY -> SUPPORTS).
3. Standardized RFC 3986 cold_archive_ref provenance URIs and mandatory evidence bindings.
4. Bi-temporal point-in-time querying and supersession lineage traversal.
5. Fail-closed intake validator integration.
"""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.intake_validators import (
    validate_assertion_bitemporal,
    validate_assertion_evidence_binding,
    validate_assertion_predicate,
    validate_assertion_record,
    validate_source_provenance_uri,
)
from dual_pipeline.migrations.migrate_v56_bitemporal_and_provenance import (
    migrate_v56_bitemporal_and_provenance,
    table_exists,
    verify_integrity,
)


class TestBitemporalProvenanceE2E(unittest.TestCase):
    """E2E lifecycle suite for bi-temporal lineage, provenance URIs, and canonical triples."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.db_path = self.tmp_path / "test_bitemporal_graph.sqlite"

        source_db = Path("graph/nee_graph.sqlite")
        if source_db.exists():
            shutil.copyfile(source_db, self.db_path)
        else:
            self._init_mock_graph(self.db_path)

        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA journal_mode = WAL;")
        self.conn.execute("PRAGMA busy_timeout = 30000;")
        self.conn.execute("PRAGMA foreign_keys = ON;")

    def tearDown(self) -> None:
        self.conn.close()
        self.temp_dir.cleanup()

    def _init_mock_graph(self, path: Path) -> None:
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        cur.executescript("""
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
                object_literal TEXT,
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

    def test_01_migration_execution_and_invariants(self) -> None:
        """Verifies Wave 2 migration execution achieves 100% invariant compliance."""
        result = migrate_v56_bitemporal_and_provenance(self.conn)
        self.assertEqual(result["status"], "COMPLETE")
        self.assertGreater(result["repaired_supersessions"], 0)
        self.assertGreater(result["backfilled_valid_from"], 0)
        self.assertGreater(result["normalized_predicates"], 0)
        self.assertGreater(result["standardized_sources"], 0)

        cur = self.conn.cursor()

        # Invariant 1: 0 broken supersession chains
        broken = cur.execute(
            "SELECT count(*) FROM assertion WHERE superseded_at IS NOT NULL AND superseded_by_assertion_id IS NULL"
        ).fetchone()[0]
        self.assertEqual(broken, 0, "No assertions should have unlinked superseded_at")

        # Invariant 2: 0 missing valid_from dates
        null_vf = cur.execute(
            "SELECT count(*) FROM assertion WHERE valid_from IS NULL OR trim(valid_from) = ''"
        ).fetchone()[0]
        self.assertEqual(null_vf, 0, "100% of assertions must possess a valid_from date")

        # Invariant 3: 0 inverse SUPPORTED_BY predicates
        sup_by = cur.execute(
            "SELECT count(*) FROM assertion WHERE predicate = 'SUPPORTED_BY'"
        ).fetchone()[0]
        self.assertEqual(sup_by, 0, "All SUPPORTED_BY assertions must be normalized to SUPPORTS")

        # Invariant 4: 100% source cold_archive_ref compliant with RFC 3986
        bad_uris = cur.execute(
            """
            SELECT count(*) FROM source 
            WHERE cold_archive_ref IS NULL 
               OR (cold_archive_ref NOT LIKE 'file://%' 
               AND cold_archive_ref NOT LIKE 'urn:%' 
               AND cold_archive_ref NOT LIKE 'https://%' 
               AND cold_archive_ref NOT LIKE 'http://%')
            """
        ).fetchone()[0]
        self.assertEqual(bad_uris, 0, "100% of source cold_archive_ref entries must be RFC 3986 URIs")

        # Invariant 5: 100% assertions bound to evidence
        if table_exists(self.conn, "assertion_evidence"):
            unbound = cur.execute(
                """
                SELECT count(*) FROM assertion a 
                WHERE NOT EXISTS (SELECT 1 FROM assertion_evidence ae WHERE ae.assertion_id = a.assertion_id)
                """
            ).fetchone()[0]
            self.assertEqual(unbound, 0, "100% of assertions must have at least one evidence binding")

        # Invariant 6: Strict foreign key and database integrity
        integrity = verify_integrity(self.conn)
        self.assertEqual(integrity["foreign_key_violations"], 0)
        self.assertEqual(integrity["integrity_check"], "ok")

    def test_02_bitemporal_point_in_time_querying(self) -> None:
        """Verifies point-in-time bi-temporal filtering and supersession pointer integrity."""
        migrate_v56_bitemporal_and_provenance(self.conn)
        cur = self.conn.cursor()

        # Query all superseded assertions
        superseded_rows = cur.execute(
            """
            SELECT assertion_id, superseded_at, superseded_by_assertion_id 
            FROM assertion 
            WHERE superseded_at IS NOT NULL
            """
        ).fetchall()
        self.assertGreater(len(superseded_rows), 0)

        # Every superseded pointer must resolve to an extant assertion
        for aid, sat, sby in superseded_rows:
            self.assertIsNotNone(sby)
            target = cur.execute(
                "SELECT assertion_id, currentness_state FROM assertion WHERE assertion_id = ?",
                (sby,),
            ).fetchone()
            self.assertIsNotNone(target, f"Supersession target {sby} for {aid} must exist")

        # Point-in-time filter query for current active assertions
        active_current = cur.execute(
            """
            SELECT count(*) FROM assertion 
            WHERE superseded_at IS NULL 
              AND valid_from <= '2026-10-15'
              AND (valid_to IS NULL OR valid_to >= '2026-10-15')
            """
        ).fetchone()[0]
        self.assertGreater(active_current, 0)

    def test_03_intake_validators_e2e_integration(self) -> None:
        """Verifies intake validation functions reject corrupt records and accept valid records."""
        # 1. Valid record passes
        clean_record = {
            "assertion_id": "ast_test_valid_001",
            "predicate": "SUPPORTS",
            "valid_from": "2026-07-09",
            "valid_to": "2026-12-31",
            "superseded_at": None,
            "superseded_by_assertion_id": None,
        }
        validate_assertion_record(clean_record, evidence_ids=["evi_test_001"])

        # 2. Null valid_from is rejected
        corrupt_vf = dict(clean_record, valid_from=None)
        with self.assertRaises(ValueError) as ctx:
            validate_assertion_bitemporal(corrupt_vf)
        self.assertIn("missing required temporal bound 'valid_from'", str(ctx.exception))

        # 3. Inverted time interval is rejected
        inverted_vf = dict(clean_record, valid_from="2026-12-31", valid_to="2026-01-01")
        with self.assertRaises(ValueError) as ctx:
            validate_assertion_bitemporal(inverted_vf)
        self.assertIn("precedes valid_from", str(ctx.exception))

        # 4. Broken supersession (superseded_at without superseded_by) is rejected
        broken_super = dict(clean_record, superseded_at="2026-09-01T00:00:00Z", superseded_by_assertion_id=None)
        with self.assertRaises(ValueError) as ctx:
            validate_assertion_bitemporal(broken_super)
        self.assertIn("broken supersession chain", str(ctx.exception))

        # 5. Inverse predicate is rejected
        with self.assertRaises(ValueError) as ctx:
            validate_assertion_predicate("SUPPORTED_BY")
        self.assertIn("forbidden by best practices", str(ctx.exception))

        # 6. Invalid URI schemes are rejected
        with self.assertRaises(ValueError) as ctx:
            validate_source_provenance_uri("raw/cold_archive/meeting.txt")
        self.assertIn("must have valid scheme", str(ctx.exception))

        # Valid URIs pass
        self.assertEqual(validate_source_provenance_uri("file:///archive/file.txt"), "file:///archive/file.txt")
        self.assertEqual(validate_source_provenance_uri("urn:nee:cold-archive:src_1"), "urn:nee:cold-archive:src_1")

        # 7. Unbound evidence is rejected
        with self.assertRaises(ValueError) as ctx:
            validate_assertion_evidence_binding("ast_test_valid_001", [])
        self.assertIn("at least one evidence item binding is required", str(ctx.exception))

    def test_04_idempotent_migration(self) -> None:
        """Verifies migration runs idempotently without side-effects on secondary execution."""
        first_run = migrate_v56_bitemporal_and_provenance(self.conn)
        self.assertEqual(first_run["status"], "COMPLETE")

        second_run = migrate_v56_bitemporal_and_provenance(self.conn)
        self.assertEqual(second_run["status"], "COMPLETE")
        self.assertEqual(second_run["repaired_supersessions"], 0)
        self.assertEqual(second_run["backfilled_valid_from"], 0)
        self.assertEqual(second_run["normalized_predicates"], 0)
