"""Unit tests for Managed Spark driver memory safety and execution guards.

Validates:
- Prohibited driver collection patterns (.collect, .toPandas) are caught
- Missing SQL LIMIT clauses are automatically bounded to driver ceilings
- Excessive SQL LIMIT clauses are flagged as safety violations
- Broadcast join hints are detected and validated
- EntityResolutionSparkJob and EmbeddingBatchSparkJob emit safe PySpark scripts
"""
from __future__ import annotations

import unittest

from dual_pipeline.spark import (
    DriverMemoryGuard,
    EmbeddingBatchSparkJob,
    EntityResolutionSparkJob,
)


class TestSparkSafety(unittest.TestCase):
    """Unit test suite for DriverMemoryGuard and PySpark safety auditing."""

    def test_sql_audit_injects_missing_limit(self):
        """Un-bounded SELECT query receives automatic safety LIMIT."""
        query = "SELECT node_id, kind FROM graph_db.node WHERE kind = 'person'"
        safe_sql, audit = DriverMemoryGuard.audit_sql_query(query, max_allowed_rows=50)

        self.assertTrue(audit.is_safe)
        self.assertIn("LIMIT 50", safe_sql)
        self.assertEqual(audit.enforced_row_limit, 50)
        self.assertGreater(len(audit.warnings), 0)

    def test_sql_audit_rejects_excessive_limit(self):
        """Query with LIMIT > 1000 is clamped and flagged as a safety violation."""
        query = "SELECT * FROM graph_db.assertion LIMIT 50000"
        safe_sql, audit = DriverMemoryGuard.audit_sql_query(query, max_allowed_rows=100)

        self.assertFalse(audit.is_safe)
        self.assertIn("exceeds driver safety ceiling", audit.violations[0])
        self.assertIn("LIMIT 100", safe_sql)

    def test_sql_audit_detects_broadcast_hint(self):
        """Broadcast join hint is correctly identified in query."""
        query = "SELECT /*+ BROADCAST(t) */ n.id, t.name FROM node n JOIN taxonomy t ON n.kind = t.kind LIMIT 20"
        _, audit = DriverMemoryGuard.audit_sql_query(query)
        self.assertTrue(audit.uses_broadcast_join)

    def test_pyspark_audit_detects_prohibited_collect(self):
        """Prohibited .collect() call on DataFrame triggers safety violation."""
        unsafe_code = """
def process():
    df = spark.read.table("huge_table")
    records = df.collect()  # Driver OOM Hazard!
    return records
"""
        audit = DriverMemoryGuard.audit_pyspark_code(unsafe_code)
        self.assertFalse(audit.is_safe)
        self.assertIn("Prohibited un-aggregated .collect()", audit.violations[0])

    def test_pyspark_audit_detects_prohibited_topandas(self):
        """Prohibited .toPandas() call triggers safety violation."""
        unsafe_code = """
def process():
    df = spark.read.table("huge_table")
    pdf = df.toPandas()
    return pdf
"""
        audit = DriverMemoryGuard.audit_pyspark_code(unsafe_code)
        self.assertFalse(audit.is_safe)
        self.assertIn("Prohibited un-aggregated .toPandas()", audit.violations[0])

    def test_entity_resolution_script_is_safe(self):
        """EntityResolutionSparkJob generates code passing DriverMemoryGuard."""
        job = EntityResolutionSparkJob(project="project_nexus")
        plan = job.generate_batch_script(target_kind="person")

        self.assertTrue(plan.safety_audit.is_safe)
        self.assertEqual(len(plan.safety_audit.violations), 0)
        self.assertTrue(plan.safety_audit.uses_broadcast_join)

    def test_embedding_batch_script_is_safe(self):
        """EmbeddingBatchSparkJob generates code passing DriverMemoryGuard."""
        job = EmbeddingBatchSparkJob(project="project_nexus")
        plan = job.generate_batch_script()

        self.assertTrue(plan.safety_audit.is_safe)
        self.assertEqual(len(plan.safety_audit.violations), 0)


if __name__ == "__main__":
    unittest.main()
