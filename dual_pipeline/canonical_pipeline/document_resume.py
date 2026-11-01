"""Reuse exactly bound document synthesis while rerunning all current release checks."""
from __future__ import annotations

import contextlib
import hashlib
from dataclasses import asdict
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from dual_pipeline.canonical_pipeline.attempt_binding import verify_attempt_binding
from dual_pipeline.canonical_pipeline.concurrency import MeetingIngestionLock
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.strategies.qa import QAEvaluationResult
from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
from dual_pipeline.checkpoint import PipelineCheckpointManager


_ACTIVE_DOCUMENT_CHECKPOINT: ContextVar[PipelineCheckpointManager | None] = ContextVar(
    "active_document_checkpoint", default=None,
)


def reset_document_checkpoint() -> None:
    _ACTIVE_DOCUMENT_CHECKPOINT.set(None)


class FreshDocumentStages:
    """Document ingress, identity and context execute fresh by default with optional in-memory stage retention."""
    def __init__(self, enable_stage_cache: bool = False) -> None:
        self.enable_stage_cache = enable_stage_cache
        self._stages: dict[str, dict] = {}

    def has_stage(self, stage: str) -> bool:
        if not self.enable_stage_cache:
            return False
        return stage in self._stages

    def get_stage(self, stage: str) -> dict | None:
        if not self.enable_stage_cache:
            return None
        return self._stages.get(stage)

    def save_stage(self, stage: str, payload: dict) -> None:
        if self.enable_stage_cache and isinstance(payload, dict):
            self._stages[stage] = dict(payload)

    def clear(self) -> None:
        self._stages.clear()


def is_document_request(metadata: dict[str, Any]) -> bool:
    return bool(metadata.get("is_document") or metadata.get("source_type") == "document")


def open_stage_checkpoint(path: Path, metadata: dict[str, Any]):
    if is_document_request(metadata):
        if metadata.get("persist_document_stage_checkpoint"):
            return PipelineCheckpointManager(path)
        return FreshDocumentStages(enable_stage_cache=bool(metadata.get("cache_document_stages", True)))
    return PipelineCheckpointManager(path)


def lock_document_source(stack: Any, metadata: dict[str, Any], source_text: str,
                         runtime_root: Path) -> None:
    if not is_document_request(metadata):
        return
    raw_proj = str(metadata.get("project") or "").strip()
    norm_proj = raw_proj if (raw_proj and raw_proj.lower() not in {"none", "auto"}) else "internal_consulting"
    source = metadata.get("raw_source_sha256") or hashlib.sha256(source_text.encode()).hexdigest()
    identity = hashlib.sha256((norm_proj + ":" + source).encode()).hexdigest()
    stack.enter_context(MeetingIngestionLock("document_" + identity, lock_dir=runtime_root / "locks"))
    metadata["_document_checkpoint_path"] = str(runtime_root / "checkpoints" / f"document_{identity}.json")


def lock_document_output(stack: Any, metadata: dict[str, Any], output: Path, runtime_root: Path) -> None:
    if is_document_request(metadata):
        key = hashlib.sha256(str(output.resolve()).encode()).hexdigest()
        stack.enter_context(MeetingIngestionLock("output_" + key, lock_dir=runtime_root / "locks"))


def _binding(kwargs: dict[str, Any]) -> dict[str, Any]:
    from dual_pipeline.canonical_pipeline.document_resume_binding import build_document_resume_binding
    meta = kwargs["metadata"]
    source_path = meta.get("_document_source_path")
    return build_document_resume_binding(
        source_text=kwargs["source_text"], metadata=meta, provider=kwargs["provider"],
        provider_type=kwargs["provider_type"], model_name=kwargs["model_name"],
        reasoning_effort=kwargs["reasoning_effort"], source_path=source_path,
        judge_model=kwargs.get("judge_model"), judge_effort=kwargs.get("judge_effort"),
        fast_judge=bool(meta.get("fast_judge", False)), judge_tier=meta.get("judge_tier"),
        context=kwargs["pa_context"], extraction_revision=meta.get("extraction_model_revision"),
        judge_revision=meta.get("judge_model_revision"),
        execution_options={"max_judge_retries": kwargs["max_judge_retries"],
                           "timeout_seconds": kwargs.get("timeout_seconds")},
    )


_REMEDIATION_MUTABLE_KEYS = frozenset({
    "metadata_sha256",
    "execution_options",
    "learned_aliases_sha256",
    "context_sha256",
    "hitl_ledger_sha256",
})


