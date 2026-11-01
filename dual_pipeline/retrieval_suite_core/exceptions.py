"""Exceptions for retrieval suite generation."""
from __future__ import annotations

from ..exceptions import DualPipelineRuntimeError


class AutomaticSuiteBuildError(DualPipelineRuntimeError):
    """Stable, fail-closed automatic suite construction failure."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")
