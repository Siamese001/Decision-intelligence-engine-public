"""
dual_pipeline/transcript_pipeline_core/pipeline.py

RawTranscriptPipeline — Agentic transcript synthesis pipeline across all 8 stages:
  U0 -> L1-L0 -> C0 -> PA -> L2 -> Exit gates X1-X3 -> UWG -> evals.
"""
from __future__ import annotations

import fcntl
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from dual_pipeline.audio_ingestion_core.container_validator import (
    validate_audio_container_headers,
)
from dual_pipeline.auto_scaffolder import PreflightAutoScaffolder
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.pipeline import CanonicalAgenticPipeline
from dual_pipeline.model_registry import ModelTier, resolve_tier_model
from dual_pipeline.transcript_pipeline_core.normalizer import (
    _extract_docx_text,
    _normalize_teams_text,
)
from dual_pipeline.transcript_pipeline_core.post_l2_review import (
    TranscriptL1PostExecutionReviewer,
)
from dual_pipeline.transcript_pipeline_core.uwg_deployer import (
    atomic_write_text,
    deploy_uwg_artifacts,
)
from dual_pipeline.transcript_stages import (
    C0ConformanceGate,
    C0RosterContextGate,
    L0DeterministicRouter,
    L1ForensicCognition,
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
from dual_pipeline.transcript_stages_core.hitl_guard import assert_no_u0_hitl_bypass
from dual_pipeline.upstream_audio_qa import UpstreamAudioQAGatekeeper

logger = logging.getLogger(__name__)

DEFAULT_MODEL, DEFAULT_EFFORT = resolve_tier_model(ModelTier.FRONTIER_HIGH_REASONING)
DEFAULT_JUDGE_MODEL, DEFAULT_JUDGE_EFFORT = resolve_tier_model(ModelTier.DIALECTICAL_JUDGE)


class RawTranscriptPipeline(CanonicalAgenticPipeline):
    """
    Agentic transcript synthesis pipeline across 8 modularized stages.
    Unified under CanonicalAgenticPipeline.
    """

    @property
    def pipeline_name(self) -> str:
        return "RawTranscriptPipeline"

    def __init__(
        self,
        provider_type: str = "llm",
        model_name: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        modality: Optional[CanonicalModality] = None,
    ):
        super().__init__(
            provider_type=provider_type,
            model_name=model_name,
            reasoning_effort=reasoning_effort,
            modality=modality,
        )
        self.provider_type = provider_type
        self.model_name = model_name
        self.reasoning_effort = reasoning_effort

        self.u0 = U0IngestionBoundary()
        self.l1 = L1ForensicCognition()
        self.l0 = L0DeterministicRouter()
        self.c0_native = C0ConformanceGate()
        self.c0_undiarized = C0RosterContextGate()
        self.pa = PAPacketAssembler()

        def_model, def_effort = resolve_tier_model(ModelTier.FRONTIER_HIGH_REASONING)
        eff = reasoning_effort or def_effort or "high"
        mdl = model_name or def_model

        provider = (
            OfflineTranscriptSynthesizer()
            if provider_type == "offline"
            else LLMTranscriptSynthesizer(model=mdl, reasoning_effort=eff)
        )
        self.provider = provider
        self.auto_scaffolder = PreflightAutoScaffolder(provider)

        self.l2_forensic = L2ForensicNormalizerExecutor(provider)
        self.l2_recap = L2ExecutiveRecapExecutor(provider)
        self.l2_graph = L2GraphExtractorExecutor(provider)
        self.l2 = L2SynthesisExecutor(provider)

        self.x_gates = XExitGatesEvaluator()
        self.upstream_qa = UpstreamAudioQAGatekeeper()
        self.l1_reviewer = TranscriptL1PostExecutionReviewer()

    @staticmethod
    def _extract_docx_text(path: Path) -> str:
        return _extract_docx_text(path)

    @staticmethod
    def _normalize_teams_text(raw_text: str) -> str:
        return _normalize_teams_text(raw_text)

    def run(
        self,
        input_path: str,
        metadata: Optional[Dict[str, Any]] = None,
        output_dir: str = "auto",
        ingestion_context: Optional[Dict[str, Any]] = None,
        presentation_path: Optional[Union[str, Path]] = None,
        attachments: Optional[Union[str, Path, List[Union[str, Path]]]] = None,
        attachment_dir: Optional[Union[str, Path]] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        max_judge_retries: int = 1,
        judge_model: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
        mutate_graph: bool = True,
        directive_id: Optional[str] = None,
        reviewer: Optional[str] = None,
        rationale: Optional[str] = None,
        embedding_backend: str = "bge-m3",
        signal_envelope: Optional[Any] = None,
        timeout_seconds: Optional[int] = None,
        auto_mutate_on_pass: bool = True,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        meta = dict(metadata or {})
        meta.setdefault("provider_type", self.provider_type)
        meta.setdefault("provider", self.provider_type)
        if ingestion_context:
            meta["ingestion_context"] = ingestion_context
        assert_no_u0_hitl_bypass(meta)

        p = Path(input_path)
        if p.is_file() and p.suffix.lower() in (".wav", ".m4a", ".mp3", ".aac"):
            # Wave 1: U0 Pre-flight container integrity check
            validate_audio_container_headers(p)

            # Wave 1: Check for active file locks (race condition protection)
            lock_file = p.with_name(f".{p.name}.flock")
            if lock_file.exists():
                try:
                    with open(lock_file, "r") as f:
                        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        fcntl.flock(f, fcntl.LOCK_UN)
                except BlockingIOError as err:
                    raise RuntimeError(f"Audio file '{p.name}' is currently locked and actively being written.") from err

        return super().run(
            input_path=input_path,
            metadata=meta,
            output_dir=output_dir,
            presentation_path=presentation_path,
            attachments=attachments,
            attachment_dir=attachment_dir,
            progress_callback=progress_callback,
            max_judge_retries=max_judge_retries,
            judge_model=judge_model,
            reasoning_effort=reasoning_effort,
            mutate_graph=mutate_graph,
            directive_id=directive_id,
            reviewer=reviewer,
            rationale=rationale,
            embedding_backend=embedding_backend,
            signal_envelope=signal_envelope,
            timeout_seconds=timeout_seconds,
            auto_mutate_on_pass=auto_mutate_on_pass,
            **kwargs,
        )

    @staticmethod
    def _atomic_write_text(path: Path, content: str) -> None:
        """Compatibility proxy for atomic file write."""
        atomic_write_text(path, content)

    def _uwg_write(
        self,
        artifacts: Dict[str, Any],
        metadata: Dict[str, Any],
        output_dir: str,
        qa_report: Optional[Dict[str, Any]] = None,
        judge_report: Optional[Dict[str, Any]] = None,
        final_decision: Optional[Dict[str, Any]] = None,
        presentation_path: Optional[Union[str, Path]] = None,
    ) -> Dict[str, Any]:
        """Compatibility proxy for UWG artifact deployment."""
        return deploy_uwg_artifacts(
            artifacts=artifacts,
            metadata=metadata,
            output_dir=output_dir,
            qa_report=qa_report,
            judge_report=judge_report,
            final_decision=final_decision,
            presentation_path=presentation_path,
        )
