"""Reuse exactly bound transcript Stage 02/03 synthesis while rerunning all current release checks."""
from __future__ import annotations

import hashlib
import json
import logging
from contextvars import ContextVar
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from dual_pipeline.canonical_pipeline.attempt_binding import verify_attempt_binding
from dual_pipeline.canonical_pipeline.concurrency import MeetingIngestionLock
from dual_pipeline.canonical_pipeline.document_resume import is_document_request
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.strategies.qa import QAEvaluationResult
from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
from dual_pipeline.canonical_pipeline.transcript_resume_binding import (
    build_transcript_resume_binding,
)
from dual_pipeline.checkpoint import PipelineCheckpointManager

logger = logging.getLogger("CanonicalAgenticPipeline.transcript_resume")

_ACTIVE_TRANSCRIPT_CHECKPOINT: ContextVar[PipelineCheckpointManager | None] = ContextVar(
    "active_transcript_checkpoint", default=None,
)


def reset_transcript_checkpoint() -> None:
    """Clear active checkpoint context variable."""
    _ACTIVE_TRANSCRIPT_CHECKPOINT.set(None)


def lock_transcript_source(
    stack: Any, metadata: Dict[str, Any], source_text: str, runtime_root: Path
) -> None:
    """Acquires ingestion lock for transcript source and registers checkpoint path."""
    if is_document_request(metadata):
        return
    source = metadata.get("raw_source_sha256") or hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    proj = str(metadata.get("project") or "unresolved")
    identity = hashlib.sha256(f"{proj}:{source}".encode("utf-8")).hexdigest()
    locks_dir = runtime_root / "locks"
    checkpoints_dir = runtime_root / "checkpoints"
    locks_dir.mkdir(parents=True, exist_ok=True)
    checkpoints_dir.mkdir(parents=True, exist_ok=True)
    stack.enter_context(MeetingIngestionLock("transcript_" + identity, lock_dir=locks_dir))
    metadata["_transcript_checkpoint_path"] = str(checkpoints_dir / f"transcript_{identity}.json")


def lock_transcript_output(
    stack: Any, metadata: Dict[str, Any], output: Path, runtime_root: Path
) -> None:
    """Acquires lock for transcript output directory."""
    if not is_document_request(metadata):
        key = hashlib.sha256(str(output.resolve()).encode("utf-8")).hexdigest()
        locks_dir = runtime_root / "locks"
        locks_dir.mkdir(parents=True, exist_ok=True)
        stack.enter_context(MeetingIngestionLock("output_" + key, lock_dir=locks_dir))


def _build_binding_for_call(kwargs: Dict[str, Any]) -> Dict[str, Any]:
    meta = kwargs["metadata"]
    return build_transcript_resume_binding(
        source_text=kwargs["source_text"],
        metadata=meta,
        provider=kwargs["provider"],
        provider_type=kwargs["provider_type"],
        model_name=kwargs.get("model_name"),
        reasoning_effort=kwargs.get("reasoning_effort"),
        output_1_text=meta.get("output_1_text") or (kwargs.get("pa_context", {}).get("forensic_transcript")),
        output_dir=kwargs.get("out_p"),
        judge_model=kwargs.get("judge_model"),
        judge_effort=kwargs.get("judge_effort"),
        fast_judge=bool(meta.get("fast_judge", False)),
        judge_tier=meta.get("judge_tier"),
        context=kwargs.get("pa_context"),
        extraction_revision=meta.get("extraction_model_revision"),
        judge_revision=meta.get("judge_model_revision"),
        execution_options={
            "max_judge_retries": kwargs.get("max_judge_retries"),
            "timeout_seconds": kwargs.get("timeout_seconds"),
        },
    )


def _validate_cached_transcript(
    payload: Dict[str, Any],
    source_text: str,
    metadata: Dict[str, Any],
    modality: CanonicalModality,
) -> Tuple[int, SynthesisResult, QAEvaluationResult]:
    try:
        from dual_pipeline.transcript_qa_core.evaluator import evaluate_all_qa_gates  # noqa: lazy-decouple
        synthesis = SynthesisResult(**payload["synthesis"])
        evaluation = QAEvaluationResult(**payload["evaluation"])
        attempt = int(payload["attempt"])

        verify_attempt_binding(synthesis, evaluation, source_text, modality)

        if not (
            evaluation.authoritative_write_authorized
            and evaluation.final_decision.get("status") in ("PASS", "APPROVED", "PASS_ADVISORY", "PASS_AUTHORIZED_EXCEPTION")
        ):
            raise ValueError("Cached candidate is not a validated success")
        if payload.get("exit_gates_verified") is False or (
            metadata.get("defer_checkpoint_until_exit_qa") and payload.get("exit_gates_verified") is not True
        ):
            raise ValueError("Cached transcript synthesis has not passed Stage X1-X3 exit gates")

        fresh_qa = evaluate_all_qa_gates(
            f1_text=synthesis.output_1,
            f2_text=synthesis.output_2,
            f3_text=synthesis.output_3,
            source_text=source_text,
            metadata=metadata,
            auto_heal=False,
        )
        overall_passed = bool(
            fresh_qa.get("overall_pass")
            if "overall_pass" in fresh_qa
            else (
                fresh_qa.get("all_gates_passed")
                if "all_gates_passed" in fresh_qa
                else (fresh_qa.get("overall_verdict") == "PASS")
            )
        )
        has_failure = any(
            isinstance(v, dict) and v.get("passed") is False
            for k, v in fresh_qa.items()
            if "gate" in k.lower() or k.startswith("output_")
        )
        if not overall_passed or has_failure:
            raise ValueError("Current deterministic QA no longer accepts the exact cached candidate")

        return attempt, synthesis, evaluation
    except Exception as exc:
        raise ValueError(f"CACHED_TRANSCRIPT_SYNTHESIS_INVALID: {exc}") from exc


