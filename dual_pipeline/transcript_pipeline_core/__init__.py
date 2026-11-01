"""
dual_pipeline.transcript_pipeline_core

Modularized implementation of RawTranscriptPipeline, span recorders,
audio/text format constants, and text normalizers.
"""

from .constants import _PIPELINE_STEPS, AUDIO_EXTENSIONS, TEXT_EXTENSIONS
from .normalizer import _extract_docx_text, _normalize_teams_text, _sanitize_filename
from .progress import PipelineProgressReporter
from .recorder import PipelineSpanRecorder


def __getattr__(name: str):
    if name == "RawTranscriptPipeline":
        from .pipeline import RawTranscriptPipeline
        return RawTranscriptPipeline
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "RawTranscriptPipeline",
    "PipelineSpanRecorder",
    "PipelineProgressReporter",
    "_sanitize_filename",
    "_extract_docx_text",
    "_normalize_teams_text",
    "AUDIO_EXTENSIONS",
    "TEXT_EXTENSIONS",
    "_PIPELINE_STEPS",
]

