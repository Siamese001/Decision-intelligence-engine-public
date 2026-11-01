"""
dual_pipeline.transcript_qa_core

Modularized deterministic QA gates, cross-artifact consistency checks,
and multi-micro-judge release evaluation.
"""

from .consistency import run_cross_artifact_consistency
from .evaluator import evaluate_all_qa_gates, evaluate_judge_and_release
from .gate1 import run_qa_gate_1
from .gate2 import run_qa_gate_2
from .gate3 import run_qa_gate_3
from .graph_healer import heal_evidence_locators
from .healing_mesh import DeterministicHealer
from .monotonic_guard import MonotonicGuard
from .signal_density import (
    RigorTier,
    SignalDensityProfile,
    compute_technical_signal_density,
)
from .surgical_patcher import ProtectedPathMutationError, SurgicalPatcher

__all__ = [
    "heal_evidence_locators",
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
    "compute_technical_signal_density",
    "RigorTier",
    "SignalDensityProfile",
    "SelectiveDefectEvaluator",
    "SelectiveEvaluationReport",
    "DefectFinding",
    "DimensionResult",
    "FailureType",
    "JudgeFailureRecord",
    "JudgeFailureDiagnosticsCollector",
]

from .judge_failure_diagnostics import (
    FailureType,
    JudgeFailureDiagnosticsCollector,
    JudgeFailureRecord,
)
from .selective_defect_evaluator import (
    DefectFinding,
    DimensionResult,
    SelectiveDefectEvaluator,
    SelectiveEvaluationReport,
)

