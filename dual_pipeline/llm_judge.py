"""
Decomposed Multi-Micro-Judge & Hardened Rubrics Engine.

Facade preserving 100% backward compatibility with dual_pipeline.llm_judge.
Underlying implementations are modularized under dual_pipeline.llm_judge_core.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from dual_pipeline.llm_judge_core import DecomposedMultiJudge
from dual_pipeline.llm_judge_core.calibration_protocol import (
    get_calibrated_borderline_band,
    record_judge_override,
)
from dual_pipeline.llm_judge_core.judges import (
    AgenticLeverageJudge,
    EnterpriseGraphReviewJudge,
    EnterpriseImpactJudge,
    ExecutiveRecapJudge,
    ForensicAccuracyJudge,
    ForensicTranscriptJudge,
    GraphExtractionJudge,
    GraphMutationJudge,
    GraphProvenanceJudge,
    GraphTransportJudge,
    HolisticQualityJudge,
    HolisticReleaseJudge,
)
from dual_pipeline.llm_judge_core.judge import (
    DialecticalEvaluationResult,
    DialecticalJudge,
    DialecticalOption,
    evaluate_dialectical_decision,
)
from dual_pipeline.llm_judge_core.models import JudgeAgenticLeverage, MicroJudgeScorecard
from dual_pipeline.llm_judge_core.prompts import (
    EXECUTIVE_EXOSKELETON_REVIEW_PROMPT,
    HARDENED_SVP_JUDGE_PROMPT,
)
from dual_pipeline.llm_judge_core.reconciler import CrossArtifactReconciler
from dual_pipeline.telemetry import (
    record_eval_result,
    record_quarantine_event,
    trace_llm_execution,
)

logger = logging.getLogger(__name__)

__all__ = [
    "JudgeAgenticLeverage",
    "MicroJudgeScorecard",
    "HARDENED_SVP_JUDGE_PROMPT",
    "EXECUTIVE_EXOSKELETON_REVIEW_PROMPT",
    "AgenticLeverageJudge",
    "ForensicTranscriptJudge",
    "ForensicAccuracyJudge",
    "ExecutiveRecapJudge",
    "GraphExtractionJudge",
    "GraphMutationJudge",
    "GraphProvenanceJudge",
    "GraphTransportJudge",
    "EnterpriseGraphReviewJudge",
    "EnterpriseImpactJudge",
    "CrossArtifactReconciler",
    "DecomposedMultiJudge",
    "HolisticReleaseJudge",
    "HolisticQualityJudge",
    "get_calibrated_borderline_band",
    "record_judge_override",
    "DialecticalJudge",
    "DialecticalOption",
    "DialecticalEvaluationResult",
    "evaluate_dialectical_decision",
    "BaseModel",
    "Field",
    "trace_llm_execution",
    "record_eval_result",
    "record_quarantine_event",
]
