"""
tests/migrations/test_roundtrip_safety.py

Phase 5 CI Testing Rigor: Database Migration & Schema Compatibility Suite.
Enforces:
1. Bidirectional Migration & Loss Validation: Tests roundtrip schema up() and down()
   with 100% data preservation and zero unapproved destructive operations.
2. Destructive Operation Approval Gate: Confirms that unapproved DROP TABLE, DROP COLUMN,
   or TRUNCATE statements fail closed unless authorized with [DATA_LOSS_APPROVED].
3. N-1 Cache Backward Compatibility: Guarantees CopilotGraphRetriever and InMemoryGraphTopology
   gracefully load and query N-1 legacy schema snapshots without crashes or regression.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from typing import List

import pytest

from dual_pipeline.in_memory_graph import InMemoryGraphTopology
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import CopilotGraphRetriever
from storage.migration import (
    BUILTIN_MIGRATIONS,
    DestructiveMigrationError,
    Migration,
    SchemaMigrator,
)
from tests.fixtures.high_fidelity_graph import compute_sha256


class TestMigrationRoundtripAndSafety:
    """Verifies bidirectional migration safety, destructive DDL gating, and N-1 compatibility."""

    def test_roundtrip_migration_up_and_down_safety(self) -> None:
        """Verifies schema version N -> N+1 (up()) and N+1 -> N (down()) with 100% data preservation."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "roundtrip_test.db"
            conn = sqlite3.connect(str(db_path))

            # 1. Apply base migrations (1-3)
            base_migrations = [m for m in BUILTIN_MIGRATIONS if m.version <= 3]
            migrator = SchemaMigrator(migrations=base_migrations)
            applied_base = migrator.apply_pending(conn)
            assert applied_base == [1, 2, 3]

            # 2. Populate pre-existing data into N schema tables
            conn.execute(
                "INSERT INTO _storage_metadata (key, value) VALUES (?, ?)",
                ("active_tenant", "partner_advisory_enterprise"),
            )
            conn.execute(
                "INSERT INTO _storage_metadata (key, value) VALUES (?, ?)",
                ("cluster_mode", "wal_isolated"),
            )
            conn.execute(
                "INSERT INTO _storage_audit_ledger (audit_id, event_type, details) VALUES (?, ?, ?)",
                ("aud_101", "BOOTSTRAP", "Baseline cluster initialization"),
            )
            conn.commit()

            # Record baseline row counts and hashes
            meta_count_before = conn.execute("SELECT COUNT(*) FROM _storage_metadata").fetchone()[0]
            audit_count_before = conn.execute("SELECT COUNT(*) FROM _storage_audit_ledger").fetchone()[0]
            assert meta_count_before == 2
            assert audit_count_before == 1

            # 3. Define N+1 forward migration with reversible down_sql
            migration_v4 = Migration(
                version=4,
                name="004_add_feature_flags_and_tenant_registry [DATA_LOSS_APPROVED]",
                up_sql="""
                CREATE TABLE app_feature_flags (
                    flag_id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    description TEXT
                );
                INSERT INTO app_feature_flags (flag_id, enabled, description)
                VALUES ('copilot_neighborhood_cache_v2', 1, 'LRU bounded graph cache');
                """,
                down_sql="""
                DROP TABLE IF EXISTS app_feature_flags;
                """,
            )

            extended_migrator = SchemaMigrator(migrations=base_migrations + [migration_v4])
            applied_v4 = extended_migrator.apply_pending(conn)
            assert applied_v4 == [4]

            # Verify table exists in N+1 schema
            flag_row = conn.execute(
                "SELECT enabled, description FROM app_feature_flags WHERE flag_id = 'copilot_neighborhood_cache_v2'"
            ).fetchone()
            assert flag_row is not None
            assert flag_row[0] == 1

            # 4. Rollback from N+1 down to N (target_version=3)
            rolled_back = extended_migrator.rollback_to_version(conn, target_version=3)
            assert rolled_back == [4]

            # Verify N+1 table is removed
            table_check = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='app_feature_flags'"
            ).fetchone()
            assert table_check is None

            # 5. Verify 100% data preservation in base tables
            meta_rows = conn.execute("SELECT key, value FROM _storage_metadata ORDER BY key ASC").fetchall()
            audit_rows = conn.execute("SELECT audit_id, event_type FROM _storage_audit_ledger").fetchall()

            assert len(meta_rows) == meta_count_before
            assert meta_rows[0] == ("active_tenant", "partner_advisory_enterprise")
            assert meta_rows[1] == ("cluster_mode", "wal_isolated")

            assert len(audit_rows) == audit_count_before
            assert audit_rows[0] == ("aud_101", "BOOTSTRAP")

            # Verify _schema_migrations state reflects target version 3
            applied_now = extended_migrator.get_applied_versions(conn)
            assert list(applied_now.keys()) == [1, 2, 3]

            conn.close()

    def test_destructive_operation_data_loss_approval_gate(self) -> None:
        """Verifies destructive DDL (DROP TABLE, TRUNCATE) fails closed unless accompanied by [DATA_LOSS_APPROVED]."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "destructive_gate_test.db"
            conn = sqlite3.connect(str(db_path))

            # Apply baseline migrations
            base_migrations = [m for m in BUILTIN_MIGRATIONS if m.version <= 2]
            migrator = SchemaMigrator(migrations=base_migrations)
            migrator.apply_pending(conn)

            # Insert critical production data
            conn.execute(
                "INSERT INTO _storage_metadata (key, value) VALUES ('critical_token', 'sec_vault_999')"
            )
            conn.commit()

            # 1. Attempt unapproved destructive migration (lacks [DATA_LOSS_APPROVED])
            unapproved_migration = Migration(
                version=3,
                name="003_drop_storage_metadata_unapproved",
                up_sql="DROP TABLE _storage_metadata;",
            )

            strict_migrator = SchemaMigrator(migrations=base_migrations + [unapproved_migration])

            # Must fail closed with DestructiveMigrationError
            with pytest.raises(DestructiveMigrationError) as exc_info:
                strict_migrator.apply_pending(conn)

            assert "contains destructive operations without mandatory [DATA_LOSS_APPROVED] authorization" in str(
                exc_info.value
            )

            # Verify table was NOT dropped and data is 100% intact
            preserved_row = conn.execute(
                "SELECT value FROM _storage_metadata WHERE key = 'critical_token'"
            ).fetchone()
            assert preserved_row is not None
            assert preserved_row[0] == "sec_vault_999"

            # 2. Now attempt with approved marker: [DATA_LOSS_APPROVED]
            approved_migration = Migration(
                version=3,
                name="003_drop_storage_metadata_authorized [DATA_LOSS_APPROVED]",
                up_sql="DROP TABLE _storage_metadata;",
            )

            approved_migrator = SchemaMigrator(migrations=base_migrations + [approved_migration])
            applied = approved_migrator.apply_pending(conn)
            assert applied == [3]

            # Verify table has now been legitimately dropped under approved HITL gate
            table_check = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='_storage_metadata'"
            ).fetchone()
            assert table_check is None

            conn.close()

    def test_n_minus_1_cache_backward_compatibility(self) -> None:
        """Guarantees CopilotGraphRetriever gracefully loads and queries N-1 legacy schema snapshots."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            legacy_db_path = Path(tmp_dir) / "legacy_n_minus_1_graph.db"
            conn = sqlite3.connect(str(legacy_db_path))
            cur = conn.cursor()

            # Construct N-1 legacy schema (omitting modern v4 metadata, tenant tags, and node history)
            cur.execute(
                """
                CREATE TABLE node (
                    node_id TEXT PRIMARY KEY,
                    canonical_name TEXT NOT NULL,
                    display_name TEXT,
                    node_type TEXT NOT NULL,
                    description TEXT,
                    status TEXT DEFAULT 'active',
                    active INTEGER DEFAULT 1
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE edge (
                    edge_id TEXT PRIMARY KEY,
                    source_node_id TEXT NOT NULL,
                    target_node_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    weight REAL DEFAULT 1.0,
                    status TEXT DEFAULT 'active'
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE assertion (
                    assertion_id TEXT PRIMARY KEY,
                    subject_node_id TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object_node_id TEXT,
                    object_literal TEXT,
                    canonical_status TEXT DEFAULT 'approved',
                    retrieval_eligible INTEGER DEFAULT 1,
                    materiality TEXT DEFAULT 'high',
                    classification TEXT DEFAULT 'internal',
                    allowed_audiences_json TEXT DEFAULT '["partner_advisory_consulting", "nee-engineering"]',
                    primary_source_id TEXT,
                    scope_text TEXT,
                    engagement_id TEXT,
                    project_scope TEXT,
                    confidence REAL DEFAULT 1.0,
                    status TEXT DEFAULT 'active'
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE source (
                    source_id TEXT PRIMARY KEY,
                    title TEXT,
                    source_date_normalized TEXT
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE evidence_item (
                    evidence_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    transcript_timestamp TEXT,
                    direct_quote TEXT
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE assertion_evidence (
                    assertion_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    evidence_relationship TEXT NOT NULL DEFAULT 'supports',
                    PRIMARY KEY (assertion_id, evidence_id)
                )
                """
            )
            cur.execute(
                """
                CREATE VIEW v_canonical_edge AS
                SELECT a.assertion_id,
                       a.subject_node_id AS from_node_id,
                       'RELATES_TO' AS relationship_family,
                       a.predicate AS edge_type,
                       a.object_node_id AS to_node_id,
                       a.materiality,
                       a.primary_source_id AS source_id,
                       0 AS hitl_required
                  FROM assertion a
                """
            )

            # Insert N-1 legacy records (Sharon & Daniel Brakin SAP ownership in Project Nexus)
            cur.execute(
                "INSERT INTO node (node_id, canonical_name, display_name, node_type) VALUES (?, ?, ?, ?)",
                ("person:daniel_brakin", "Daniel Brakin", "Daniel Brakin", "person"),
            )
            cur.execute(
                "INSERT INTO node (node_id, canonical_name, display_name, node_type) VALUES (?, ?, ?, ?)",
                ("system:sap", "SAP", "SAP S/4HANA", "system"),
            )
            cur.execute(
                "INSERT INTO edge (edge_id, source_node_id, target_node_id, relation_type) VALUES (?, ?, ?, ?)",
                ("edge_1", "person:daniel_brakin", "system:sap", "OWNS_SYSTEM"),
            )
            cur.execute(
                """
                INSERT INTO assertion (
                    assertion_id, subject_node_id, predicate, object_node_id,
                    object_literal, primary_source_id, engagement_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "ast-legacy-sap-001",
                    "person:daniel_brakin",
                    "leads_architecture_for",
                    "system:sap",
                    "Daniel Brakin is the confirmed tech owner and architect for SAP integration.",
                    "src_nexus_01",
                    "project:project_nexus",
                ),
            )
            cur.execute(
                "INSERT INTO source (source_id, title, source_date_normalized) VALUES (?, ?, ?)",
                ("src_nexus_01", "Project Nexus Architecture Workshop", "10/01/2026"),
            )
            cur.execute(
                "INSERT INTO evidence_item (evidence_id, source_id, transcript_timestamp, direct_quote) VALUES (?, ?, ?, ?)",
                (
                    "ev_nexus_01",
                    "src_nexus_01",
                    "10:15",
                    "Daniel Brakin confirmed ownership of the SAP integration stream.",
                ),
            )
            cur.execute(
                "INSERT INTO assertion_evidence (assertion_id, evidence_id) VALUES (?, ?)",
                ("ast-legacy-sap-001", "ev_nexus_01"),
            )
            conn.commit()
            conn.close()

            db_hash = compute_sha256(legacy_db_path)

            # Load into memory using production loader
            in_mem = InMemoryGraphTopology.load_from_sqlite(legacy_db_path, expected_sha256=db_hash)
            assert in_mem is not None
            assert len(in_mem.nodes) >= 2
            assert len(in_mem.assertions) >= 1

            # Instantiate retriever with legacy database
            attendees = [{"name": "Daniel Brakin", "email": "dbrakin@example.com"}]
            retriever = CopilotGraphRetriever(
                in_mem,
                attendees=attendees,
                graph_sha256=db_hash,
                generation="generation-legacy-n-minus-1",
                engagement_id="project:project_nexus",
            )

            # Query retriever against N-1 schema
            assertions = retriever.retrieve_structured_assertions(
                query="Who is the tech owner for SAP?",
                max_results=5,
            )

            # Verify assertions retrieved cleanly without runtime exceptions or missing column crashes
            assert assertions is not None
            assert len(assertions) >= 1
            sap_claims = [a.get("object_literal", "") for a in assertions]
            assert any("Daniel Brakin is the confirmed tech owner" in claim for claim in sap_claims)

            # Also verify retrieve_context functions smoothly
            ctx_text = retriever.retrieve_context("Who is the tech owner for SAP?")
            assert isinstance(ctx_text, str)
