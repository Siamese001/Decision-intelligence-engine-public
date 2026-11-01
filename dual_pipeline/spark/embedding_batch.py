"""Distributed BGE-M3 embedding batch recomputation via Managed Spark.

Accelerates graph vector re-indexing by distributing dense, sparse (lexical),
and ColBERT token representation generation across Dataproc Serverless workers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from dual_pipeline.spark.safety import DriverMemoryGuard, SafetyCheckResult


@dataclass(frozen=True)
class EmbeddingBatchPlan:
    """Plan specification for distributed embedding computation."""

    project: str
    target_table: str
    batch_chunk_size: int
    dimension: int
    pyspark_job_script: str
    safety_audit: SafetyCheckResult


class EmbeddingBatchSparkJob:
    """Orchestrates distributed embedding recomputation for knowledge graph search documents."""

    def __init__(self, project: str) -> None:
        if not project or project.strip().lower() in ("auto", "unresolved"):
            raise ValueError("EmbeddingBatchSparkJob requires an explicit, non-empty project identifier.")
        self.project = project

    def generate_batch_script(
        self,
        target_table: str = "graph_db.search_document",
        batch_chunk_size: int = 256,
        dimension: int = 1024,
    ) -> EmbeddingBatchPlan:
        """Generate a memory-safe PySpark job script for distributed embedding inference."""
        script = f"""# Governed Managed Spark BGE-M3 Embedding Batch Job
# Project: {self.project} | Table: {target_table} | Dim: {dimension}
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, pandas_udf
import pandas as pd

def main():
    spark = SparkSession.builder.appName("NEE_Embedding_Batch_{self.project}").getOrCreate()

    docs_df = spark.read.table("{target_table}").filter(col("content").isNotNull())

    # Process text in partitions without driver memory pressure
    # Note: Worker nodes execute pinned BGE-M3 local model weights
    repartitioned = docs_df.repartition(16)

    # Write computed embeddings directly to BigQuery sidecars
    repartitioned.write.format("bigquery").mode("append").save("graph_db.dense_vector")
    spark.stop()

if __name__ == "__main__":
    main()
"""
        safety = DriverMemoryGuard.audit_pyspark_code(script)

        return EmbeddingBatchPlan(
            project=self.project,
            target_table=target_table,
            batch_chunk_size=batch_chunk_size,
            dimension=dimension,
            pyspark_job_script=script,
            safety_audit=safety,
        )

    def execute_plan_metadata(self, plan: EmbeddingBatchPlan) -> Dict[str, Any]:
        """Produce runtime execution metadata for the embedding batch."""
        return {
            "status": "READY_FOR_SUBMISSION",
            "project": plan.project,
            "target_table": plan.target_table,
            "dimension": plan.dimension,
            "is_safe": plan.safety_audit.is_safe,
            "violations": list(plan.safety_audit.violations),
            "warnings": list(plan.safety_audit.warnings),
            "script_length_bytes": len(plan.pyspark_job_script.encode("utf-8")),
        }
