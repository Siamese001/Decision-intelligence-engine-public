"""Unit tests for the complete 10-agent orchestrator panel."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.graph_freshness_core.orchestrator import run_orchestrator


class TestGraphFreshnessOrchestrator(unittest.TestCase):
    def test_orchestrator_pipeline(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = Path(d) / "graph.sqlite"
            out_path = Path(d) / "plan.json"
            hist_path = Path(d) / "history.jsonl"

            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("CREATE TABLE assertion (assertion_id TEXT, currentness_state TEXT, retrieval_eligible INTEGER, canonical_status TEXT, extraction_run_id TEXT, qualifiers_json TEXT, subject_node_id TEXT, predicate TEXT, object_node_id TEXT, object_literal TEXT, primary_source_id TEXT, superseded_by_assertion_id TEXT, recorded_at TEXT, validation_state TEXT, resolution_state TEXT)")
                cursor.execute("CREATE TABLE evidence_item (evidence_id TEXT, location_status TEXT, direct_quote TEXT)")
                cursor.execute("CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT)")
                cursor.execute("CREATE TABLE search_document (id TEXT)")

                # Insert mock data that will trigger the agents
                cursor.execute("INSERT INTO evidence_item VALUES ('e_orphan', 'exact', 'quote')")
                # Stale assertion
                cursor.execute("INSERT INTO assertion VALUES ('a_stale', 'current', 1, 'candidate', 'run1', '{}', 's1', 'p1', 'o1', NULL, 'src1', NULL, date('now', '-100 days'), 'unreviewed', 'resolved')")
                # Quarantined assertion (no extraction run)
                cursor.execute("INSERT INTO assertion VALUES ('a_quar', 'current', 1, 'candidate', NULL, '{}', 's2', 'p2', 'o2', NULL, 'src1', NULL, date('now'), 'unreviewed', 'resolved')")
                # Sentinel doc for stale assertion
                cursor.execute("INSERT INTO search_document VALUES ('a_stale')")

                conn.commit()

            receipt = run_orchestrator(db_path, out_path, hist_path)

            self.assertEqual(receipt["status"], "PLAN_READY")
            self.assertTrue(out_path.exists())
            self.assertTrue(hist_path.exists())

            self.assertIn("DELETE FROM evidence_item WHERE evidence_id = 'e_orphan';", receipt["sql_script"])
            self.assertIn("UPDATE assertion SET currentness_state='historical', retrieval_eligible=0 WHERE assertion_id = 'a_stale';", receipt["sql_script"])
            self.assertIn("UPDATE assertion SET validation_state='needs_review', retrieval_eligible=0 WHERE assertion_id = 'a_quar';", receipt["sql_script"])
            self.assertIn("DELETE FROM search_document WHERE id = 'a_stale';", receipt["sql_script"])
