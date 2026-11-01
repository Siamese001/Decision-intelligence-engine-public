"""Governed dual-use intake, release, retrieval, and learning runtime.

The package implements Waves 2 through 6 of ``NEE-KG-DUAL-PIPELINE-01``.
The approved V55 graph remains an immutable seed; runtime releases operate on
versioned copies and activate a graph/index pair through one atomic manifest.
"""

from .core import RUNTIME_VERSION
from .exceptions import (
    DualPipelineError,
    DualPipelineRuntimeError,
    DualPipelineValueError,
    ProviderError,
)

__all__ = [
    "RUNTIME_VERSION",
    "DualPipelineError",
    "DualPipelineRuntimeError",
    "DualPipelineValueError",
    "ProviderError",
]

