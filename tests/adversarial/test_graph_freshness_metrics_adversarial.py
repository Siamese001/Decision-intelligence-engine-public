"""Adversarial tests for freshness metrics."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.graph_freshness_core.metrics import calculate_metrics, run_metrics_pipeline


class TestGraphFreshnessMetricsAdversarial(unittest.TestCase):
    def test_missing_tables(self):
        with sqlite3.connect(":memory:") as conn:
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE wrong_table (id TEXT)")
            # Should raise an error because tables are missing
            with self.assertRaises(sqlite3.OperationalError):
                calculate_metrics(conn)

    def test_malformed_json_qualifiers(self):
        with sqlite3.connect(":memory:") as conn:
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE assertion (assertion_id TEXT, currentness_state TEXT, retrieval_eligible INTEGER, canonical_status TEXT, extraction_run_id TEXT, qualifiers_json TEXT, subject_node_id TEXT, predicate TEXT, object_node_id TEXT, object_literal TEXT, primary_source_id TEXT, superseded_by_assertion_id TEXT, recorded_at TEXT)")
            cursor.execute("CREATE TABLE evidence_item (evidence_id TEXT, location_status TEXT, direct_quote TEXT)")
            cursor.execute("CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT)")

            # Insert invalid JSON (SQLite json_extract should gracefully return NULL or raise depending on version)
            # Actually, SQLite `json_extract` on malformed JSON raises an error if checked.
            # In our main code, `qualifiers_json` is constraint-checked `CHECK (json_valid(qualifiers_json))`.
            # But let's insert anyway without constraints:
            cursor.execute("INSERT INTO assertion VALUES ('a1', 'current', 1, 'candidate', 'run1', '{bad json', 's1', 'p1', 'o1', NULL, 'src1', NULL, '2026-01-01')")

            with self.assertRaises(sqlite3.OperationalError):
                calculate_metrics(conn)

    def test_mutation_during_read(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "graph.sqlite"
            receipt = Path(d) / "receipt.json"

            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("CREATE TABLE assertion (assertion_id TEXT, currentness_state TEXT, retrieval_eligible INTEGER, canonical_status TEXT, extraction_run_id TEXT, qualifiers_json TEXT, subject_node_id TEXT, predicate TEXT, object_node_id TEXT, object_literal TEXT, primary_source_id TEXT, superseded_by_assertion_id TEXT, recorded_at TEXT)")
                cursor.execute("CREATE TABLE evidence_item (evidence_id TEXT, location_status TEXT, direct_quote TEXT)")
                cursor.execute("CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT)")
                cursor.execute("CREATE TABLE search_document (id TEXT)")

            # Monkey-patch sha256_file to simulate mutation
            import dual_pipeline.graph_freshness_core.metrics as m
            original_sha = m.sha256_file

            counter = 0
            def fake_sha(path):
                nonlocal counter
                counter += 1
                return f"hash{counter}"

            m.sha256_file = fake_sha
            try:
                with self.assertRaisesRegex(RuntimeError, "Graph was mutated"):
                    run_metrics_pipeline(db_path, receipt)
            finally:
                m.sha256_file = original_sha
