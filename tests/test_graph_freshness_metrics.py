"""Unit tests for Graph Freshness & Integrity Metrics."""
import sqlite3
import unittest

from dual_pipeline.graph_freshness_core.metrics import calculate_metrics, verify_sweeper_dry_run


class TestGraphFreshnessMetrics(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        cursor = self.conn.cursor()

        # We need a minimal schema to test metrics.
        cursor.execute("CREATE TABLE assertion (assertion_id TEXT, currentness_state TEXT, retrieval_eligible INTEGER, canonical_status TEXT, extraction_run_id TEXT, qualifiers_json TEXT, subject_node_id TEXT, predicate TEXT, object_node_id TEXT, object_literal TEXT, primary_source_id TEXT, superseded_by_assertion_id TEXT, recorded_at TEXT)")
        cursor.execute("CREATE TABLE evidence_item (evidence_id TEXT, location_status TEXT, direct_quote TEXT)")
        cursor.execute("CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT)")

        # Populate
        cursor.execute("INSERT INTO assertion VALUES ('a1', 'current', 1, 'candidate', 'run1', '{}', 's1', 'p1', 'o1', NULL, 'src1', NULL, '2026-01-01')")
        cursor.execute("INSERT INTO assertion VALUES ('a2', 'historical', 1, 'candidate', NULL, '{\"stale_after\": \"2026-10-01\"}', 's1', 'p1', 'o2', NULL, 'src2', NULL, '2026-02-01')")

        cursor.execute("INSERT INTO evidence_item VALUES ('e1', 'exact', 'quote1')")
        cursor.execute("INSERT INTO evidence_item VALUES ('e2', 'fuzzy', NULL)")

        cursor.execute("INSERT INTO assertion_evidence VALUES ('a1', 'e1')")

        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_calculate_metrics(self):
        metrics = calculate_metrics(self.conn)
        self.assertEqual(metrics["total_assertions"], 2)
        self.assertEqual(metrics["current_assertions"], 1)
        self.assertEqual(metrics["assertions_no_extraction_run"], 1)
        self.assertEqual(metrics["assertions_no_evidence"], 1) # a2
        self.assertEqual(metrics["evidence_orphans"], 1) # e2
        self.assertEqual(metrics["evidence_loc_not_exact"], 1) # e2
        self.assertEqual(metrics["evidence_no_quote"], 1) # e2
        self.assertEqual(metrics["stale_after_coverage"], 0) # a2 is not current

    def test_sweeper_dry_run(self):
        # Sweeper should see no stale_after column since it's not a real column here,
        # but in actual schema it might be.
        res = verify_sweeper_dry_run(self.conn)
        self.assertEqual(res["status"], "NO_STALE_AFTER_COLUMN")
