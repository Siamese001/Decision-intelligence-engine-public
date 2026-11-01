"""
transcript_qa.py — Deterministic QA Gates & Multi-Micro-Judge Engine.

Facade preserving 100% backward compatibility with dual_pipeline.transcript_qa.
Underlying implementations are modularized under dual_pipeline.transcript_qa_core.
"""

from __future__ import annotations

import logging

from dual_pipeline.core import load_ssot_env
from dual_pipeline.llm_judge import CrossArtifactReconciler, DecomposedMultiJudge
from dual_pipeline.transcript_models import CanonicalSignalProjectionV57
from dual_pipeline.transcript_qa_core import (
    DeterministicHealer,
    MonotonicGuard,
    ProtectedPathMutationError,
    SurgicalPatcher,
    evaluate_all_qa_gates,
    evaluate_judge_and_release,
    run_cross_artifact_consistency,
    run_qa_gate_1,
    run_qa_gate_2,
    run_qa_gate_3,
)

logger = logging.getLogger(__name__)

__all__ = [
    "run_qa_gate_1",
    "run_qa_gate_2",
    "run_qa_gate_3",
    "run_cross_artifact_consistency",
    "evaluate_all_qa_gates",
    "evaluate_judge_and_release",
    "DeterministicHealer",
    "SurgicalPatcher",
    "ProtectedPathMutationError",
    "MonotonicGuard",
    "DecomposedMultiJudge",
    "CrossArtifactReconciler",
    "CanonicalSignalProjectionV57",
    "load_ssot_env",
]

