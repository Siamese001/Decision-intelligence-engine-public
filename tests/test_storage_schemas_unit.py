"""Unit tests for storage schema DDL and initialization modules.

Verifies schema DDL loading and in-memory SQLite execution for:
- storage.schema_canonical
- storage.schema_sidecar
- storage.schema_shadow
"""
from __future__ import annotations

import sqlite3
import unittest

from storage.schema_canonical import (
    get_canonical_schema_ddl,
    get_evidence_schema_ddl,
    init_canonical_schema,
    init_evidence_schema,
)
from storage.schema_shadow import (
    get_shadow_schema_ddl,
    init_shadow_schema,
)
from storage.schema_sidecar import (
    get_sidecar_schema_ddl,
    init_sidecar_schema,
)


class TestStorageSchemasUnit(unittest.TestCase):
    def test_ddl_strings_are_non_empty(self) -> None:
        self.assertTrue(len(get_evidence_schema_ddl().strip()) > 50)
        self.assertTrue(len(get_canonical_schema_ddl().strip()) > 50)
        self.assertTrue(len(get_sidecar_schema_ddl().strip()) > 50)
        self.assertTrue(len(get_shadow_schema_ddl().strip()) > 50)

    def test_init_canonical_and_evidence_schema(self) -> None:
        conn = sqlite3.connect(":memory:")
        try:
            init_evidence_schema(conn)
            init_canonical_schema(conn)

            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            # Verify core canonical and evidence tables exist
            self.assertIn("canonical_graph_nodes", tables)
            self.assertIn("canonical_graph_edges", tables)
            self.assertIn("assertion", tables)
            self.assertIn("assertion_evidence", tables)
            self.assertIn("immutable_evidence_log", tables)
            self.assertIn("durable_work_queue", tables)
        finally:
            conn.close()

    def test_init_sidecar_schema(self) -> None:
        conn = sqlite3.connect(":memory:")
        try:
            init_sidecar_schema(conn)

            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            self.assertIn("dense_vector", tables)
            self.assertIn("sparse_weight", tables)
            self.assertIn("colbert_token", tables)
            self.assertIn("retrieval_record", tables)
        finally:
            conn.close()

    def test_init_shadow_schema_and_migration(self) -> None:
        conn = sqlite3.connect(":memory:")
        try:
            # Case 1: fresh initialization
            init_shadow_schema(conn)

            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            self.assertIn("shadow_graph_nodes", tables)
            self.assertIn("shadow_graph_edges", tables)

            cols = [
                r[1]
                for r in conn.execute(
                    "PRAGMA table_info(shadow_graph_nodes)"
                ).fetchall()
            ]
            self.assertIn("target_entity_hmac", cols)

            # Case 2: Idempotent re-initialization
            init_shadow_schema(conn)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
