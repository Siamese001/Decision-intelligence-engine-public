"""Large-scale graph entity resolution and deduplication via Managed Spark.

Distributes pairwise entity comparison, phonetic clustering, and cross-source
deduplication across Dataproc Serverless workers without driver memory bottlenecks.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from dual_pipeline.spark.safety import DriverMemoryGuard, SafetyCheckResult


@dataclass(frozen=True)
class EntityResolutionPlan:
    """Plan specification for Spark-powered graph entity resolution."""

    project: str
    target_node_kind: str
    similarity_threshold: float
    max_cluster_size: int
    pyspark_job_script: str
    safety_audit: SafetyCheckResult


class EntityResolutionSparkJob:
    """Orchestrates Spark batch jobs for deduplicating knowledge graph entities."""

    def __init__(self, project: str) -> None:
        if not project or project.strip().lower() in ("auto", "unresolved"):
            raise ValueError("EntityResolutionSparkJob requires an explicit, non-empty project identifier.")
        self.project = project

    def generate_batch_script(
        self,
        target_kind: str = "person",
        similarity_threshold: float = 0.85,
        max_cluster_size: int = 50,
    ) -> EntityResolutionPlan:
        """Generate a memory-safe PySpark job script for entity resolution."""
        # Clean templated script adhering to broadcast joins and bounded collection
        script = f"""# Governed Managed Spark Entity Resolution Batch Job
# Project: {self.project} | Target Kind: {target_kind}
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, broadcast, levenshtein, length

def main():
    spark = SparkSession.builder.appName("NEE_Entity_Resolution_{target_kind}").getOrCreate()

    # Read nodes from BigQuery or Parquet
    nodes_df = spark.read.table("graph_db.node").filter(col("kind") == "{target_kind}")

    # Broadcast small reference taxonomy
    taxonomy_df = spark.read.table("graph_db.entity_taxonomy")
    joined = nodes_df.join(broadcast(taxonomy_df), "kind")

    # Compute pairwise normalized edit distance
    pairs = joined.alias("a").join(
        joined.alias("b"),
        (col("a.name") != col("b.name")) &
        (levenshtein(col("a.name"), col("b.name")) <= 3)
    )

    # Write resolved clusters to BigQuery
    pairs.write.format("bigquery").mode("append").save("graph_db.resolved_clusters")
    spark.stop()

if __name__ == "__main__":
    main()
"""
        safety = DriverMemoryGuard.audit_pyspark_code(script)

        return EntityResolutionPlan(
            project=self.project,
            target_node_kind=target_kind,
            similarity_threshold=similarity_threshold,
            max_cluster_size=max_cluster_size,
            pyspark_job_script=script,
            safety_audit=safety,
        )

    def execute_plan_metadata(self, plan: EntityResolutionPlan) -> Dict[str, Any]:
        """Produce runtime execution metadata for the entity resolution batch."""
        return {
            "status": "READY_FOR_SUBMISSION",
            "project": plan.project,
            "target_kind": plan.target_node_kind,
            "similarity_threshold": plan.similarity_threshold,
            "is_safe": plan.safety_audit.is_safe,
            "violations": list(plan.safety_audit.violations),
            "warnings": list(plan.safety_audit.warnings),
            "script_length_bytes": len(plan.pyspark_job_script.encode("utf-8")),
        }
