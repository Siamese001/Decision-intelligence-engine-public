"""E2E test for the graph freshness baseline generation."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.graph_freshness_core.metrics import run_metrics_pipeline


class TestGraphFreshnessBaselineE2E(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.graph_path = Path(self.temp_dir.name) / "graph.sqlite"
        self.receipt_path = Path(self.temp_dir.name) / "reports" / "graph_freshness_baseline_receipt.json"

        with sqlite3.connect(self.graph_path) as conn:
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE assertion (assertion_id TEXT, currentness_state TEXT, retrieval_eligible INTEGER, canonical_status TEXT, extraction_run_id TEXT, qualifiers_json TEXT, subject_node_id TEXT, predicate TEXT, object_node_id TEXT, object_literal TEXT, primary_source_id TEXT, superseded_by_assertion_id TEXT, recorded_at TEXT)")
            cursor.execute("CREATE TABLE evidence_item (evidence_id TEXT, location_status TEXT, direct_quote TEXT)")
            cursor.execute("CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT)")
            cursor.execute("CREATE TABLE search_document (id TEXT)")

            # 2579 assertions, mostly current to simulate the real DB.
            for i in range(100):
                state = "current" if i < 90 else "historical"
                cursor.execute(f"INSERT INTO assertion VALUES ('a{i}', '{state}', 1, 'candidate', 'run1', '{{}}', 's{i}', 'p1', 'o1', NULL, 'src1', NULL, '2026-01-01')")

            conn.commit()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_baseline_generation(self):
        # Run the pipeline
        receipt = run_metrics_pipeline(self.graph_path, self.receipt_path)

        self.assertEqual(receipt["status"], "COMPLETE")
        self.assertTrue(self.receipt_path.exists())

        with open(self.receipt_path, "r") as f:
            data = json.load(f)

        self.assertEqual(data["metrics"]["total_assertions"], 100)
        self.assertEqual(data["metrics"]["current_assertions"], 90)
        self.assertEqual(data["pre_sha256"], data["post_sha256"])
        self.assertEqual(data["sweeper_dry_run_status"]["status"], "NO_STALE_AFTER_COLUMN")
