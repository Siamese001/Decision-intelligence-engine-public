"""Pipeline B: Artery 2 - The Slow Path (Compute Fork & Extraction).

Provides async worker execution over `durable_work_queue` enforcing:
  - Law 1: Compute Fork BEFORE Extraction (CLIENT_SAFE vs INTERNAL_ADVISORY)
  - Law 2: Purged Agentic Leverage -> Governed INTERNAL_ADVISORY
  - Law 3: Discourse Window Extraction
  - Law 4: Immutable Provenance First
  - Law 5: Multi-Dimensional Soft-Quarantine Tagging & Incremental Entity Resolution
"""
from pipeline_b_extraction.contracts import (
    DiscourseBatch,
    ExtractionInferenceError,
    ForkedExtractionResult,
)
from pipeline_b_extraction.dlq import (
    DeadLetterEntry,
    DeadLetterQueueManager,
)
from pipeline_b_extraction.extractors import (
    ClientSafeExtractor,
    ComputeForkCoordinator,
    InternalAdvisoryExtractor,
)
from pipeline_b_extraction.json_repair import (
    extract_outer_json_structure,
    repair_and_parse_json,
    repair_truncated_json,
    strip_markdown_fences,
)
from pipeline_b_extraction.judges import (
    JudgeResult,
    QualityJudge,
    QualityMicroJudge,
    StealthJudge,
)
from pipeline_b_extraction.pre_egress_sanitizer import (
    LocalEgressSanitizer,
    SanitizationResult,
    default_sanitizer,
    sanitize_for_cloud_egress,
)
from pipeline_b_extraction.worker import (
    ExtractionQueueWorker,
    SlowPathWorker,
    WorkerConfig,
)

__all__ = [
    "ClientSafeExtractor",
    "ComputeForkCoordinator",
    "DeadLetterEntry",
    "DeadLetterQueueManager",
    "DiscourseBatch",
    "ExtractionInferenceError",
    "ExtractionQueueWorker",
    "ForkedExtractionResult",
    "InternalAdvisoryExtractor",
    "JudgeResult",
    "LocalEgressSanitizer",
    "QualityJudge",
    "QualityMicroJudge",
    "SanitizationResult",
    "SlowPathWorker",
    "StealthJudge",
    "WorkerConfig",
    "default_sanitizer",
    "extract_outer_json_structure",
    "repair_and_parse_json",
    "repair_truncated_json",
    "sanitize_for_cloud_egress",
    "strip_markdown_fences",
]
