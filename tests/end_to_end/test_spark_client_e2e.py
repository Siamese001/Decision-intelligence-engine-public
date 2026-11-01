"""End-to-End test suite for Dataproc Serverless controller and Spark tools.

Exercises full lifecycle:
1. Agent initialization equipped with live-ready Spark tools
2. Memory-bounded Spark SQL query execution
3. Dataproc Serverless batch graph job submission (entity resolution & embedding)
4. Batch lifecycle status tracking and inspection
"""
from __future__ import annotations

import unittest

from google.antigravity import Agent

from dual_pipeline.agent_sdk import AgentConfigFactory
from dual_pipeline.agent_sdk.tools import (
    execute_spark_sql,
    inspect_spark_batch_status,
    submit_spark_graph_batch,
)
from dual_pipeline.spark.session import ManagedSparkSessionFactory, SparkSessionConfig


class TestSparkClientE2E(unittest.TestCase):
    """End-to-End lifecycle tests for Dataproc batch controller and agent tools."""

    def test_full_agent_spark_tools_lifecycle(self):
        """Exercises complete agent lifecycle using refactored Spark tools."""
        factory = AgentConfigFactory(env={"GEMINI_API_KEY": "dummy-spark-client-e2e-key"})
        config = factory.create_local_agent_config(
            system_instructions="Spark Graph Operations Agent",
            tools=[execute_spark_sql, submit_spark_graph_batch, inspect_spark_batch_status],
            model_name="gemini-3.8-flash",
        )
        agent = Agent(config)
        self.assertIsNotNone(agent)

        # 1. Execute Spark SQL query tool
        sql_res = execute_spark_sql(
            query="SELECT entity_id, name, degree FROM graph_db.node WHERE degree > 3",
            row_limit=25,
        )
        self.assertEqual(sql_res["status"], "SUCCESS")
        self.assertIn("LIMIT 25", sql_res["executed_query"])
        self.assertGreater(sql_res["row_count"], 0)

        # 2. Submit entity resolution batch job
        batch_sub = submit_spark_graph_batch(
            job_type="entity_resolution",
            project="project_nexus",
            parameters={"target_kind": "organization", "similarity_threshold": 0.90},
        )
        self.assertEqual(batch_sub["status"], "SUBMITTED")
        self.assertTrue(batch_sub["is_safe"])
        self.assertIn("batch-entity_resolution-project_nexus-001", batch_sub["batch_id"])

        # 3. Submit embedding batch job
        embed_sub = submit_spark_graph_batch(
            job_type="embedding_batch",
            project="project_nexus",
            parameters={"target_table": "graph_db.search_document", "dimension": 1024},
        )
        self.assertEqual(embed_sub["status"], "SUBMITTED")
        self.assertTrue(embed_sub["is_safe"])

        # 4. Inspect status of submitted batch
        status_res = inspect_spark_batch_status(batch_sub["batch_id"])
        self.assertEqual(status_res["status"], "SUCCEEDED")
        self.assertEqual(status_res["state"], "SUCCEEDED")
        self.assertIn("https://dataproc.google.com/batches/", status_res["spark_ui_url"])

    def test_dataproc_batch_controller_pipeline(self):
        """Verifies session factory generating controller and executing batch pipeline."""
        session_factory = ManagedSparkSessionFactory(
            default_config=SparkSessionConfig(
                project_id="apex_global-energy-di",
                location="us-central1",
            )
        )
        controller = session_factory.get_batch_controller()
        batch_id = "batch-e2e-pipeline-001"
        payload = session_factory.build_dataproc_batch_payload(
            main_python_file_uri="gs://apex_global-artifacts/scripts/extract.py",
            batch_id=batch_id,
        )

        sub_res = controller.submit_batch(batch_id=batch_id, batch_payload=payload)
        self.assertEqual(sub_res["status"], "SUBMITTED")

        status = controller.wait_for_completion(batch_id=batch_id, timeout_seconds=10.0)
        self.assertTrue(status.is_terminal)
        self.assertTrue(status.is_successful)


if __name__ == "__main__":
    unittest.main()
