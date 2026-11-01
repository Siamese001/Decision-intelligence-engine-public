"""Managed Spark Integration for Decision Intelligence Engine.

Provides Dataproc Serverless session building, driver memory safety auditing,
distributed entity resolution, batch vector embedding, Dataproc batch client,
Spark-distributed Antigravity agent worker execution, and distributed 11-gate
retrieval release evaluation.
"""
from __future__ import annotations

from dual_pipeline.spark.agent_worker import (
    SparkAgentPartitionWorker,
    WorkerTokenAccumulator,
    WorkerTokenMetrics,
)
from dual_pipeline.spark.client import (
    BatchStatusResult,
    DataprocBatchControllerClient,
)
from dual_pipeline.spark.embedding_batch import (
    EmbeddingBatchPlan,
    EmbeddingBatchSparkJob,
)
from dual_pipeline.spark.entity_resolution import (
    EntityResolutionPlan,
    EntityResolutionSparkJob,
)
from dual_pipeline.spark.retrieval_eval_spark import (
    RETRIEVAL_11_GATES,
    SparkRetrievalEvalResult,
    SparkRetrievalReleaseEvaluator,
)
from dual_pipeline.spark.safety import (
    DriverMemoryGuard,
    SafetyCheckResult,
    SparkSafetyViolation,
)
from dual_pipeline.spark.session import (
    ManagedSparkSessionFactory,
    SparkSessionConfig,
)
from dual_pipeline.spark.sink import (
    PartitionedStorageSink,
    RecordExecutionSummary,
    SinkFlushResult,
)

__all__ = [
    "BatchStatusResult",
    "DataprocBatchControllerClient",
    "DriverMemoryGuard",
    "EmbeddingBatchPlan",
    "EmbeddingBatchSparkJob",
    "EntityResolutionPlan",
    "EntityResolutionSparkJob",
    "ManagedSparkSessionFactory",
    "PartitionedStorageSink",
    "RecordExecutionSummary",
    "RETRIEVAL_11_GATES",
    "SafetyCheckResult",
    "SinkFlushResult",
    "SparkAgentPartitionWorker",
    "SparkRetrievalEvalResult",
    "SparkRetrievalReleaseEvaluator",
    "SparkSafetyViolation",
    "SparkSessionConfig",
    "WorkerTokenAccumulator",
    "WorkerTokenMetrics",
]
