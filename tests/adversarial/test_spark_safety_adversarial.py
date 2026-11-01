"""Adversarial and boundary stress tests for Managed Spark integration.

Covers:
1. Driver memory exhaustion evasion (hidden collect(), case-insensitive TOPANDAS())
2. Malformed SQL and code (syntax errors, empty queries, missing limits)
3. Boundary payloads (negative limits, huge limits clamped to ceiling)
4. Unsupported job types and rogue batch submission attempts
"""
from __future__ import annotations

import unittest

from dual_pipeline.agent_sdk.tools import (
    execute_spark_sql,
    inspect_spark_batch_status,
    submit_spark_graph_batch,
)
from dual_pipeline.spark import DriverMemoryGuard


class TestSparkSafetyAdversarial(unittest.TestCase):
    """Adversarial test suite stress testing Spark memory guards and tools."""

    def test_case_insensitive_prohibited_methods(self):
        """Invariant bypass: Case variations like .COLLECT() or .toPandas() must be caught."""
        variations = [
            "df.COLLECT()",
            "df.Collect()",
            "df.to_pandas()",
            "df.TOPANDAS()",
        ]
        for snippet in variations:
            code = f"def run(df):\n    return {snippet}\n"
            audit = DriverMemoryGuard.audit_pyspark_code(code)
            self.assertFalse(audit.is_safe, f"Failed to catch prohibited call: {snippet}")

    def test_query_row_limit_overflow_clamping(self):
        """Boundary payload: Extreme or negative row_limit values are safely clamped."""
        # Negative limit clamped to 1
        res_neg = execute_spark_sql("SELECT id FROM nodes", row_limit=-100)
        self.assertEqual(res_neg["status"], "SUCCESS")
        self.assertEqual(res_neg["enforced_row_limit"], 1)

        # Huge limit clamped to max 100 for agent context
        res_huge = execute_spark_sql("SELECT id FROM nodes", row_limit=50000)
        self.assertEqual(res_huge["status"], "SUCCESS")
        self.assertEqual(res_huge["enforced_row_limit"], 100)

    def test_empty_sql_query_rejected(self):
        """Malformed input: Empty or whitespace query fails safely."""
        for empty_q in ["", "   ", "\n\t"]:
            res = execute_spark_sql(empty_q)
            self.assertEqual(res["status"], "ERROR")
            self.assertEqual(res["row_count"], 0)

    def test_sql_query_with_dangerous_limit_rejected(self):
        """Excessive limit in SQL text (> 1000) is flagged as unsafe."""
        dangerous_sql = "SELECT * FROM huge_table LIMIT 999999"
        res = execute_spark_sql(dangerous_sql)
        self.assertEqual(res["status"], "REJECTED_BY_SAFETY_GUARD")
        self.assertEqual(len(res["violations"]), 1)
        self.assertIn("exceeds driver safety ceiling", res["violations"][0])

    def test_unsupported_batch_job_type_rejection(self):
        """Malformed input: Unapproved or unknown job types are rejected."""
        res = submit_spark_graph_batch(job_type="unauthorized_crypto_miner")
        self.assertEqual(res["status"], "ERROR")
        self.assertIn("Unsupported job_type", res["error"])

    def test_inspect_batch_status_empty_id_rejection(self):
        """Malformed input: Empty batch_id is rejected safely."""
        res = inspect_spark_batch_status("")
        self.assertEqual(res["status"], "ERROR")
        self.assertIn("batch_id is required", res["error"])

    def test_syntax_error_in_pyspark_script_caught(self):
        """Malformed code: Syntax errors in PySpark script are caught during static audit."""
        bad_code = "def invalid_syntax( ::::"
        audit = DriverMemoryGuard.audit_pyspark_code(bad_code)
        self.assertFalse(audit.is_safe)
        self.assertIn("Syntax error", audit.violations[0])


if __name__ == "__main__":
    unittest.main()
