"""
RawTranscriptPipeline — agentic 3-path transcript synthesis pipeline.

Facade preserving 100% backward compatibility with dual_pipeline.transcript_pipeline.
Underlying implementations are modularized under dual_pipeline.transcript_pipeline_core.
"""

from __future__ import annotations

import logging

from dual_pipeline.audio_ingestion import (
    BaseAudioTranscriber,
    LocalFasterWhisperTranscriber,
    OpenAIAudioTranscriber,
    WhisperXAudioTranscriber,
    get_default_transcriber,
)
from dual_pipeline.auto_scaffolder import PreflightAutoScaffolder
from dual_pipeline.query_router_core.project_router import determine_project_directory
from dual_pipeline.telemetry import RunRecorder
from dual_pipeline.transcript_pipeline_core import (
    _PIPELINE_STEPS,
    AUDIO_EXTENSIONS,
    TEXT_EXTENSIONS,
    PipelineSpanRecorder,
    RawTranscriptPipeline,
    _sanitize_filename,
)
from dual_pipeline.transcript_qa import evaluate_judge_and_release
from dual_pipeline.transcript_stages import (
    HITLRequiredError,
    L0DeterministicRouter,
    L1ForensicCognition,
    L1PlanContract,
    L2ExecutiveRecapExecutor,
    L2ForensicNormalizerExecutor,
    L2GraphExtractorExecutor,
    L2SynthesisExecutor,
    LLMTranscriptSynthesizer,
    OfflineTranscriptSynthesizer,
    PAPacketAssembler,
    U0IngestionBoundary,
    XExitGatesEvaluator,
)
from dual_pipeline.upstream_audio_qa import UpstreamAudioQAGatekeeper

logger = logging.getLogger(__name__)

__all__ = [
    "AUDIO_EXTENSIONS",
    "TEXT_EXTENSIONS",
    "_PIPELINE_STEPS",
    "_sanitize_filename",
    "PipelineSpanRecorder",
    "RawTranscriptPipeline",
    "U0IngestionBoundary",
    "L1ForensicCognition",
    "L1PlanContract",
    "L0DeterministicRouter",
    "PAPacketAssembler",
    "L2ForensicNormalizerExecutor",
    "L2ExecutiveRecapExecutor",
    "L2GraphExtractorExecutor",
    "L2SynthesisExecutor",
    "XExitGatesEvaluator",
    "OfflineTranscriptSynthesizer",
    "LLMTranscriptSynthesizer",
    "HITLRequiredError",
    "PreflightAutoScaffolder",
    "BaseAudioTranscriber",
    "OpenAIAudioTranscriber",
    "WhisperXAudioTranscriber",
    "LocalFasterWhisperTranscriber",
    "get_default_transcriber",
    "RunRecorder",
    "evaluate_judge_and_release",
    "determine_project_directory",
    "UpstreamAudioQAGatekeeper",
]

if __name__ == "__main__":
    import sys
    sys.stderr.write(
        "[GOVERNANCE VIOLATION] Direct CLI execution of dual_pipeline.transcript_pipeline is strictly prohibited.\n"
        "Use the canonical CLI entrypoint instead: 'python nee.py ingest-meeting ...'\n"
    )
    sys.exit(1)
