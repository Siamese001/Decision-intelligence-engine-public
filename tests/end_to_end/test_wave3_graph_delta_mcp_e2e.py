"""End-to-end tests for Wave 3: GraphDB Inspector MCP Expansion & Itemized Assertions Delta."""
from __future__ import annotations

import json
import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.graph_delta import (
    build_itemized_assertion_delta,
    format_markdown_mutation_table,
)
from tools.mcp_graphdb_server import (
    graphdb_inspect_vector_health,
    graphdb_itemized_assertion_delta,
)


def _create_graph_db(db_path: Path, assertions: list[dict[str, any]]) -> None:
    """Helper to initialize a test graph SQLite database with assertion table."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT,
            node_type TEXT
        );
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object_literal TEXT,
            canonical_status TEXT,
            superseded_by_assertion_id TEXT,
            created_at TEXT
        );
    """)
    for a in assertions:
        conn.execute(
            """INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal,
                                      canonical_status, superseded_by_assertion_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                a["assertion_id"],
                a.get("subject_node_id", "node:default"),
                a.get("predicate", "HAS_DECISION"),
                a.get("object_literal", ""),
                a.get("canonical_status", "CANONICAL"),
                a.get("superseded_by_assertion_id"),
                a.get("created_at", "2026-09-27T00:00:00Z"),
            ),
        )
    conn.commit()
    conn.close()


def _create_sidecar_db(sidecar_path: Path, num_records: int = 3) -> None:
    """Helper to initialize a valid sidecar database with 1024d dense vectors."""
    conn = sqlite3.connect(sidecar_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dense_vector (
            record_id TEXT PRIMARY KEY,
            dimensions INTEGER NOT NULL,
            vector BLOB NOT NULL
        );
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sparse_weight (
            record_id TEXT NOT NULL,
            token TEXT NOT NULL,
            weight REAL NOT NULL,
            PRIMARY KEY (record_id, token)
        );
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS colbert_token (
            record_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            token TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            vector BLOB NOT NULL,
            PRIMARY KEY (record_id, position)
        );
    """)
    norm_val = 1.0 / (1024 ** 0.5)
    valid_blob = struct.pack("<1024f", *([norm_val] * 1024))
    for i in range(num_records):
        rid = f"rec:{i}"
        conn.execute("INSERT INTO dense_vector VALUES (?, 1024, ?)", (rid, valid_blob))
        conn.execute("INSERT INTO sparse_weight VALUES (?, 'model', 1.5)", (rid,))
        conn.execute("INSERT INTO colbert_token VALUES (?, 0, 'tok', 1024, ?)", (rid, valid_blob))
    conn.commit()
    conn.close()


