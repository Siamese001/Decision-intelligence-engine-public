"""Specialized adversarial benchmark evaluators for acoustic, extraction, retrieval, and decision systems.

Provides both canonical domain benchmark names and backward-compatible legacy pipeline aliases.
"""
from __future__ import annotations

from dual_pipeline.evals.pipelines.models import (
    AcousticBenchmarkScorecard,
    DecisionBenchmarkScorecard,
    ExtractionBenchmarkScorecard,
    PipelineADEvalMasterReport,
    PipelineAEvalScorecard,
    PipelineBEvalScorecard,
    PipelineCEvalScorecard,
    PipelineDEvalScorecard,
    RetrievalBenchmarkScorecard,
    UnifiedBenchmarkMasterReport,
)
from dual_pipeline.evals.pipelines.pipeline_a_evaluator import (
    AcousticBenchmarkEvaluator,
    PipelineAEvaluator,
)
from dual_pipeline.evals.pipelines.pipeline_b_evaluator import (
    ExtractionBenchmarkEvaluator,
    PipelineBEvaluator,
)
from dual_pipeline.evals.pipelines.pipeline_c_evaluator import (
    PipelineCEvaluator,
    RetrievalBenchmarkEvaluator,
)
from dual_pipeline.evals.pipelines.pipeline_d_evaluator import (
    DecisionBenchmarkEvaluator,
    PipelineDEvaluator,
)

__all__ = [
    # Canonical domain benchmark scorecards & reports
    "AcousticBenchmarkScorecard",
    "ExtractionBenchmarkScorecard",
    "RetrievalBenchmarkScorecard",
    "DecisionBenchmarkScorecard",
    "UnifiedBenchmarkMasterReport",
    # Canonical domain benchmark evaluators
    "AcousticBenchmarkEvaluator",
    "ExtractionBenchmarkEvaluator",
    "RetrievalBenchmarkEvaluator",
    "DecisionBenchmarkEvaluator",
    # Backward-compatible legacy aliases
    "PipelineAEvalScorecard",
    "PipelineBEvalScorecard",
    "PipelineCEvalScorecard",
    "PipelineDEvalScorecard",
    "PipelineADEvalMasterReport",
    "PipelineAEvaluator",
    "PipelineBEvaluator",
    "PipelineCEvaluator",
    "PipelineDEvaluator",
]