def _is_compatible_provider_component(old_value: Any, new_value: Any) -> bool:
    if not (isinstance(old_value, dict) and isinstance(new_value, dict)):
        return old_value == new_value
    old_prov = {k: v for k, v in old_value.items() if k != "declared_revision"}
    new_prov = {k: v for k, v in new_value.items() if k != "declared_revision"}
    if old_prov != new_prov:
        return False
    return old_value.get("declared_revision") in (None, new_value.get("declared_revision"))


def _is_compatible_component_value(key: str, old_value: Any, new_value: Any) -> bool:
    if key in _REMEDIATION_MUTABLE_KEYS:
        return True
    if key == "prompts" and isinstance(old_value, dict) and isinstance(new_value, dict):
        old_p = {k: v for k, v in old_value.items() if k != "document_user_content"}
        new_p = {k: v for k, v in new_value.items() if k != "document_user_content"}
        return old_p == new_p
    if key in ("provider", "extraction"):
        return _is_compatible_provider_component(old_value, new_value)
    return old_value == new_value


def _only_bounded_remediation_prompt_changed(
    before: dict[str, Any], after: dict[str, Any]
) -> bool:
    """Allow an in-run repair prompt change without relaxing source/config binding."""
    if before.get("schema_version") != after.get("schema_version"):
        return False
    before_blockers = set(before.get("reuse_blockers") or [])
    after_blockers = set(after.get("reuse_blockers") or [])
    allowed_resolved = {"EXTRACTION_MODEL_REVISION_UNBOUND", "JUDGE_MODEL_REVISION_UNBOUND"}
    if not (after_blockers <= before_blockers and (before_blockers - after_blockers) <= allowed_resolved):
        return False
    old_components = before.get("components", {})
    new_components = after.get("components", {})
    if old_components.keys() != new_components.keys():
        return False
    return all(
        _is_compatible_component_value(k, old_val, new_components[k])
        for k, old_val in old_components.items()
    )


def _validate_cached(payload: dict[str, Any], source_text: str, metadata: dict[str, Any]):
    from dual_pipeline.document_pipeline_core.evaluator import evaluate_document_qa_gates
    from dual_pipeline.document_pipeline_core.quality import qualify_document_judge
    try:
        synthesis = SynthesisResult(**payload["synthesis"])
        evaluation = QAEvaluationResult(**payload["evaluation"])
        attempt = int(payload["attempt"])
        verify_attempt_binding(synthesis, evaluation, source_text, CanonicalModality.DOCUMENT)
        if not (
            evaluation.authoritative_write_authorized
            and evaluation.final_decision.get("status") in ("PASS", "APPROVED", "PASS_ADVISORY", "PASS_AUTHORIZED_EXCEPTION")
        ):
            raise ValueError("Cached candidate is not a validated success")
        if not qualify_document_judge(evaluation.judge_report["output_3_evaluation"])[1]:
            raise ValueError("Cached candidate does not satisfy current rubric")
        fresh_qa = evaluate_document_qa_gates(synthesis.output_3, source_text, metadata)
        if not fresh_qa.get("passed") or fresh_qa["healed_artifacts"]["output_3"] != synthesis.output_3:
            raise ValueError("Current deterministic QA no longer accepts the exact cached candidate")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("CACHED_SYNTHESIS_INVALID: explicit regeneration or repair is required") from exc
    return attempt, synthesis, evaluation


def _is_disk_fallback_blocked(audit: dict[str, Any]) -> bool:
    if audit.get("reason") in {"BINDING_CHANGED", "FORCE_INVALIDATION", "STAGE_INVALIDATED"}:
        return True
    audit_events = audit.get("audit") or []
    if audit_events and isinstance(audit_events[-1], dict):
        if audit_events[-1].get("reason") == "X_EXIT_GATES_FAILED_SYNTHESIS_INVALIDATED":
            return True
    checkpoint = _ACTIVE_DOCUMENT_CHECKPOINT.get()
    if checkpoint is not None:
        with contextlib.suppress(Exception):
            if checkpoint.has_stage("SYNTHESIS_NOT_REUSABLE"):
                return True
            last_fail = checkpoint.get_stage("LAST_FAILURE") or {}
            if (
                last_fail.get("reason") == "X_EXIT_GATES_FAILED_SYNTHESIS_INVALIDATED"
                or last_fail.get("stage") == "X_EXIT_GATES"
            ):
                return True
    return False


