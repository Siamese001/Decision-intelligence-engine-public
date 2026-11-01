"""Unit tests for Delta Auditor, Metric Historian, and Retrieval Sentinel (Waves 5 & 6)."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.graph_freshness_core.agents.delta import DeltaAuditor
from dual_pipeline.graph_freshness_core.agents.historian import MetricHistorian
from dual_pipeline.graph_freshness_core.agents.sentinel import RetrievalSentinel


class TestGraphFreshnessWave56(unittest.TestCase):
    def test_delta_auditor(self):
        pre_metrics = {
            "total_assertions": 100,
            "evidence_orphans": 10,
            "current_assertions": 90
        }
        post_metrics_good = {
            "total_assertions": 100,
            "evidence_orphans": 0,
            "current_assertions": 80
        }

        auditor = DeltaAuditor()
        findings_good = auditor.scan(pre_metrics, post_metrics_good)
        self.assertFalse(findings_good.unintended_side_effects)
        self.assertEqual(findings_good.metric_diffs["total_assertions"], 0)
        self.assertEqual(findings_good.metric_diffs["evidence_orphans"], -10)
        self.assertEqual(findings_good.metric_diffs["current_assertions"], -10)

        post_metrics_bad = {
            "total_assertions": 105, # unintended increase
            "evidence_orphans": 12,  # unintended increase
            "current_assertions": 90
        }
        findings_bad = auditor.scan(pre_metrics, post_metrics_bad)
        self.assertTrue(findings_bad.unintended_side_effects)

    def test_metric_historian(self):
        metrics = {"total_assertions": 42}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "history.jsonl"
            historian = MetricHistorian()
            historian.record(metrics, p)

            with open(p, "r") as f:
                line = f.readline()
                data = json.loads(line)

            self.assertIn("timestamp", data)
            self.assertEqual(data["metrics"]["total_assertions"], 42)

    def test_retrieval_sentinel(self):
        with sqlite3.connect(":memory:") as conn:
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE search_document (id TEXT)")
            cursor.execute("INSERT INTO search_document VALUES ('a1')")
            cursor.execute("INSERT INTO search_document VALUES ('a2')")
            cursor.execute("INSERT INTO search_document VALUES ('a3')")
            conn.commit()

            sentinel = RetrievalSentinel()
            quarantined = {"a2", "a3", "a99"}
            findings = sentinel.scan(conn, quarantined)

            # a99 is not in search_document, so it shouldn't be in the purge list
            self.assertIn("a2", findings.search_docs_to_purge)
            self.assertIn("a3", findings.search_docs_to_purge)
            self.assertNotIn("a99", findings.search_docs_to_purge)
            self.assertNotIn("a1", findings.search_docs_to_purge)
