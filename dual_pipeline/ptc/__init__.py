"""
dual_pipeline/ptc/__init__.py

Package initialization for Programmatic Tool Calling (PTC) subsystem across all phases:
- Phase 1: Sandbox, Spooler, PHGQE (Hybrid Retrieval), PGBH (11-Gate Benchmark)
- Phase 2: PERH (Entity Resolution), PDME (Decision Matrix Evaluator)
- Phase 3: PASQM (Ambiguity Sifter & Queue Manager), PCFS (Context Fusion Streamer)
"""
from __future__ import annotations

from dual_pipeline.ptc.benchmark_harness import (
    PTC11GateBenchmarkHarness,
    PTC11GateReceipt,
    PTC11GateReleasePlan,
)
from dual_pipeline.ptc.context_fusion import (
    PTCContextFusionStreamer,
    PTCFusedContextPayload,
    PTCFusionPlan,
    PTCLiveFastPathEvent,
)
from dual_pipeline.ptc.contracts import (
    PTCError,
    PTCExecutionContext,
    PTCExecutionError,
    PTCResultSummary,
    PTCSecurityViolationError,
    PTCStatus,
    PTCTimeoutError,
    TokenBudget,
)
from dual_pipeline.ptc.decision_evaluator import (
    PTCDecisionCriterion,
    PTCDecisionEvaluationReceipt,
    PTCDecisionMatrixEvaluator,
    PTCDecisionMatrixPlan,
    PTCDecisionOption,
)
from dual_pipeline.ptc.entity_resolution import (
    ALLOWED_ENTITY_PREFIXES,
    ALLOWED_STATEMENT_KINDS,
    PTCEntityResolutionPlan,
    PTCEntityResolver,
    PTCResolvedProjection,
    PTCTriple,
)
from dual_pipeline.ptc.hitl_sifter import (
    PTCAmbiguitySifter,
    PTCHITLCandidate,
    PTCHITLOption,
    PTCHITLQueueManager,
    PTCSiftResult,
)
from dual_pipeline.ptc.hybrid_query import (
    PTCDecisionEvidencePack,
    PTCHybridQueryExecutor,
    PTCHybridQueryPlan,
)
from dual_pipeline.ptc.sandbox import (
    PTCAstValidator,
    PTCSandbox,
)
from dual_pipeline.ptc.spooler import PTCOutputSpooler

__all__ = [
    # Contracts & Base
    "PTCError",
    "PTCExecutionContext",
    "PTCExecutionError",
    "PTCResultSummary",
    "PTCSecurityViolationError",
    "PTCStatus",
    "PTCTimeoutError",
    "TokenBudget",
    # Sandbox & Spooler
    "PTCAstValidator",
    "PTCSandbox",
    "PTCOutputSpooler",
    # Phase 1: Retrieval & Benchmarks
    "PTCHybridQueryExecutor",
    "PTCDecisionEvidencePack",
    "PTCHybridQueryPlan",
    "PTC11GateBenchmarkHarness",
    "PTC11GateReceipt",
    "PTC11GateReleasePlan",
    # Phase 2: Entity Grounding & Decision Evaluation
    "ALLOWED_ENTITY_PREFIXES",
    "ALLOWED_STATEMENT_KINDS",
    "PTCEntityResolver",
    "PTCEntityResolutionPlan",
    "PTCResolvedProjection",
    "PTCTriple",
    "PTCDecisionCriterion",
    "PTCDecisionEvaluationReceipt",
    "PTCDecisionMatrixEvaluator",
    "PTCDecisionMatrixPlan",
    "PTCDecisionOption",
    # Phase 3: Calibrated HITL & Context Fusion
    "PTCAmbiguitySifter",
    "PTCHITLCandidate",
    "PTCHITLOption",
    "PTCHITLQueueManager",
    "PTCSiftResult",
    "PTCContextFusionStreamer",
    "PTCFusedContextPayload",
    "PTCFusionPlan",
    "PTCLiveFastPathEvent",
]
