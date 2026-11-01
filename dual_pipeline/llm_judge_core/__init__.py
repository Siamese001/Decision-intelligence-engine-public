"""
dual_pipeline.llm_judge_core

Master Vector Orchestrator for deterministic and semantic vector evaluations.
Specialized LLM judges are in dual_pipeline.llm_judge_core.judges.
"""

from .adversarial_probe import (
    AdversarialAuditResult,
    BoundedAdversarialProbe,
)
from .adversarial_recap_auditor import (
    AdversarialRecapAuditor,
    AdversarialRecapAuditResult,
)
from .judge import (
    DialecticalEvaluationResult,
    DialecticalJudge,
    DialecticalOption,
    evaluate_dialectical_decision,
)
from .batch_judge import (
    BatchMicroJudgeEvaluator,
)
from .models import (
    BatchedEvaluationResult,
    JudgeAgenticLeverage,
    MicroJudgeScorecard,
)
from .orchestrator import (
    DecomposedMultiJudge,
)
from .prompts import (
    EXECUTIVE_EXOSKELETON_REVIEW_PROMPT,
    HARDENED_SVP_JUDGE_PROMPT,
)
from .reconciler import (
    CrossArtifactReconciler,
)

__all__ = [
    "JudgeAgenticLeverage",
    "MicroJudgeScorecard",
    "BatchedEvaluationResult",
    "BatchMicroJudgeEvaluator",
    "HARDENED_SVP_JUDGE_PROMPT",
    "EXECUTIVE_EXOSKELETON_REVIEW_PROMPT",
    "CrossArtifactReconciler",
    "DecomposedMultiJudge",
    "BoundedAdversarialProbe",
    "AdversarialAuditResult",
    "AdversarialRecapAuditor",
    "AdversarialRecapAuditResult",
    "DialecticalJudge",
    "DialecticalOption",
    "DialecticalEvaluationResult",
    "evaluate_dialectical_decision",
]
