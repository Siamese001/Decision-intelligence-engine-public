"""
dual_pipeline/canonical_pipeline/strategies/qa.py

Polymorphic QA gate and micro-judge evaluation strategies for Canonical Agentic Pipeline.
Decouples Stage X1-X3 exit gate verification based on CanonicalModality:
1. FullMeetingQAGateStrategy: Evaluates Gate 1, 2, 3, consistency, and 3 micro-judges.
2. NotesMeetingQAGateStrategy: Evaluates Gate 2, 3, and micro-judges 2 & 3 (01 skipped).
3. DocumentQAGateStrategy: Evaluates Gate 3 and micro-judge 3 (01 & 02 skipped).
"""
from __future__ import annotations

import abc
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult

logger = logging.getLogger(__name__)


@dataclass
class QAEvaluationResult:
    """Encapsulates the evaluation output from Stage X1-X3."""
    passed: bool
    qa_report: Dict[str, Any]
    judge_report: Dict[str, Any]
    final_decision: Dict[str, Any]
    authoritative_write_authorized: bool


class QAGateStrategy(abc.ABC):
    """Abstract strategy protocol for Stage X1-X3 Exit Gates and Micro-Judges."""

    @property
    @abc.abstractmethod
    def modality(self) -> CanonicalModality:
        """The canonical modality this strategy serves."""

    def evaluate(
        self,
        synthesis_result: SynthesisResult,
        source_text: str,
        metadata: Dict[str, Any],
        attempt: int = 1,
        max_attempts: int = 3,
        mock: bool = False,
        judge_model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        fast_judge: bool = False,
    ) -> QAEvaluationResult:
        """Evaluates deterministic gates and micro-judges via UnifiedPipelineQAEvaluator."""
        from dual_pipeline.canonical_pipeline.evaluator import UnifiedPipelineQAEvaluator

        auth, qa_rep, judge_rep, final_dec = UnifiedPipelineQAEvaluator.evaluate(
            modality=self.modality,
            synthesis_result=synthesis_result,
            source_text=source_text,
            metadata=metadata,
            attempt=attempt,
            max_attempts=max_attempts,
            mock=mock,
            judge_model=judge_model,
            reasoning_effort=reasoning_effort,
            fast_judge=fast_judge,
        )
        return QAEvaluationResult(
            passed=auth,
            qa_report=qa_rep,
            judge_report=judge_rep,
            final_decision=final_dec,
            authoritative_write_authorized=auth,
        )


class FullMeetingQAGateStrategy(QAGateStrategy):
    """QA strategy for Full Meetings (Audio and Diarized Transcripts)."""
    @property
    def modality(self) -> CanonicalModality:
        return CanonicalModality.MEETING_TRANSCRIPT


class NotesMeetingQAGateStrategy(QAGateStrategy):
    """QA strategy for Meeting Notes & Summaries (01 skipped, 02 and 03 evaluated)."""
    @property
    def modality(self) -> CanonicalModality:
        return CanonicalModality.MEETING_NOTES


class DocumentQAGateStrategy(QAGateStrategy):
    """QA strategy for Written Documents (01 and 02 skipped, 03 evaluated)."""
    @property
    def modality(self) -> CanonicalModality:
        return CanonicalModality.DOCUMENT


def get_qa_strategy(modality: CanonicalModality) -> QAGateStrategy:
    """Factory creating the appropriate QAGateStrategy for a given modality."""
    if modality in (CanonicalModality.AUDIO, CanonicalModality.MEETING_TRANSCRIPT):
        return FullMeetingQAGateStrategy()
    if modality == CanonicalModality.MEETING_NOTES:
        return NotesMeetingQAGateStrategy()
    if modality == CanonicalModality.DOCUMENT:
        return DocumentQAGateStrategy()
    return FullMeetingQAGateStrategy()
