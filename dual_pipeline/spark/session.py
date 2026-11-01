"""Dataproc Serverless and Managed Spark Session management.

Provides configuration building, environment resolution, and session management
for Google Managed Spark (Dataproc Serverless and Spark Connect).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

from storage.credentials import CredentialManager


@dataclass(frozen=True)
class SparkSessionConfig:
    """Configuration parameters for Google Managed Spark / Dataproc Serverless."""

    project_id: str
    location: str = "us-central1"
    deps_bucket: Optional[str] = None
    service_account: Optional[str] = None
    batch_id_prefix: str = "nee-graph-analytics"
    driver_memory: str = "4g"
    executor_memory: str = "4g"
    max_executors: int = 10
    use_serverless: bool = True
    spark_properties: Mapping[str, str] = field(default_factory=dict)

    def summary(self) -> str:
        """Sanitized representation of Spark configuration."""
        return (
            f"SparkConfig[project={self.project_id}, location={self.location}, "
            f"serverless={self.use_serverless}, driver_mem={self.driver_memory}, "
            f"exec_mem={self.executor_memory}, max_executors={self.max_executors}]"
        )


class ManagedSparkSessionFactory:
    """Builds and manages configurations for Dataproc Serverless and Spark Connect."""

    def __init__(
        self,
        env: Optional[Mapping[str, str]] = None,
        default_config: Optional[SparkSessionConfig] = None,
    ) -> None:
        self._env = env if env is not None else CredentialManager.get_process_environment()
        self._config = default_config or self._resolve_config_from_env()

    @property
    def config(self) -> SparkSessionConfig:
        return self._config

    def _resolve_config_from_env(self) -> SparkSessionConfig:
        """Resolve project, region, and GCS dependencies from environment."""
        project = (
            self._env.get("SPARK_PROJECT_ID")
            or self._env.get("VERTEX_PROJECT")
            or self._env.get("GOOGLE_CLOUD_PROJECT")
            or "decision-intelligence-dev"
        )
        location = (
            self._env.get("SPARK_REGION")
            or self._env.get("VERTEX_LOCATION")
            or "us-central1"
        )
        deps_bucket = self._env.get("DATAPROC_DEPS_BUCKET")
        service_account = self._env.get("DATAPROC_SERVICE_ACCOUNT")

        # Standard BigQuery and Iceberg connectors on Managed Spark
        properties = {
            "spark.jars.packages": "com.google.cloud.spark:spark-3.5-bigquery:0.41.0",
            "spark.sql.streaming.forceDeleteTempCheckpointLocation": "true",
            "spark.dynamicAllocation.maxExecutors": "10",
        }

        return SparkSessionConfig(
            project_id=project,
            location=location,
            deps_bucket=deps_bucket,
            service_account=service_account,
            spark_properties=properties,
        )

    def build_dataproc_batch_payload(
        self,
        main_python_file_uri: str,
        python_file_uris: Optional[list[str]] = None,
        args: Optional[list[str]] = None,
        batch_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Construct the REST/gcloud payload for submitting a Dataproc Serverless PySpark batch."""
        clean_batch_id = batch_id or f"{self._config.batch_id_prefix}-001"

        payload: Dict[str, Any] = {
            "name": f"projects/{self._config.project_id}/locations/{self._config.location}/batches/{clean_batch_id}",
            "pyspark_batch": {
                "main_python_file_uri": main_python_file_uri,
                "python_file_uris": python_file_uris or [],
                "args": args or [],
            },
            "runtime_config": {
                "properties": dict(self._config.spark_properties),
            },
            "environment_config": {
                "execution_config": {
                    "subnetwork_uri": self._env.get("DATAPROC_SUBNETWORK_URI", "default"),
                }
            },
        }

        if self._config.service_account:
            payload["environment_config"]["execution_config"]["service_account"] = (
                self._config.service_account
            )

        return payload

    def build_connect_builder_params(self) -> Dict[str, Any]:
        """Construct parameters for ManagedSparkSession.builder (google-cloud-spark-connect)."""
        return {
            "project_id": self._config.project_id,
            "location": self._config.location,
            "properties": dict(self._config.spark_properties),
        }

    def get_batch_controller(self) -> Any:
        """Instantiate a DataprocBatchControllerClient configured with current settings."""
        from dual_pipeline.spark.client import DataprocBatchControllerClient

        return DataprocBatchControllerClient(
            project_id=self._config.project_id,
            location=self._config.location,
        )

    def get_managed_spark_session(self) -> Optional[Any]:
        """Connect to ManagedSparkSession via Spark Connect when enabled by environment."""
        if self._env.get("SPARK_CONNECT_ENABLED", "0") in ("1", "true", "True"):
            try:
                from google.cloud.managed_spark_connect import ManagedSparkSession

                builder = ManagedSparkSession.builder.projectId(self._config.project_id).location(
                    self._config.location
                )
                for k, v in self._config.spark_properties.items():
                    builder = builder.config(k, v)
                return builder.getOrCreate()
            except Exception:
                return None
        return None