def run_synthesis_with_transcript_resume(
    *, synthesize: Any, **kwargs: Any
) -> Tuple[int, SynthesisResult, QAEvaluationResult]:
    """Reuses validated Stage 02/03 synthesis when exact source/policy bindings match."""
    from dual_pipeline.canonical_pipeline.attachments import is_deliberate_reset

    meta = kwargs["metadata"]
    path = meta.get("_transcript_checkpoint_path")
    if not path or is_document_request(meta):
        return synthesize(**kwargs)

    checkpoint = PipelineCheckpointManager(path, require_binding=True)
    binding = _build_binding_for_call(kwargs)
    audit = checkpoint.bind(binding, force=bool(is_deliberate_reset(meta)))
    _ACTIVE_TRANSCRIPT_CHECKPOINT.set(checkpoint)

    payload = checkpoint.get_stage("VALIDATED_SYNTHESIS")
    info = {
        "reused_synthesis": False,
        "reason": audit.get("reason"),
        "binding_sha256": binding["binding_sha256"],
        "checkpoint": str(path),
    }
    if not binding["reusable"]:
        info.update(reason="UNBOUND_REVISION_OR_REGENERATE", blockers=binding["reuse_blockers"])
    meta["transcript_resume"] = info

    if payload is not None and binding["reusable"]:
        try:
            result = _validate_cached_transcript(payload, kwargs["source_text"], meta, kwargs["modality"])
            info.update(reused_synthesis=True, origin_run_id=payload.get("run_id"))
            return result
        except Exception as cache_err:
            logger.warning("Cached transcript synthesis rejected: %s", cache_err)

    result = synthesize(**kwargs)
    attempt, synthesis, evaluation = result

    final_binding = _build_binding_for_call(kwargs)
    if final_binding.get("binding_sha256") != binding.get("binding_sha256"):
        checkpoint.save_stage(
            "SYNTHESIS_NOT_REUSABLE",
            {"reason": "BINDING_CHANGED_DURING_RUN", "run_id": meta.get("run_id")},
        )
        return result

    if evaluation.authoritative_write_authorized and evaluation.final_decision.get("status") in ("PASS", "APPROVED", "PASS_ADVISORY", "PASS_AUTHORIZED_EXCEPTION"):
        verify_attempt_binding(synthesis, evaluation, kwargs["source_text"], kwargs["modality"])
        stage_k = "PENDING_EXIT_QA_SYNTHESIS" if meta.get("defer_checkpoint_until_exit_qa") else "VALIDATED_SYNTHESIS"
        checkpoint.save_stage(stage_k, {"attempt": attempt, "synthesis": asdict(synthesis), "evaluation": asdict(evaluation), "run_id": meta.get("run_id"), "binding_sha256": binding["binding_sha256"]})

    return result


def refresh_validated_transcript_checkpoint(
    synthesis: SynthesisResult,
    evaluation: QAEvaluationResult,
    attempt: int,
    metadata: Dict[str, Any],
) -> None:
    """Refreshes VALIDATED_SYNTHESIS checkpoint after Stage X1-X3 exit gates and auto-heals succeed."""
    checkpoint = _ACTIVE_TRANSCRIPT_CHECKPOINT.get()
    if checkpoint is None:
        return
    if not checkpoint.has_stage("VALIDATED_SYNTHESIS") and not checkpoint.has_stage("PENDING_EXIT_QA_SYNTHESIS"):
        return
    existing = checkpoint.get_stage("VALIDATED_SYNTHESIS") or checkpoint.get_stage("PENDING_EXIT_QA_SYNTHESIS") or {}
    checkpoint.save_stage(
        "VALIDATED_SYNTHESIS",
        {
            "attempt": attempt,
            "synthesis": asdict(synthesis),
            "evaluation": asdict(evaluation),
            "run_id": metadata.get("run_id"),
            "binding_sha256": existing.get("binding_sha256"),
            "exit_gates_verified": True,
        },
    )


def record_transcript_failure(metadata: Dict[str, Any], stage: str, error: BaseException) -> None:
    """Records downstream failure reason against active transcript checkpoint."""
    checkpoint = _ACTIVE_TRANSCRIPT_CHECKPOINT.get()
    if checkpoint is not None:
        if stage == "X_EXIT_GATES" and (
            checkpoint.has_stage("VALIDATED_SYNTHESIS") or checkpoint.has_stage("PENDING_EXIT_QA_SYNTHESIS")
        ):
            checkpoint.invalidate(
                ("VALIDATED_SYNTHESIS", "PENDING_EXIT_QA_SYNTHESIS"),
                reason="X_EXIT_GATES_FAILED_SYNTHESIS_INVALIDATED",
            )
        checkpoint.save_stage(
            "LAST_FAILURE",
            {
                "reason": "DOWNSTREAM_FAILURE_REUSE_VALIDATED_SYNTHESIS"
                if checkpoint.has_stage("VALIDATED_SYNTHESIS")
                else ("X_EXIT_GATES_FAILED_SYNTHESIS_INVALIDATED" if stage == "X_EXIT_GATES" else "NO_VALIDATED_SYNTHESIS"),
                "stage": stage,
                "error_type": type(error).__name__,
                "run_id": metadata.get("run_id"),
            },
        )
    reset_transcript_checkpoint()