def _try_resume_disk_attempt(
    out_p: Any, binding: dict[str, Any], kwargs: dict[str, Any], meta: dict[str, Any], audit: dict[str, Any], info: dict[str, Any]
) -> Any:
    if not (out_p and binding.get("reusable", True) and not _is_disk_fallback_blocked(audit)):
        return None
    from dual_pipeline.canonical_pipeline.attachments import try_resume_passing_attempt
    from dual_pipeline.canonical_pipeline.execution_identity import execution_reuse_blockers
    disk_resumed = try_resume_passing_attempt(Path(out_p), meta, CanonicalModality.DOCUMENT)
    if disk_resumed is None:
        return None
    validated = _validate_cached(
        {
            "attempt": disk_resumed[0],
            "synthesis": asdict(disk_resumed[1]),
            "evaluation": asdict(disk_resumed[2]),
        },
        kwargs["source_text"],
        meta,
    )
    if execution_reuse_blockers(binding, validated[2]):
        raise ValueError("CACHED_SYNTHESIS_INVALID: observed execution identity disagrees with binding")
    info.update(reused_synthesis=True, origin_run_id="disk_attempt")
    return validated


def _try_resume_cached_payload(
    payload: Any, binding: dict[str, Any], kwargs: dict[str, Any],
    meta: dict[str, Any], audit: dict[str, Any], info: dict[str, Any]
) -> Any:
    from dual_pipeline.canonical_pipeline.execution_identity import execution_reuse_blockers
    if payload is not None and binding["reusable"]:
        result = _validate_cached(payload, kwargs["source_text"], meta)
        if execution_reuse_blockers(binding, result[2]):
            raise ValueError("CACHED_SYNTHESIS_INVALID: observed execution identity disagrees with binding")
        info.update(reused_synthesis=True, origin_run_id=payload["run_id"])
        return result

    disk_res = _try_resume_disk_attempt(kwargs.get("out_p"), binding, kwargs, meta, audit, info)
    if disk_res is not None:
        return disk_res

    is_req = bool(meta.get("resume") or meta.get("require_resume"))
    if is_req and not binding["reusable"]:
        raise ValueError(f"RESUME_FAILED_CLOSED: unresolved revision binding ({', '.join(binding['reuse_blockers'])})")
    if is_req and payload is None:
        raise ValueError(f"RESUME_FAILED_CLOSED: no validated synthesis stage available (audit_reason={audit['reason']})")
    return None


def _rebind_checkpoint_safely(checkpoint: Any, binding_payload: Optional[dict[str, Any]] = None) -> None:
    with contextlib.suppress(Exception):
        if binding_payload is not None:
            checkpoint.bind(binding_payload, force=True)
        elif getattr(checkpoint, "_binding", None) is not None:
            checkpoint.bind(checkpoint._binding, force=True)
        elif isinstance(getattr(checkpoint, "_data", None), dict) and checkpoint._data.get("binding"):
            checkpoint.bind(checkpoint._data["binding"], force=False)


def _handle_binding_change(
    checkpoint: Any, binding: dict[str, Any], final_binding: dict[str, Any],
    kwargs: dict[str, Any], meta: dict[str, Any], initial_remediation_directive: Any, info: dict[str, Any]
) -> None:
    if not _only_bounded_remediation_prompt_changed(binding, final_binding):
        raise ValueError("SYNTHESIS_BINDING_CHANGED_DURING_RUN")
    info.update(reason="IN_RUN_REMEDIATION_NOT_REUSABLE")
    _rebind_checkpoint_safely(checkpoint, binding)
    checkpoint.save_stage("SYNTHESIS_NOT_REUSABLE", {
        "reason": "IN_RUN_REMEDIATION_NOT_REUSABLE",
        "run_id": meta.get("run_id"),
    })


def _persist_document_synthesis_stage(
    checkpoint: PipelineCheckpointManager,
    attempt: int,
    synthesis: SynthesisResult,
    evaluation: QAEvaluationResult,
    meta: dict[str, Any],
    effective_binding: dict[str, Any],
) -> None:
    stage_key = "PENDING_EXIT_QA_SYNTHESIS" if meta.get("defer_checkpoint_until_exit_qa") else "VALIDATED_SYNTHESIS"
    with contextlib.suppress(Exception):
        _rebind_checkpoint_safely(checkpoint, effective_binding)
        if checkpoint.has_stage("LAST_FAILURE") or checkpoint.has_stage("SYNTHESIS_NOT_REUSABLE"):
            checkpoint.invalidate(("LAST_FAILURE", "SYNTHESIS_NOT_REUSABLE"), reason="NEW_SYNTHESIS_VALIDATED")
        checkpoint.save_stage(stage_key, {
            "attempt": attempt, "synthesis": asdict(synthesis), "evaluation": asdict(evaluation),
            "run_id": meta.get("run_id"), "binding_sha256": effective_binding["binding_sha256"],
        })


