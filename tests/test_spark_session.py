"""Unit tests for Managed Spark session factory and configuration building.

Validates:
- ManagedSparkSessionFactory resolves project, location, and storage defaults
- Dataproc Serverless batch payloads match Google Cloud specifications
- Spark Connect builder parameters are constructed cleanly
- Credential isolation (no secrets leaked in session summaries)
"""
from __future__ import annotations

import unittest

from dual_pipeline.spark import ManagedSparkSessionFactory, SparkSessionConfig


class TestSparkSession(unittest.TestCase):
    """Unit test suite for Spark session and Dataproc Serverless batch configuration."""

    def test_default_session_factory_env_resolution(self):
        """Factory resolves project and location from environment or defaults."""
        env = {
            "SPARK_PROJECT_ID": "apex_global-ai-prod",
            "SPARK_REGION": "us-central1",
            "DATAPROC_DEPS_BUCKET": "gs://apex_global-spark-deps",
        }
        factory = ManagedSparkSessionFactory(env=env)
        config = factory.config

        self.assertEqual(config.project_id, "apex_global-ai-prod")
        self.assertEqual(config.location, "us-central1")
        self.assertEqual(config.deps_bucket, "gs://apex_global-spark-deps")
        self.assertTrue(config.use_serverless)
        self.assertIn("spark.jars.packages", config.spark_properties)

    def test_dataproc_batch_payload_generation(self):
        """Constructs contract-valid Dataproc Serverless PySpark batch payload."""
        factory = ManagedSparkSessionFactory(
            default_config=SparkSessionConfig(
                project_id="apex_global-gcp-test",
                location="us-east4",
                service_account="dataproc-runner@apex_global-gcp-test.iam.gserviceaccount.com",
            )
        )

        payload = factory.build_dataproc_batch_payload(
            main_python_file_uri="gs://apex_global-spark-deps/entity_resolution.py",
            args=["--project", "project_nexus", "--threshold", "0.85"],
            batch_id="batch-entity-resolution-001",
        )

        self.assertEqual(
            payload["name"],
            "projects/apex_global-gcp-test/locations/us-east4/batches/batch-entity-resolution-001",
        )
        self.assertEqual(
            payload["pyspark_batch"]["main_python_file_uri"],
            "gs://apex_global-spark-deps/entity_resolution.py",
        )
        self.assertEqual(
            payload["pyspark_batch"]["args"],
            ["--project", "project_nexus", "--threshold", "0.85"],
        )
        self.assertIn(
            "service_account",
            payload["environment_config"]["execution_config"],
        )

    def test_spark_connect_builder_parameters(self):
        """Constructs parameters for google-cloud-spark-connect builder."""
        factory = ManagedSparkSessionFactory(
            default_config=SparkSessionConfig(
                project_id="test-proj",
                location="us-west1",
            )
        )
        params = factory.build_connect_builder_params()
        self.assertEqual(params["project_id"], "test-proj")
        self.assertEqual(params["location"], "us-west1")
        self.assertIn("properties", params)

    def test_credential_isolation_summary(self):
        """Summary contains high-level config without leaking sensitive credentials."""
        config = SparkSessionConfig(
            project_id="test-proj",
            service_account="secret-account@test.iam.gserviceaccount.com",
        )
        summary = config.summary()
        self.assertIn("test-proj", summary)
        self.assertNotIn("secret-account", summary)


if __name__ == "__main__":
    unittest.main()
