"""End-to-End test suite for Managed Spark & Antigravity SDK integration.

Validates the full lifecycle of agent-driven Spark analytics:
1. Agent initialization equipped with Spark custom tools
2. Memory-bounded Spark SQL execution
3. Dataproc Serverless batch graph analytics submission
4. Runtime batch status inspection
"""
from __future__ import annotations

import unittest

from google.antigravity import Agent, LocalAgentConfig

from dual_pipeline.agent_sdk import AgentConfigFactory
from dual_pipeline.agent_sdk.tools import (
    execute_spark_sql,
    inspect_spark_batch_status,
    submit_spark_graph_batch,
)
from dual_pipeline.spark import ManagedSparkSessionFactory, SparkSessionConfig


class TestSparkIntegrationE2E(unittest.TestCase):
    """End-to-End lifecycle test suite for Spark analytics within Antigravity agents."""

    def test_full_spark_agent_lifecycle(self):
        """Exercises complete agent lifecycle equipped with Spark tools."""
        factory = AgentConfigFactory(env={"GEMINI_API_KEY": "dummy-spark-e2e-key"})

        # 1. Agent configuration with Spark custom tools
        config = factory.create_local_agent_config(
            system_instructions="Spark Graph Analytics Specialist.",
            tools=[execute_spark_sql, submit_spark_graph_batch, inspect_spark_batch_status],
            model_name="gemini-3.8-flash",
        )
        self.assertIsInstance(config, LocalAgentConfig)
        self.assertIsNotNone(config.tools)
        self.assertEqual(len(config.tools), 3)

        agent = Agent(config)
        self.assertIsNotNone(agent)

        # 2. Execute Spark SQL query tool
        sql_res = execute_spark_sql(
            query="SELECT entity_id, kind, degree FROM graph_db.node WHERE degree > 5",
            row_limit=50,
        )
        self.assertEqual(sql_res["status"], "SUCCESS")
        self.assertIn("LIMIT 50", sql_res["executed_query"])
        self.assertGreater(sql_res["row_count"], 0)

        # 3. Submit Dataproc Serverless entity resolution batch
        batch_res = submit_spark_graph_batch(
            job_type="entity_resolution",
            project="project_nexus",
            parameters={"target_kind": "person", "similarity_threshold": 0.88},
        )
        self.assertEqual(batch_res["status"], "SUBMITTED")
        self.assertTrue(batch_res["is_safe"])
        self.assertIn("batch-entity_resolution", batch_res["batch_id"])

        # 4. Inspect status of submitted batch
        status_res = inspect_spark_batch_status(batch_res["batch_id"])
        self.assertEqual(status_res["status"], "SUCCEEDED")
        self.assertEqual(status_res["state"], "SUCCEEDED")

    def test_dataproc_serverless_batch_pipeline_lifecycle(self):
        """Exercises Dataproc Serverless session builder and payload pipeline."""
        session_factory = ManagedSparkSessionFactory(
            default_config=SparkSessionConfig(
                project_id="apex_global-energy-gcp",
                location="us-central1",
                deps_bucket="gs://apex_global-artifacts",
            )
        )
        payload = session_factory.build_dataproc_batch_payload(
            main_python_file_uri="gs://apex_global-artifacts/embedding_batch.py",
            args=["--table", "graph_db.search_document", "--dim", "1024"],
            batch_id="batch-embed-001",
        )
        self.assertIn("apex_global-energy-gcp", payload["name"])
        self.assertEqual(
            payload["pyspark_batch"]["main_python_file_uri"],
            "gs://apex_global-artifacts/embedding_batch.py",
        )


if __name__ == "__main__":
    unittest.main()
