"""Unified Agentic Pipeline (Decision Intelligence Engine).

This package is the canonical SSOT namespace for the Unified Agentic Pipeline:
    U0 -> L1 -> L0 -> C0-PA -> L2 -> X1-X3 (exit) -> EVALS

Provides full backward and forward compatibility with the legacy `dual_pipeline`
namespace while establishing the single-pipeline standard across the codebase.
"""

from __future__ import annotations

import importlib
import sys
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import ModuleSpec
from typing import Any

import dual_pipeline


class _UnifiedPipelineAliasLoader(Loader):
    """PEP 451 Loader that transparently proxies imports to the underlying module."""

    def __init__(self, target_mod: Any) -> None:
        self.target_mod = target_mod

    def create_module(self, spec: ModuleSpec) -> Any:
        return self.target_mod

    def exec_module(self, module: Any) -> None:
        pass

    def get_code(self, fullname: str) -> Any:
        loader = getattr(self.target_mod, "__loader__", None)
        if loader and hasattr(loader, "get_code"):
            return loader.get_code(getattr(self.target_mod, "__name__", fullname))
        return None

    def get_source(self, fullname: str) -> Any:
        loader = getattr(self.target_mod, "__loader__", None)
        if loader and hasattr(loader, "get_source"):
            return loader.get_source(getattr(self.target_mod, "__name__", fullname))
        return None

    def is_package(self, fullname: str) -> bool:
        loader = getattr(self.target_mod, "__loader__", None)
        if loader and hasattr(loader, "is_package"):
            return loader.is_package(getattr(self.target_mod, "__name__", fullname))
        return False



class _UnifiedPipelineModuleAliasFinder(MetaPathFinder):
    """Transparently alias any `unified_pipeline.<submodule>` to `dual_pipeline.<submodule>`."""

    @classmethod
    def find_spec(cls, fullname: str, path: Any = None, target: Any = None) -> ModuleSpec | None:
        if fullname.startswith("unified_pipeline."):
            suffix = fullname[len("unified_pipeline."):]
            target_name = f"dual_pipeline.{suffix}"
            try:
                target_mod = importlib.import_module(target_name)
                origin = getattr(target_mod, "__file__", None)
                spec = ModuleSpec(fullname, _UnifiedPipelineAliasLoader(target_mod), origin=origin)
                spec.has_location = bool(origin)
                return spec
            except ModuleNotFoundError:
                return None
        return None


# Install alias finder if not already present
if not any(
    isinstance(finder, type) and finder.__name__ == "_UnifiedPipelineModuleAliasFinder"
    for finder in sys.meta_path
):
    sys.meta_path.insert(0, _UnifiedPipelineModuleAliasFinder)


# Re-export core Layer 0 abstractions
from dual_pipeline.document_pipeline import DocumentPipeline
from dual_pipeline.intake import IntakeEngine
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.notes_pipeline import NotesPipeline

# Re-export canonical engines
from dual_pipeline.query import QueryEngine

# Re-export canonical pipelines
from dual_pipeline.transcript_pipeline import RawTranscriptPipeline
from dual_pipeline.unified_agentic_pipeline import (
    PipelineExecutionReceipt,
    PipelineSequencingViolationError,
    PipelineStage,
    UnifiedAgenticPipeline,
    UnifiedAgenticPipelineTracker,
)

__all__ = [
    "DocumentPipeline",
    "IntakeEngine",
    "LiveMeetingRecorder",
    "NotesPipeline",
    "PipelineExecutionReceipt",
    "PipelineSequencingViolationError",
    "PipelineStage",
    "QueryEngine",
    "RawTranscriptPipeline",
    "UnifiedAgenticPipeline",
    "UnifiedAgenticPipelineTracker",
]


def __getattr__(name: str) -> Any:
    """Dynamically resolve un-exported sub-modules or attributes from dual_pipeline."""
    if name in globals():
        return globals()[name]

    # Try sub-module import from dual_pipeline.<name>
    try:
        mod = importlib.import_module(f"dual_pipeline.{name}")
        sys.modules[f"unified_pipeline.{name}"] = mod
        return mod
    except ModuleNotFoundError:
        pass

    # Try attribute lookup on dual_pipeline module
    if hasattr(dual_pipeline, name):
        return getattr(dual_pipeline, name)

    raise AttributeError(f"module 'unified_pipeline' has no attribute '{name}'")


def __dir__() -> list[str]:
    """Return available attributes including dual_pipeline exports."""
    return sorted(set(list(globals().keys()) + dir(dual_pipeline) + __all__))