def _restore_initial_remediation_directive(meta: dict[str, Any], initial_remediation_directive: Any) -> None:
    for rem_key in ("remediation_directive_forensic", "remediation_directive_recap", "remediation_directive_graph"):
        meta.pop(rem_key, None)
    if initial_remediation_directive is None:
        meta.pop("remediation_directive", None)
    else:
        meta["remediation_directive"] = initial_remediation_directive


def run_synthesis_with_resume(*, synthesize: Any, **kwargs: Any):
    """Only validated source-derived L2 work is reusable; publication never is."""
    meta = kwargs["metadata"]
    if kwargs["modality"] != CanonicalModality.DOCUMENT:
        from dual_pipeline.canonical_pipeline.transcript_resume import run_synthesis_with_transcript_resume
        return run_synthesis_with_transcript_resume(synthesize=synthesize, **kwargs)
    path = meta.get("_document_checkpoint_path")
    if not path:
        return synthesize(**kwargs)
    checkpoint, binding = PipelineCheckpointManager(path, require_binding=True), _binding(kwargs)
    initial_remediation_directive = meta.get("remediation_directive")
    from dual_pipeline.canonical_pipeline.attachments import is_deliberate_reset
    audit = checkpoint.bind(binding, force=bool(is_deliberate_reset(meta)))
    _ACTIVE_DOCUMENT_CHECKPOINT.set(checkpoint)
    payload = checkpoint.get_stage("VALIDATED_SYNTHESIS")
    info = {"reused_synthesis": False, "reason": audit["reason"],
            "binding_sha256": binding["binding_sha256"], "checkpoint": str(path)}
    if not binding["reusable"]:
        info.update(reason="MODEL_REVISION_UNBOUND", blockers=binding["reuse_blockers"])
    meta["document_resume"] = info
    cached = _try_resume_cached_payload(payload, binding, kwargs, meta, audit, info)
    if cached is not None:
        return cached
    result = synthesize(**kwargs)
    attempt, synthesis, evaluation = result
    final_binding = _binding(kwargs)
    _restore_initial_remediation_directive(meta, initial_remediation_directive)
    effective_binding = binding
    if final_binding != binding:
        if not _only_bounded_remediation_prompt_changed(binding, final_binding):
            raise ValueError("SYNTHESIS_BINDING_CHANGED_DURING_RUN")
        is_passed = (
            getattr(evaluation, "authoritative_write_authorized", False)
            and isinstance(getattr(evaluation, "final_decision", None), dict)
            and evaluation.final_decision.get("status") in ("PASS", "APPROVED", "PASS_ADVISORY", "PASS_AUTHORIZED_EXCEPTION")
        )
        if not is_passed:
            _handle_binding_change(checkpoint, binding, final_binding, kwargs, meta, initial_remediation_directive, info)
            return result
    _rebind_checkpoint_safely(checkpoint, effective_binding)
    from dual_pipeline.canonical_pipeline.execution_identity import execution_reuse_blockers
    observed_blockers = execution_reuse_blockers(effective_binding, evaluation)
    if observed_blockers:
        info.update(reason="OBSERVED_EXECUTION_MISMATCH", blockers=observed_blockers)
        checkpoint.save_stage("SYNTHESIS_NOT_REUSABLE", {
            "reason": "OBSERVED_EXECUTION_MISMATCH", "blockers": observed_blockers, "run_id": meta.get("run_id"),
        })
    elif evaluation.authoritative_write_authorized:
        verify_attempt_binding(synthesis, evaluation, kwargs["source_text"], CanonicalModality.DOCUMENT)
        _persist_document_synthesis_stage(checkpoint, attempt, synthesis, evaluation, meta, effective_binding)
    return result


