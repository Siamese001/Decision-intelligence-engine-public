"""Exception classes for the ingestion-reasoning boundary."""
from __future__ import annotations

from ..exceptions import DualPipelineRuntimeError


class IngestionReasoningError(DualPipelineRuntimeError):
    """Base error for the optional ingestion-reasoning boundary."""


class IngestionProviderError(IngestionReasoningError):
    """Safe typed provider failure that never contains secrets or raw content."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ProposalValidationError(IngestionReasoningError):
    """Strict model-output validation failure."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = tuple(errors)