class Wave3GraphDeltaMcpE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="wave3-e2e-")
        self.dir_path = Path(self.temp_dir.name)
        self.before_db = self.dir_path / "before_graph.sqlite"
        self.after_db = self.dir_path / "after_graph.sqlite"
        self.sidecar_db = self.dir_path / "sidecar.sqlite"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_e2e_itemized_assertion_delta_lifecycle(self) -> None:
        # Baseline graph with 2 assertions
        _create_graph_db(
            self.before_db,
            [
                {
                    "assertion_id": "asrt:unchanged",
                    "subject_node_id": "project:nexus",
                    "predicate": "HAS_SCOPE",
                    "object_literal": "Phase 1 Delivery",
                    "canonical_status": "CANONICAL",
                },
                {
                    "assertion_id": "asrt:to_supersede",
                    "subject_node_id": "project:nexus",
                    "predicate": "HAS_TIMELINE",
                    "object_literal": "Q3 2026",
                    "canonical_status": "CANONICAL",
                },
            ],
        )

        # Mutated graph:
        # - asrt:unchanged is unchanged
        # - asrt:to_supersede is now superseded by asrt:new_timeline
        # - asrt:new_timeline is created
        # - asrt:modified has updated literal text
        _create_graph_db(
            self.after_db,
            [
                {
                    "assertion_id": "asrt:unchanged",
                    "subject_node_id": "project:nexus",
                    "predicate": "HAS_SCOPE",
                    "object_literal": "Phase 1 Delivery",
                    "canonical_status": "CANONICAL",
                },
                {
                    "assertion_id": "asrt:to_supersede",
                    "subject_node_id": "project:nexus",
                    "predicate": "HAS_TIMELINE",
                    "object_literal": "Q3 2026",
                    "canonical_status": "SUPERSEDED",
                    "superseded_by_assertion_id": "asrt:new_timeline",
                },
                {
                    "assertion_id": "asrt:new_timeline",
                    "subject_node_id": "project:nexus",
                    "predicate": "HAS_TIMELINE",
                    "object_literal": "Q4 2026 (Extended)",
                    "canonical_status": "CANONICAL",
                },
            ],
        )

        delta = build_itemized_assertion_delta(self.before_db, self.after_db)

        self.assertEqual(delta["total_before"], 2)
        self.assertEqual(delta["total_after"], 3)
        self.assertEqual(delta["created_count"], 1)
        self.assertEqual(delta["superseded_count"], 1)
        self.assertEqual(delta["noop_count"], 1)
        self.assertFalse(delta["is_noop"])

        # Check markdown table
        md = delta["markdown_table"]
        self.assertIn("### 2. Assertions Inserted into `assertion` (Itemized Assertions)", md)
        self.assertIn("| Assertion ID | Subject Node | Predicate | Literal / Claim Text | Status |", md)
        self.assertIn("`asrt:new_timeline`", md)
        self.assertIn("`asrt:to_supersede`", md)
        self.assertIn("`SUPERSEDED`", md)

    def test_e2e_markdown_mutation_table_always_renders_table_2(self) -> None:
        # Case 1: IDEMPOTENT_NOOP
        noop_summary = {
            "status": "IDEMPOTENT_NOOP",
            "generation_before": "gen-001",
            "generation_after": "gen-001",
            "graph_sha256_before": "aaa",
            "graph_sha256_after": "aaa",
        }
        noop_md = format_markdown_mutation_table(noop_summary)
        self.assertIn("### 1. Granular Graph DB Delta (Table-by-Table)", noop_md)
        self.assertIn("### 2. Assertions Inserted into `assertion` (Itemized Assertions)", noop_md)
        self.assertIn("| — | — | — | *Idempotent no-op: zero assertions inserted or modified.* | `IDEMPOTENT_NOOP` |", noop_md)
        self.assertIn("### 3. Idempotency & Hash Transition Explanation", noop_md)

        # Case 2: Mutating run with explicit assertions
        mut_summary = {
            "status": "GRAPH_MUTATED",
            "generation_before": "gen-001",
            "generation_after": "gen-002",
            "graph_sha256_before": "aaa",
            "graph_sha256_after": "bbb",
            "actual_graph_delta": {"table": []},
        }
        asrt_list = [
            {
                "assertion_id": "asrt:101",
                "subject_node_id": "org:partner_advisory",
                "predicate": "COMMITTED_TO",
                "claim_text": "Production readiness by October 2026",
                "status": "CANONICAL",
            }
        ]
        mut_md = format_markdown_mutation_table(mut_summary, assertions_inserted=asrt_list)
        self.assertIn("### 2. Assertions Inserted into `assertion` (Itemized Assertions)", mut_md)
        self.assertIn("`asrt:101`", mut_md)
        self.assertIn("`org:partner_advisory`", mut_md)
        self.assertIn("`COMMITTED_TO`", mut_md)
        self.assertIn("*Production readiness by October 2026*", mut_md)
        self.assertIn("`CANONICAL`", mut_md)

        # Case 3: Mutating run with empty assertions
        empty_mut_md = format_markdown_mutation_table(mut_summary, assertions_inserted=[])
        self.assertIn("### 2. Assertions Inserted into `assertion` (Itemized Assertions)", empty_mut_md)
        self.assertIn("| — | — | — | *No assertions inserted or modified.* | `UNCHANGED` |", empty_mut_md)

    def test_e2e_mcp_tools_execution(self) -> None:
        _create_graph_db(
            self.before_db,
            [{"assertion_id": "asrt:1", "object_literal": "Base assertion"}],
        )
        _create_graph_db(
            self.after_db,
            [
                {"assertion_id": "asrt:1", "object_literal": "Base assertion"},
                {"assertion_id": "asrt:2", "object_literal": "Newly added assertion"},
            ],
        )
        _create_sidecar_db(self.sidecar_db, num_records=2)

        # Test graphdb_itemized_assertion_delta MCP tool
        res_raw = graphdb_itemized_assertion_delta(
            before_db_path=str(self.before_db),
            after_db_path=str(self.after_db),
            limit=10,
        )
        res = json.loads(res_raw)
        self.assertEqual(res["created_count"], 1)
        self.assertEqual(res["total_before"], 1)
        self.assertEqual(res["total_after"], 2)
        self.assertIn("asrt:2", res["markdown_table"])

        # Test error handling for missing target db
        err_raw = graphdb_itemized_assertion_delta(
            before_db_path=str(self.before_db),
            after_db_path="/tmp/non_existent_graph_12345.sqlite",
        )
        err = json.loads(err_raw)
        self.assertIn("error", err)

        # Test graphdb_inspect_vector_health MCP tool
        vhealth_raw = graphdb_inspect_vector_health(
            sidecar_path=str(self.sidecar_db),
            sample_limit=100,
        )
        vhealth = json.loads(vhealth_raw)
        self.assertEqual(vhealth["status"], "HEALTHY")
        self.assertTrue(vhealth["is_healthy"])
        self.assertEqual(vhealth["tables"]["dense_vector"]["total_rows"], 2)
        self.assertEqual(vhealth["tables"]["dense_vector"]["nan_count"], 0)
        self.assertEqual(vhealth["tables"]["dense_vector"]["zero_vector_count"], 0)
        self.assertIn("### Vector Database Health Audit", vhealth["markdown_report"])


if __name__ == "__main__":
    unittest.main()