def _rebind_healed_evaluation(
    synthesis: SynthesisResult,
    evaluation: QAEvaluationResult,
    metadata: dict[str, Any],
) -> None:
    from dual_pipeline.release_policy import (
        build_artifact_manifest,
        compute_release_evaluation_digest,
    )
    f3_str = synthesis.output_3 or ""
    if isinstance(evaluation.qa_report, dict):
        healed = evaluation.qa_report.setdefault("healed_artifacts", {})
        if isinstance(healed, dict):
            healed["output_3"] = f3_str
    if isinstance(evaluation.judge_report, dict) and isinstance(evaluation.final_decision, dict):
        eval_digest = compute_release_evaluation_digest(evaluation.judge_report)
        evaluation.judge_report["evaluation_receipt_digest"] = eval_digest
        orig_manifest = evaluation.final_decision.get("artifact_manifest") or {}
        source_text = metadata.get("source_text") or ""
        if source_text:
            manifest = build_artifact_manifest(
                f1_text=source_text,
                f2_text=synthesis.output_2 or "",
                f3_text=f3_str,
                model=evaluation.judge_report.get("evaluator_model") or orig_manifest.get("evaluator_model") or "unknown",
                reasoning_effort=evaluation.judge_report.get("reasoning_effort") or orig_manifest.get("reasoning_effort"),
                judge_eval_digest=eval_digest,
            )
            evaluation.final_decision["artifact_manifest"] = manifest
        elif isinstance(orig_manifest, dict):
            orig_manifest["f3_sha256"] = hashlib.sha256(f3_str.encode("utf-8")).hexdigest()
            orig_manifest["judge_evaluation_digest"] = eval_digest
        evaluation.final_decision["judge_evaluation_digest"] = eval_digest


def refresh_validated_document_checkpoint(
    synthesis: SynthesisResult,
    evaluation: QAEvaluationResult,
    attempt: int,
    metadata: dict[str, Any],
) -> None:
    """Refreshes VALIDATED_SYNTHESIS document checkpoint after Stage X1-X3 exit gates and auto-heals succeed."""
    checkpoint = _ACTIVE_DOCUMENT_CHECKPOINT.get()
    if checkpoint is None:
        return
    if not checkpoint.has_stage("VALIDATED_SYNTHESIS") and not checkpoint.has_stage("PENDING_EXIT_QA_SYNTHESIS"):
        return
    _rebind_checkpoint_safely(checkpoint)
    _rebind_healed_evaluation(synthesis, evaluation, metadata)
    existing = checkpoint.get_stage("VALIDATED_SYNTHESIS") or checkpoint.get_stage("PENDING_EXIT_QA_SYNTHESIS") or {}
    if checkpoint.has_stage("PENDING_EXIT_QA_SYNTHESIS"):
        checkpoint.invalidate(("PENDING_EXIT_QA_SYNTHESIS",), reason="PROMOTED_TO_VALIDATED_SYNTHESIS")
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


def record_document_failure(metadata: dict[str, Any], stage: str, error: BaseException) -> None:
    checkpoint = _ACTIVE_DOCUMENT_CHECKPOINT.get()
    if checkpoint is not None:
        _rebind_checkpoint_safely(checkpoint)
        if stage == "X_EXIT_GATES" and (
            checkpoint.has_stage("VALIDATED_SYNTHESIS") or checkpoint.has_stage("PENDING_EXIT_QA_SYNTHESIS")
        ):
            checkpoint.invalidate(
                ("VALIDATED_SYNTHESIS", "PENDING_EXIT_QA_SYNTHESIS"),
                reason="X_EXIT_GATES_FAILED_SYNTHESIS_INVALIDATED",
            )
        checkpoint.save_stage("LAST_FAILURE", {
            "reason": (
                "DOWNSTREAM_FAILURE_REUSE_VALIDATED_SYNTHESIS"
                if checkpoint.has_stage("VALIDATED_SYNTHESIS")
                else ("X_EXIT_GATES_FAILED_SYNTHESIS_INVALIDATED" if stage == "X_EXIT_GATES" else "NO_VALIDATED_SYNTHESIS")
            ),
            "stage": stage, "error_type": type(error).__name__, "run_id": metadata.get("run_id"),
        })
    reset_document_checkpoint()


def record_pipeline_failure(metadata: dict[str, Any], stage: str, error: BaseException) -> None:
    """Dispatches failure recording to active document or transcript checkpoint."""
    if is_document_request(metadata):
        record_document_failure(metadata, stage, error)
    else:
        from dual_pipeline.canonical_pipeline.transcript_resume import record_transcript_failure
        record_transcript_failure(metadata, stage, error)
