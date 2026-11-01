"""Unified exception hierarchy for the NEE dual-pipeline runtime.

A thin but intentional base layer that lets callers differentiate
pipeline errors from builtin exceptions while preserving backward
compatibility with existing ``except ValueError`` / ``except RuntimeError``
handlers via multiple inheritance.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class DualPipelineError(Exception):
    """Root of every dual-pipeline exception.

    Catching ``DualPipelineError`` captures any pipeline-raised error.
    Subclasses double-inherit from the relevant builtin so that existing
    ``except ValueError:`` handlers continue to work.
    """


class DualPipelineValueError(DualPipelineError, ValueError):
    """Domain-level or input-validation error within the pipeline."""


class DualPipelineRuntimeError(DualPipelineError, RuntimeError):
    """Infrastructure or operational failure within the pipeline."""


class ProviderError(DualPipelineRuntimeError):
    """An external provider call failed or returned invalid output."""


class ConcurrentIngestionError(DualPipelineRuntimeError):
    """Raised when another process is concurrently ingesting the same meeting."""


class AudioContainerValidationError(DualPipelineValueError):
    """Raised when an audio file has malformed container headers or invalid parameters."""


class TranscriptEmptyError(DualPipelineValueError):
    """Raised when the transcriber yields an empty transcript string, preventing empty LLM cascades."""


class LedgerIntegrityTamperedError(DualPipelineRuntimeError):
    """Raised when the HITL decision ledger envelope or hash chain has been tampered with."""


class InsufficientMemoryError(DualPipelineRuntimeError):
    """Raised when available system memory is insufficient to safely run heavy model inference."""


class SourceIntegrityError(DualPipelineValueError):
    """Raised when raw source input file fails byte-level hash or size verification."""


class GraphMutationSecurityError(DualPipelineRuntimeError, PermissionError):
    """Raised when a graph mutation fails cryptographic signature verification."""


class ProjectResolutionRequiredError(DualPipelineValueError):
    """Raised when project assignment cannot be deterministically or dynamically resolved and requires operator input."""


class ZeroSignalError(DualPipelineValueError):
    """Raised when source material falls below minimum semantic yield threshold."""

class IdempotentNoOpIntakeError(DualPipelineRuntimeError):
    """Raised when source material yields zero substantive candidates, short-circuiting the pipeline."""

class DiarizationCollapseError(DualPipelineRuntimeError):
    """Raised when acoustic diarization detects a single-speaker collapse (>85% duration)
    or fewer acoustic clusters than the expected roster count."""
    pass

class AttributionAnomalyHaltError(DualPipelineRuntimeError):
    """Raised when Gate C or Gate 1.2 detects an unrecoverable speaker attribution anomaly,
    halting the pipeline and quarantining drafts instead of running blind LLM retries."""

    def __init__(
        self,
        message: str,
        findings: Optional[List[Any]] = None,
        quarantined_paths: Optional[List[str]] = None,
        anomaly_codes: Optional[List[str]] = None,
    ) -> None:
        super().__init__(message)
        self.findings = findings or []
        self.quarantined_paths = quarantined_paths or []
        self.anomaly_codes = anomaly_codes or []

class UnmappedSpeakerError(DualPipelineRuntimeError):
    """Raised when an unmapped generic speaker (e.g. Speaker 1) is detected
    and no speaker map is provided, immediately halting ingestion."""
    pass

class ProviderAccessViolation(DualPipelineRuntimeError):
    """Raised when a provider is instantiated before L2D explicitly authorizes it."""
    pass

class RetrievalCoverageInsufficientError(DualPipelineRuntimeError):
    """Raised when set-logic intersections verify entity coverage is missing prior to L2D."""
    pass

class PipelineTokenBudgetExhaustedError(DualPipelineRuntimeError):
    """Raised when the directive-scoped pipeline token budget is exhausted."""
    pass


class ChildDispatchBlockedError(DualPipelineRuntimeError):
    """Raised when Stage 02 / Stage 03 child dispatch is blocked due to Output 01 prerequisite failures."""

    def __init__(self, message: str, reasons: Optional[List[str]] = None) -> None:
        super().__init__(message)
        self.reasons = reasons or []


class HITLRequiredError(DualPipelineRuntimeError):
    """Raised when an unresolvable ambiguity or Human-In-The-Loop resolution is required before proceeding."""

    def __init__(
        self,
        message: str,
        unmapped_speakers: Optional[List[str]] = None,
        hitl_type: str = "speaker_attribution",
        missing_fields: Optional[List[str]] = None,
        options: Optional[List[Dict[str, Any]]] = None,
        prompt: Optional[str] = None,
        evidence: Optional[Dict[str, Any]] = None,
        review_contexts: Optional[List[Dict[str, Any]]] = None,
        review_items: Optional[List[Dict[str, Any]]] = None,
        context_digest: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.unmapped_speakers = unmapped_speakers or []
        self.hitl_type = hitl_type
        self.missing_fields = missing_fields or []
        self.options = options or []
        self.prompt = prompt or ""
        self.evidence = evidence or {}
        self.review_contexts = review_contexts or []
        self.review_items = review_items or []
        self.context_digest = context_digest or ""
        if self.review_contexts and "review_contexts" not in self.evidence:
            self.evidence["review_contexts"] = self.review_contexts
        if self.review_items and "review_items" not in self.evidence:
            self.evidence["review_items"] = self.review_items
        if self.context_digest and "context_digest" not in self.evidence:
            self.evidence["context_digest"] = self.context_digest


class BudgetExhaustedError(DualPipelineRuntimeError):
    """Raised when an operation or modality request budget ceiling is exceeded."""
    pass


class LatePublicationBlockedError(DualPipelineRuntimeError):
    """Raised when a late or cancelled worker attempt tries to publish artifacts after scope termination."""
    pass


class OperationCancelledError(DualPipelineRuntimeError):
    """Raised when an operation or stage reservation is attempted on a cancelled execution scope."""
    pass


class CacheIntegrityViolationError(DualPipelineRuntimeError):
    """Raised when cached transcript sidecars (.segments.json, .transcript_cache.json, .utterances.json)
    fail cryptographic seal verification, hash parity, or turn-count consistency checks."""
    pass

