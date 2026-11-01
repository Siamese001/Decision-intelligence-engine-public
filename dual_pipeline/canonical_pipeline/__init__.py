"""
dual_pipeline/canonical_pipeline/__init__.py

Canonical Agentic Pipeline Package:
Consolidates Audio, Diarized Transcripts, Copilot Notes, and Written Documents
under a single unified 7-stage orchestrator.
Uses PEP 562 lazy __getattr__ to defer heavy imports (e.g. LLM judge, synthesis strategies)
when importing submodules like worktree_naming.
"""
from __future__ import annotations

import importlib
from typing import Any

__all__ = [
    "CanonicalAgenticPipeline",
    "CanonicalModality",
    "detect_modality",
    "get_deliverables_for_modality",
    "SynthesisStrategy",
    "SynthesisResult",
    "get_synthesis_strategy",
    "QAGateStrategy",
    "QAEvaluationResult",
    "get_qa_strategy",
]

_SYMBOL_TO_MODULE = {
    "CanonicalModality": "dual_pipeline.canonical_pipeline.modalities",
    "detect_modality": "dual_pipeline.canonical_pipeline.modalities",
    "get_deliverables_for_modality": "dual_pipeline.canonical_pipeline.modalities",
    "CanonicalAgenticPipeline": "dual_pipeline.canonical_pipeline.pipeline",
    "QAEvaluationResult": "dual_pipeline.canonical_pipeline.strategies.qa",
    "QAGateStrategy": "dual_pipeline.canonical_pipeline.strategies.qa",
    "get_qa_strategy": "dual_pipeline.canonical_pipeline.strategies.qa",
    "SynthesisResult": "dual_pipeline.canonical_pipeline.strategies.synthesis",
    "SynthesisStrategy": "dual_pipeline.canonical_pipeline.strategies.synthesis",
    "get_synthesis_strategy": "dual_pipeline.canonical_pipeline.strategies.synthesis",
}


def __getattr__(name: str) -> Any:
    mod_name = _SYMBOL_TO_MODULE.get(name)
    if mod_name is not None:
        mod = importlib.import_module(mod_name)
        val = getattr(mod, name)
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + __all__)
