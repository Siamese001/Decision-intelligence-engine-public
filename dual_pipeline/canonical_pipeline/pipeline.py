"""
dual_pipeline/canonical_pipeline/pipeline.py
CanonicalAgenticPipeline — The single, enforced, universal ingestion pipeline.
Traverses the immutable 7-stage agentic lifecycle across all four modalities:
U0 (Ingress) -> L1 (Identity) -> L0 (Routing) -> C0-PA (Context)
-> L2 (Synthesis) -> X1-X3 (Exit QA) -> EVALS (Release & Publication).
"""
from __future__ import annotations
import hashlib
import json
import logging
import os
import time
import uuid
from contextlib import ExitStack
from pathlib import Path
from dual_pipeline.canonical_pipeline.context_binding import BoundedContext
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from dual_pipeline.canonical_pipeline.batch_queue import _handle_batch_queue
from dual_pipeline.canonical_pipeline.document_resume import (
    is_document_request, lock_document_output, lock_document_source,
    open_stage_checkpoint, record_document_failure, refresh_validated_document_checkpoint,
    reset_document_checkpoint, run_synthesis_with_resume,
)
from dual_pipeline.canonical_pipeline.transcript_resume import (
    lock_transcript_output, lock_transcript_source, refresh_validated_transcript_checkpoint, reset_transcript_checkpoint,
)
from dual_pipeline.canonical_pipeline.concurrency import MeetingIngestionLock, derive_meeting_lock_key
from dual_pipeline.canonical_pipeline.content_loader import load_canonical_source_content
from dual_pipeline.canonical_pipeline.lifecycle import (
    build_and_validate_intake_package, check_directory_compliance, deploy_deliverables,
    resolve_routing, run_synthesis_loop, scaffold_metadata,
)
from dual_pipeline.canonical_pipeline.c0_pa_runner import execute_c0_pa, step_c0_pa_with_resume
from dual_pipeline.canonical_pipeline.pre_gate_runner import run_pre_gate_check
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality, detect_modality, get_deliverables_for_modality
from dual_pipeline.canonical_pipeline.publish_helper import (
    assemble_pipeline_result, check_idempotent_or_continue, check_mutation_reviewer_and_preflight,
    extract_proposed_entities, handle_pipeline_run_exception, is_eval_verdict_pass,
    prepare_run_metadata, publish_graph_if_requested, record_retrieval_completion,
    resolve_pipeline_project_alias, verify_exit_qa,
)
from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
from dual_pipeline.transcript_metadata_helper import sanitize_loaded_metadata, sanitize_metadata_for_persistence
from dual_pipeline.execution_evidence import durable_execution_scope
from dual_pipeline.checkpoint import PipelineCheckpointManager
from dual_pipeline.core import ROOT
from dual_pipeline.dlq import DeadLetterQueueManager
from dual_pipeline.model_registry import ModelTier, resolve_tier_model
from dual_pipeline.transcript_pipeline_core.caching import compute_source_file_hash
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
from dual_pipeline.transcript_stages_core import (
    C05TitleCompletenessGate,
    C06GraphDBRoleVerificationGate,
)
from dual_pipeline.transcript_stages_core.hitl_guard import assert_no_u0_hitl_bypass
from dual_pipeline.unified_agentic_pipeline import (
    PipelineStage,
    UnifiedAgenticPipeline,
)
from dual_pipeline.upstream_audio_qa import UpstreamAudioQAGatekeeper
logger = logging.getLogger("CanonicalAgenticPipeline")


def _notify(callback: Optional[Callable[[int, int, str], None]], step: int, desc: str) -> None:
    """Emit progress notification if callback provided."""
    if callback:
        callback(step, 7, desc)


def _stage_approval_receipt(staging_dir: Path, out_p: Path, meta: Dict[str, Any]) -> None:
    import shutil
    is_replay = bool(meta.get("force_rerun") or meta.get("regenerate") or meta.get("is_replay") or meta.get("auto_regenerate_track1"))
    if meta.get("approval_receipt"):
        (staging_dir / "mutation_approval_receipt.json").write_text(json.dumps(meta["approval_receipt"], indent=2, default=str), encoding="utf-8")
    elif (out_p / "mutation_approval_receipt.json").is_file() and not is_replay:
        try:
            prior_rec = json.loads((out_p / "mutation_approval_receipt.json").read_text(encoding="utf-8"))
        except Exception:
            prior_rec = {}
        if not isinstance(prior_rec, dict) or prior_rec.get("status") != "ACTIVATED":
            shutil.copy2(out_p / "mutation_approval_receipt.json", staging_dir / "mutation_approval_receipt.json")


def _save_partial_qa_failure(out_p: Path, attempt: int, meta: Dict[str, Any], modality: CanonicalModality, synthesis_result: Any, exit_qa_err: Exception) -> None:
    has_partial = bool(synthesis_result and any(getattr(synthesis_result, a, None) for a in ("raw_transcript", "executive_recap", "canonical_graph", "canonical_signal_projection", "output_1", "output_2", "output_3")))
    if meta.get("preserve_partial", True) and has_partial:
        logger.warning("Exit QA verification failed: %s. Preserving partial deliverables.", exit_qa_err)
        partial_attempt_dir = out_p / "attempts" / f"attempt_{attempt}_partial_qa_failure"
        partial_attempt_dir.mkdir(parents=True, exist_ok=True)
        deploy_deliverables(partial_attempt_dir, meta, modality, synthesis_result)
        meta.update({"partial_deliverables_saved": True, "partial_deliverables_dir": str(partial_attempt_dir), "partial_deliverables_error": str(exit_qa_err), "status": "PARTIAL_QA_FAILURE"})
        (out_p / "metadata.json").write_text(json.dumps(sanitize_metadata_for_persistence(meta), indent=2, default=str), encoding="utf-8")


def _resolve_evals_mutation_intent(
    meta: Dict[str, Any], mutate_graph: bool, auto_mutate_on_pass: bool, eval_result: Any, reviewer: Optional[str] = None,
) -> Tuple[bool, bool]:
    is_pass = is_eval_verdict_pass(eval_result)
    rev = reviewer or meta.get("reviewer") or meta.get("approval_receipt") or meta.get("mutation_approval_receipt")
    has_reviewer = bool((rev and str(rev).strip()) or meta.get("approval_receipt") or meta.get("mutation_approval_receipt"))
    if mutate_graph and not is_pass:
        raise RuntimeError(
            f"Fail-Fast: Cannot mutate graph because QA/Judge evaluation failed (is_pass=False). "
            f"Final decision: {getattr(eval_result, 'final_decision', {})}. "
            f"Judge verdict: {getattr(eval_result, 'judge_report', {}).get('overall_verdict')}."
        )
    if meta.get("dry_run_graph"):
        raise ValueError("Explicit dry-run requests ('dry_run_graph') are prohibited for production ingestion.")
    should_mutate = bool(has_reviewer and (mutate_graph or (auto_mutate_on_pass and is_pass)))
    return is_pass, should_mutate


class CanonicalAgenticPipeline(UnifiedAgenticPipeline):
    """Universal canonical pipeline consolidating Audio, Transcripts, Notes, and Documents."""
    @property
    def pipeline_name(self) -> str:
        return "CanonicalAgenticPipeline"
    def __init__(
        self,
        provider_type: str = "llm",
        model_name: Optional[str] = None,
        reasoning_effort: Optional[str] = "high",
        modality: Optional[CanonicalModality] = None,
        *,
        u0_override: Optional[Any] = None,
        l1_override: Optional[Any] = None,
        l0_override: Optional[Any] = None,
        c0_native_override: Optional[Any] = None,
        c0_undiarized_override: Optional[Any] = None,
        c05_title_override: Optional[Any] = None,
        c06_role_override: Optional[Any] = None,
        pa_override: Optional[Any] = None,
        l2_override: Optional[Any] = None,
        x_gates_override: Optional[Any] = None,
        upstream_qa_override: Optional[Any] = None,
        l1_reviewer_override: Optional[Any] = None,
        stage_overrides: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.provider_type = provider_type
        def_model, def_effort = resolve_tier_model(ModelTier.FRONTIER_HIGH_REASONING)
        self.model_name = model_name or def_model
        eff = reasoning_effort or def_effort or "high"
        self.reasoning_effort = eff
        self.forced_modality = modality
        if provider_type == "offline":
            self.provider = OfflineTranscriptSynthesizer()
        elif provider_type == "mock":
            from unittest.mock import MagicMock
            self.provider = LLMTranscriptSynthesizer(model=self.model_name, reasoning_effort=eff, _client=MagicMock(), _creds=MagicMock())
        else:
            self.provider = LLMTranscriptSynthesizer(model=self.model_name, reasoning_effort=eff)
        overrides = stage_overrides or {}
        self.u0 = u0_override or overrides.get("u0") or U0IngestionBoundary()
        self.l1 = l1_override or overrides.get("l1") or L1ForensicCognition()
        self.l0 = l0_override or overrides.get("l0") or L0DeterministicRouter()
        self.c0_native = c0_native_override or overrides.get("c0_native") or C0ConformanceGate()
        self.c0_undiarized = c0_undiarized_override or overrides.get("c0_undiarized") or C0RosterContextGate()
        self.c05_title = c05_title_override or overrides.get("c05_title") or C05TitleCompletenessGate()
        self.c06_role = c06_role_override or overrides.get("c06_role") or C06GraphDBRoleVerificationGate()
        self.pa = pa_override or overrides.get("pa") or PAPacketAssembler()
        self.l2_forensic = overrides.get("l2_forensic") or L2ForensicNormalizerExecutor(self.provider)
        self.l2_recap = overrides.get("l2_recap") or L2ExecutiveRecapExecutor(self.provider)
        self.l2_graph = overrides.get("l2_graph") or L2GraphExtractorExecutor(self.provider)
        self.l2 = l2_override or overrides.get("l2") or L2SynthesisExecutor(self.provider)
        self.x_gates = x_gates_override or overrides.get("x_gates") or XExitGatesEvaluator()
        self.upstream_qa = upstream_qa_override or overrides.get("upstream_qa") or UpstreamAudioQAGatekeeper()
        if l1_reviewer_override or overrides.get("l1_reviewer"):
            self.l1_reviewer = l1_reviewer_override or overrides.get("l1_reviewer")
        else:
            from dual_pipeline.transcript_pipeline_core.post_l2_review import (
                TranscriptL1PostExecutionReviewer,
            )
            self.l1_reviewer = TranscriptL1PostExecutionReviewer()
    def _load_source_content(
        self, input_path: Union[str, Path, None], meta: Dict[str, Any],
        presentation_path: Optional[Union[str, Path]] = None,
    ) -> tuple[str, str, Optional[Path]]:
        return load_canonical_source_content(input_path=input_path, meta=meta, presentation_path=presentation_path, offline=(self.provider_type == "offline"))
    def _resolve_routing(self, meta: Dict[str, Any], output_dir: str, modality: Optional[CanonicalModality] = None) -> Path:
        return resolve_routing(meta, output_dir, modality)
    def _deploy_deliverables(self, out_p: Path, meta: Dict[str, Any], modality: CanonicalModality, synthesis_result: SynthesisResult) -> Dict[str, str]:
        return deploy_deliverables(out_p, meta, modality, synthesis_result)
    def _execute_u0(
        self, input_path: Union[str, Path, None], meta: Dict[str, Any],
        presentation_path: Optional[Union[str, Path]], attachments: Any,
        attachment_dir: Any, signal_envelope: Any, output_dir: str,
        mutate_graph: bool, tracker: Any,
    ) -> Tuple[str, str, Optional[Path], Optional[Dict[str, Any]]]:
        tracker.start_stage(PipelineStage.U0_INGRESS)
        if not meta.get("is_document"):
            from dual_pipeline.canonical_pipeline.u0_ingress import hydrate_live_container_artifacts
            hydrate_live_container_artifacts(meta, input_path=input_path)
        active_env = signal_envelope or meta.get("signal_envelope") or meta.get("forensic_signal_envelope")
        if active_env is not None:
            from dual_pipeline.feature_flags import get_feature_flag_manager
            flags = get_feature_flag_manager()
            if not flags.is_off("UPSTREAM_AUDIO_QA"):
                qa_rep = self.upstream_qa.evaluate(active_env, metadata=meta)
                meta["upstream_qa_report"] = qa_rep
                if qa_rep.verdict in ("REJECT", "NO_SPEECH") and flags.is_on("UPSTREAM_AUDIO_QA"):
                    from dual_pipeline.speaker_attribution_core.attribution_anomaly_gate import handle_gate_c_attribution_failure
                    qa_rep, active_env = handle_gate_c_attribution_failure(
                        gatekeeper=self.upstream_qa, envelope=active_env, qa_rep=qa_rep,
                        metadata=meta, input_path=input_path, output_dir=output_dir,
                    )
                    meta["upstream_qa_report"] = qa_rep
                    if qa_rep.verdict in ("REJECT", "NO_SPEECH"):
                        reject_codes = [f.code for f in qa_rep.findings if f.severity == "blocking"]
                        raise ValueError(f"Upstream Audio QA Gatekeeper REJECTED signal envelope with verdict {qa_rep.verdict}. Blocking codes: {reject_codes}")
        source_text, input_hash, src_file_path = self._load_source_content(input_path, meta, presentation_path=presentation_path)
        meta["input_hash"] = input_hash
        meta["source_hash"] = input_hash
        meta["source_text"] = source_text
        post_qa_rep = meta.get("upstream_qa_report")
        if active_env is None and post_qa_rep is not None and getattr(post_qa_rep, "verdict", None) in ("REJECT", "NO_SPEECH"):
            from dual_pipeline.feature_flags import get_feature_flag_manager
            if get_feature_flag_manager().is_on("UPSTREAM_AUDIO_QA") or bool(meta.get("enforce_upstream_audio_qa")):
                reject_codes = [f.code for f in getattr(post_qa_rep, "findings", []) if getattr(f, "severity", "") == "blocking"]
                raise ValueError(f"Upstream Audio QA Gatekeeper REJECTED standalone audio envelope with verdict {post_qa_rep.verdict}. Blocking codes: {reject_codes}")
        if meta.get("is_document"):
            from dual_pipeline.document_pipeline_core.retrieval_visibility import (
                RetrievalClearancePending, preflight_document_clearance,
            )
            try:
                meta["retrieval_preflight"] = preflight_document_clearance(
                    meta, required=bool(mutate_graph or meta.get("auto_mutate_on_pass")
                                        or meta.get("require_retrieval")),
                )
            except RetrievalClearancePending as exc:
                meta["retrieval_preflight"] = exc.report
                raise

        if not meta.get("meeting_name") or not meta.get("date"):
            detected_mod = self.forced_modality or detect_modality(input_path, source_text, meta)
            meta.update(scaffold_metadata(detected_mod, input_path, meta, source_text=source_text))

        out_p = self._resolve_routing(meta, output_dir)
        eff_output_dir = str(out_p)

        if attachments or attachment_dir or not meta.get("is_document"):
            from dual_pipeline.transcript_pipeline_core.preflight_coordinator import (
                ingest_and_align_attachments,
            )
            source_text = ingest_and_align_attachments(
                metadata=meta, transcript_text=source_text, output_dir=eff_output_dir,
                attachments=attachments, attachment_dir=attachment_dir, input_path=input_path,
            )

        import hashlib
        input_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        meta["input_hash"] = input_hash
        meta["source_hash"] = input_hash
        meta["source_text"] = source_text

        idempotent = check_idempotent_or_continue(tracker, eff_output_dir, meta, input_hash, mutate_graph)
        if idempotent is not None:
            return source_text, input_hash, src_file_path, idempotent

        tracker.complete_stage(
            PipelineStage.U0_INGRESS, status="PASS", details={"input_hash": input_hash, "char_count": len(source_text)}
        )
        return source_text, input_hash, src_file_path, None

    def _execute_l1_l0(
        self, input_path: Union[str, Path, None], source_text: str,
        meta: Dict[str, Any], output_dir: str, tracker: Any,
    ) -> Tuple[CanonicalModality, Path]:
        tracker.start_stage(PipelineStage.L1_IDENTITY)
        modality = self.forced_modality or detect_modality(input_path, source_text, meta)
        if meta.get("canonical_modality") or meta.get("notes_mode") or meta.get("is_notes"):
            modality = detect_modality(input_path, source_text, meta)
        meta["canonical_modality"] = modality.value
        meta["required_deliverables"] = get_deliverables_for_modality(modality)

        if modality in (CanonicalModality.AUDIO, CanonicalModality.MEETING_TRANSCRIPT):
            is_diarized = meta.get("diarization_status") in ("NATIVE", "DIARIZED_ACOUSTIC_RESOLVED") or (
                hasattr(self.u0, "_detect_diarization_status")
                and self.u0._detect_diarization_status(source_text, meta) in ("NATIVE", "DIARIZED_ACOUSTIC_RESOLVED")
            )
            if not is_diarized:
                triage_res = self.provider.synthesize_signal_triage(source_text)
                if "NO_SIGNAL" in triage_res.upper():
                    from dual_pipeline.exceptions import IdempotentNoOpIntakeError
                    raise IdempotentNoOpIntakeError("L1 Lightweight Signal Triage: pure chit-chat detected (NO_SIGNAL). Aborting execution.")

        if modality == CanonicalModality.DOCUMENT:
            meta.update(scaffold_metadata(modality, input_path, meta, source_text=source_text))
        elif modality == CanonicalModality.MEETING_NOTES or meta.get("notes_mode") or meta.get("is_notes"):
            if not meta.get("meeting_name") or not meta.get("date"):
                meta.update(scaffold_metadata(modality, input_path, meta, source_text=source_text))
            meta["notes_mode"] = True
            meta["is_notes"] = True
            meta["canonical_modality"] = CanonicalModality.MEETING_NOTES.value
            u0_ctx = self.u0.execute(source_text, meta)
            meta.update(u0_ctx.get("metadata", {}))
            if u0_ctx.get("clean_transcript"):
                meta["clean_transcript"] = meta["source_text"] = u0_ctx["clean_transcript"]
        else:
            if not meta.get("meeting_name") or not meta.get("date"):
                meta.update(scaffold_metadata(modality, input_path, meta, source_text=source_text))
            u0_ctx = self.u0.execute(source_text, meta)
            meta.update(u0_ctx.get("metadata", {}))
            if u0_ctx.get("clean_transcript"):
                meta["clean_transcript"] = meta["source_text"] = u0_ctx["clean_transcript"]

        tracker.complete_stage(
            PipelineStage.L1_IDENTITY, status="PASS", details={"modality": modality.value, "deliverables": meta["required_deliverables"]}
        )

        tracker.start_stage(PipelineStage.L0_ROUTING)
        out_p = resolve_routing(meta, output_dir, modality)
        meta["target_output_dir"] = str(out_p)
        tracker.complete_stage(PipelineStage.L0_ROUTING, status="PASS", details={"output_dir": str(out_p)})
        return modality, out_p

    def _execute_c0_pa(
        self, modality: CanonicalModality, source_text: str, input_hash: str,
        meta: Dict[str, Any], presentation_path: Optional[Union[str, Path]],
        out_p: Path, tracker: Any,
    ) -> Tuple[Dict[str, Any], str]:
        return execute_c0_pa(
            pipeline=self,
            modality=modality,
            source_text=source_text,
            input_hash=input_hash,
            meta=meta,
            presentation_path=presentation_path,
            out_p=out_p,
            tracker=tracker,
        )

    def _execute_evals_release(
        self, modality: CanonicalModality, meta: Dict[str, Any], out_p: Path,
        source_text: str, src_file_path: Optional[Path], synthesis_result: SynthesisResult,
        eval_result: Any, mutate_graph: bool, directive_id: Optional[str],
        reviewer: Optional[str], rationale: Optional[str], embedding_backend: str,
        tracker: Any, auto_mutate_on_pass: bool = False,
    ) -> Dict[str, Any]:
        tracker.start_stage(PipelineStage.EVALS_RELEASE)
        from config.request_deadline import check_deadline
        check_deadline()
        import shutil
        is_fast_mode = bool(meta.get("test_fast") or os.environ.get("NEE_FAST_TEST_MODE"))
        staging_token = hashlib.sha256(directive_id.encode("utf-8")).hexdigest()[:8] if directive_id else uuid.uuid4().hex[:8]
        staging_dir = out_p if is_fast_mode else (out_p.parent / f".staging_{out_p.name}_{staging_token}")
        staging_dir.mkdir(parents=True, exist_ok=True)
        meta["target_output_dir"] = str(out_p)
        meta["deliverable_dir"] = str(out_p)
        meta["deliverable_receipt_path"] = str(out_p / "pipeline_execution_receipt.json")
        tracker.metadata["deliverable_dir"] = str(out_p)
        tracker.metadata["deliverable_receipt_path"] = str(out_p / "pipeline_execution_receipt.json")
        try:
            artifacts_map = deploy_deliverables(staging_dir, meta, modality, synthesis_result)
            if modality == CanonicalModality.DOCUMENT and source_text:
                if "source_text" not in meta:
                    meta["source_text"] = source_text
                (staging_dir / "source_document.txt").write_text(source_text, encoding="utf-8")

            for fn, payload in (
                ("metadata.json", sanitize_metadata_for_persistence(meta)),
                ("qa_gates_report.json", eval_result.qa_report),
                ("llm_judge_evaluation.json", eval_result.judge_report),
                ("final_release_decision.json", eval_result.final_decision),
            ):
                (staging_dir / fn).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

            _stage_approval_receipt(staging_dir, out_p, meta)
            intake_pkg_path = build_and_validate_intake_package(staging_dir, artifacts_map, meta, source_text, src_file_path)

            from dual_pipeline.core import ROOT
            from dual_pipeline.graph_scout_core.pipeline_hook import execute_tier1_scout_hook

            proposed_entities = extract_proposed_entities(artifacts_map.get("03_graph", ""))
            scout_result = execute_tier1_scout_hook(
                proposed_entities=proposed_entities,
                output_dir=staging_dir,
                runtime_root=ROOT / ".local" / "dual_pipeline" / "runtime",
                dir_id=directive_id,
                written=artifacts_map,
                metadata=meta,
            )
            is_pass, should_mutate = _resolve_evals_mutation_intent(
                meta, mutate_graph, auto_mutate_on_pass, eval_result, reviewer=reviewer
            )

            check_mutation_reviewer_and_preflight(modality, should_mutate, meta, reviewer)

            try:
                check_deadline()
                mutation_result = publish_graph_if_requested(
                    mutate_graph=should_mutate,
                    intake_pkg_path=intake_pkg_path,
                    metadata=meta,
                    written=artifacts_map,
                    directive_id=directive_id,
                    reviewer=reviewer,
                    rationale=rationale,
                    embedding_backend=embedding_backend,
                )
            except Exception as e:
                logger.error("Authoritative GraphDB mutation failed: %s", e)
                raise RuntimeError(f"Fail-Fast: Authoritative GraphDB mutation failed: {e}") from e

            mut_res = mutation_result or {}
            mut_err = mut_res.get("error")
            mut_status = mut_res.get("status")
            from dual_pipeline.canonical_pipeline.publish_helper import (  # noqa: PLC0415
                resolve_release_completion_and_receipt,
            )
            receipt = resolve_release_completion_and_receipt(
                tracker=tracker,
                staging_dir=staging_dir,
                modality=modality,
                meta=meta,
                mut_res=mut_res,
                mut_status=mut_status,
                mut_err=mut_err,
                should_mutate=should_mutate,
                scout_result=scout_result,
                intake_pkg_path=intake_pkg_path,
                artifacts_map=artifacts_map,
            )

            if staging_dir != out_p:
                from dual_pipeline.transcript_pipeline_core.release_runner import (  # noqa: PLC0415
                    promote_staging_to_release,
                )
                promote_staging_to_release(staging_dir, out_p, purge_staging=False)
                check_directory_compliance(out_p, meta.get("project"))

                promoted_artifacts = {}
                for k, p in artifacts_map.items():
                    if isinstance(p, (str, Path)) and str(p).strip():
                        promoted_artifacts[k] = str(out_p / Path(p).name)
                    else:
                        promoted_artifacts[k] = p
                artifacts_map = promoted_artifacts
                intake_pkg_path = out_p / intake_pkg_path.name

            if meta.get("source_manifest"):
                manifest_path = out_p / "source_manifest.json"
                manifest_path.write_text(json.dumps(meta["source_manifest"], indent=2), encoding="utf-8")
                artifacts_map["source_manifest"] = str(manifest_path)
            receipt_path = out_p / "pipeline_execution_receipt.json"
            if not receipt_path.is_file():
                found_rc = list(out_p.glob("pipeline_execution_receipt*.json")) or list(out_p.glob("release_receipt_*.json"))
                if found_rc:
                    receipt_path = found_rc[0]
        except Exception:
            keep_staging = True
            raise
        finally:
            if (
                staging_dir != out_p
                and staging_dir.exists()
                and not locals().get("keep_staging", False)
                and (locals().get("mut_status") != "AWAITING_APPROVAL")
            ):
                shutil.rmtree(staging_dir, ignore_errors=True)

        return assemble_pipeline_result(
            modality=modality, meta=meta, out_p=out_p, artifacts_map=artifacts_map,
            eval_result=eval_result, intake_pkg_path=intake_pkg_path,
            receipt_path=receipt_path, mutation_result=mutation_result,
        )

    def _step_u0_with_resume(
        self,
        checkpoint: PipelineCheckpointManager,
        should_resume: bool,
        input_path: Union[str, Path, None],
        meta: Dict[str, Any],
        presentation_path: Optional[Union[str, Path]],
        attachments: Optional[Union[str, Path, List[Union[str, Path]]]],
        attachment_dir: Optional[Union[str, Path]],
        signal_envelope: Optional[Any],
        output_dir: str,
        mutate_graph: bool,
        tracker: Any,
        progress_callback: Optional[Callable[[int, int, str], None]],
    ) -> Tuple[str, str, Optional[Path], Optional[Dict[str, Any]]]:
        if should_resume and not meta.get("force_rerun") and checkpoint.has_stage("U0"):
            u0_data = checkpoint.get_stage("U0") or {}
            source_text = u0_data.get("source_text", "")
            input_hash = u0_data.get("input_hash", "")
            src_file_path = Path(u0_data["src_file_path"]) if u0_data.get("src_file_path") else None
            if not source_text or not input_hash:
                raise ValueError("Fail-Fast: Resumed U0 checkpoint is incomplete (missing source_text or input_hash).")
            from .u0_hint_reconciler import propagate_u0_context_to_metadata, verify_resumed_u0_input_hash
            propagate_u0_context_to_metadata(meta, u0_data)
            verify_resumed_u0_input_hash(input_path, meta, u0_data, input_hash, src_file_path)
            if is_document_request(meta):
                from dual_pipeline.document_pipeline_core.retrieval_visibility import preflight_document_clearance
                meta["retrieval_preflight"] = preflight_document_clearance(
                    meta, required=bool(mutate_graph or meta.get("auto_mutate_on_pass") or meta.get("require_retrieval")),
                )
            tracker.start_stage(PipelineStage.U0_INGRESS)
            idempotent = check_idempotent_or_continue(tracker, str(self._resolve_routing(meta, output_dir)), meta, input_hash, mutate_graph)
            if idempotent is not None:
                return source_text, input_hash, src_file_path, idempotent
            tracker.complete_stage(PipelineStage.U0_INGRESS, status="PASS", details={"resumed": True})
            return source_text, input_hash, src_file_path, None

        _notify(progress_callback, 1, "Stage U0: Ingress Boundary")
        source_text, input_hash, src_file_path, idempotent = self._execute_u0(
            input_path, meta, presentation_path, attachments, attachment_dir,
            signal_envelope, output_dir, mutate_graph, tracker,
        )
        if idempotent is not None:
            return source_text, input_hash, src_file_path, idempotent
        meta.setdefault("original_source_path", str(src_file_path) if src_file_path else None)
        checkpoint.save_stage("U0", {
            "source_text": source_text,
            "input_hash": input_hash,
            "src_file_path": str(src_file_path) if src_file_path else None,
            "original_source_path": str(src_file_path) if src_file_path else None,
            "source_file_hash": meta.get("source_file_hash") or meta.get("raw_source_sha256"),
            "metadata": meta,
        })
        return source_text, input_hash, src_file_path, None

    def _step_l1_l0_with_resume(
        self,
        checkpoint: PipelineCheckpointManager,
        should_resume: bool,
        input_path: Union[str, Path, None],
        source_text: str,
        meta: Dict[str, Any],
        output_dir: str,
        tracker: Any,
        progress_callback: Optional[Callable[[int, int, str], None]],
    ) -> Tuple[CanonicalModality, Path]:
        if should_resume and not meta.get("force_rerun") and checkpoint.has_stage("L1_L0"):
            l1_data = checkpoint.get_stage("L1_L0") or {}
            modality_str = l1_data.get("modality")
            out_p_str = l1_data.get("out_p")
            if not modality_str or not out_p_str:
                raise ValueError("Fail-Fast: Resumed L1_L0 checkpoint is incomplete (missing modality or out_p).")
            modality = CanonicalModality(modality_str)
            from dual_pipeline.canonical_pipeline.worktree_inheritance import reanchor_resumed_output_dir
            out_p = reanchor_resumed_output_dir(Path(out_p_str), meta)
            meta["canonical_modality"] = modality.value
            meta["required_deliverables"] = get_deliverables_for_modality(modality)
            from dual_pipeline.canonical_pipeline.c0_pa_runner import hydrate_resumed_l1_l0_metadata
            hydrate_resumed_l1_l0_metadata(self, l1_data, meta, input_path)
            if modality != CanonicalModality.DOCUMENT and not meta.get("diarization_status"):
                if hasattr(self.u0, "execute"):
                    u0_ctx = self.u0.execute(source_text, meta)
                    meta.update(u0_ctx.get("metadata", {}))
                    if u0_ctx.get("clean_transcript"):
                        meta["clean_transcript"] = meta["source_text"] = u0_ctx["clean_transcript"]
            tracker.start_stage(PipelineStage.L1_IDENTITY)
            tracker.complete_stage(PipelineStage.L1_IDENTITY, status="PASS", details={"resumed": True})
            tracker.start_stage(PipelineStage.L0_ROUTING)
            tracker.complete_stage(PipelineStage.L0_ROUTING, status="PASS", details={"resumed": True})
            return modality, out_p

        _notify(progress_callback, 2, "Stage L1: Modality & Identity Classification")
        _notify(progress_callback, 3, "Stage L0: Deterministic Routing")
        modality, out_p = self._execute_l1_l0(input_path, source_text, meta, output_dir, tracker)
        checkpoint.save_stage("L1_L0", {
            "modality": modality.value,
            "out_p": str(out_p),
            "metadata": meta,
        })
        return modality, out_p

    def _step_c0_pa_with_resume(
        self,
        checkpoint: PipelineCheckpointManager,
        should_resume: bool,
        modality: CanonicalModality,
        source_text: str,
        input_hash: str,
        meta: Dict[str, Any],
        presentation_path: Optional[Union[str, Path]],
        out_p: Path,
        tracker: Any,
        progress_callback: Optional[Callable[[int, int, str], None]],
    ) -> Tuple[Dict[str, Any], str]:
        return step_c0_pa_with_resume(
            pipeline=self,
            checkpoint=checkpoint,
            should_resume=should_resume,
            modality=modality,
            source_text=source_text,
            input_hash=input_hash,
            meta=meta,
            presentation_path=presentation_path,
            out_p=out_p,
            tracker=tracker,
            progress_callback=progress_callback,
        )

    def run(
        self,
        input_path: Union[str, Path, None], metadata: Optional[Dict[str, Any]] = None,
        output_dir: str = "auto", mutate_graph: bool = True,
        directive_id: Optional[str] = None, reviewer: Optional[str] = None, rationale: Optional[str] = None,
        embedding_backend: str = "bge-m3", judge_model: Optional[str] = None, reasoning_effort: Optional[str] = None,
        max_judge_retries: int = 2, progress_callback: Optional[Callable[[int, int, str], None]] = None,
        presentation_path: Optional[Union[str, Path]] = None,
        attachments: Optional[Union[str, Path, List[Union[str, Path]]]] = None, attachment_dir: Optional[Union[str, Path]] = None,
        signal_envelope: Optional[Any] = None, timeout_seconds: Optional[int] = None,
        auto_mutate_on_pass: bool = True,
        extraction_effort: Optional[str] = None, judge_effort: Optional[str] = None,
        test_fast: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        reset_document_checkpoint()
        if test_fast or (metadata and metadata.get("test_fast")) or os.environ.get("NEE_FAST_TEST_MODE"):
            metadata = metadata or {}
            metadata["test_fast"] = True
            metadata.setdefault("skip_swarm", True)
            max_judge_retries = 0

        meta, mutate_graph, auto_mutate_on_pass = prepare_run_metadata(
            self.forced_modality, self.provider_type, metadata, directive_id,
            presentation_path, signal_envelope, auto_mutate_on_pass, mutate_graph, kwargs,
        )
        assert_no_u0_hitl_bypass(meta)
        from dual_pipeline.canonical_pipeline.run_configuration import prepare_run_configuration
        run_config = prepare_run_configuration(
            self.provider, meta, provider_type=self.provider_type, default_effort=self.reasoning_effort,
            extraction_effort=extraction_effort, judge_effort=judge_effort,
            legacy_judge_effort=reasoning_effort, judge_model=judge_model,
        ) if is_document_request(meta) else None
        resolve_pipeline_project_alias(meta, output_dir)
        run_pre_gate_check(meta, metadata, input_path=input_path)

        lock_key = derive_meeting_lock_key(input_path, meta, output_dir)
        test_sandbox = os.environ.get("TEST_SANDBOX_DIR")
        base_runtime_dir = Path(test_sandbox) / "dual_pipeline" if test_sandbox else ROOT / ".local" / "dual_pipeline"
        checkpoint_dir = base_runtime_dir / "checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        sanitize_loaded_metadata(meta)
        should_resume = bool(meta.get("resume", False))
        current_stage = "U0_INGRESS"

        with MeetingIngestionLock(lock_key, input_path=input_path), ExitStack() as execution:
            checkpoint = open_stage_checkpoint(checkpoint_dir / f"checkpoint_{lock_key}.json", meta)
            tracker = self.create_tracker(input_path or "inline_text", meta)
            execution.enter_context(durable_execution_scope(tracker, checkpoint_dir.parent))
            start_t = time.monotonic()
            from config.request_deadline import check_deadline, deadline_scope
            execution.enter_context(deadline_scope(timeout_seconds))

            try:
                current_stage = "U0_INGRESS"
                source_text, input_hash, src_file_path, idempotent = self._step_u0_with_resume(
                    checkpoint=checkpoint, should_resume=should_resume, input_path=input_path,
                    meta=meta, presentation_path=presentation_path, attachments=attachments,
                    attachment_dir=attachment_dir, signal_envelope=signal_envelope,
                    output_dir=output_dir, mutate_graph=mutate_graph, tracker=tracker,
                    progress_callback=progress_callback,
                )
                if idempotent is not None:
                    return idempotent
                meta["_document_source_path"] = str(src_file_path) if src_file_path else None
                lock_document_source(execution, meta, source_text, checkpoint_dir.parent)
                lock_transcript_source(execution, meta, source_text, checkpoint_dir.parent)

                current_stage = "L1_L0_ROUTING"
                modality, out_p = self._step_l1_l0_with_resume(
                    checkpoint=checkpoint, should_resume=should_resume, input_path=input_path,
                    source_text=source_text, meta=meta, output_dir=output_dir,
                    tracker=tracker, progress_callback=progress_callback,
                )

                lock_document_output(execution, meta, out_p, checkpoint_dir.parent)
                lock_transcript_output(execution, meta, out_p, checkpoint_dir.parent)
                current_stage = "C0_PA_CONTEXT"
                pa_context, source_text = self._step_c0_pa_with_resume(
                    checkpoint=checkpoint, should_resume=should_resume, modality=modality,
                    source_text=source_text, input_hash=input_hash, meta=meta,
                    presentation_path=presentation_path, out_p=out_p, tracker=tracker,
                    progress_callback=progress_callback,
                )

                current_stage = "L2_SYNTHESIS"
                tracker.start_stage(PipelineStage.L2_SYNTHESIS)
                run_pre_gate_check(meta, metadata, source_text=source_text, modality=str(modality) if modality else None, tracker=tracker, stage=PipelineStage.L2_SYNTHESIS)

                if meta.get("batch_queue"):
                    return _handle_batch_queue(out_p, meta, source_text, tracker, self.model_name, self.provider_type)

                if modality == CanonicalModality.DOCUMENT or meta.get("is_document"):
                    from dual_pipeline.document_pipeline_core.multi_agent_doc_intelligence import inject_5_agent_intelligence_if_needed
                    inject_5_agent_intelligence_if_needed(source_text, meta)
                    if isinstance(pa_context, dict) and isinstance(pa_context.get("metadata"), dict):
                        for k in ("prior_context", "5_agent_intelligence_report"):
                            if k in meta:
                                pa_context["metadata"][k] = meta[k]

                meta.setdefault("defer_checkpoint_until_exit_qa", True)
                attempt, synthesis_result, eval_result = run_synthesis_with_resume(
                    synthesize=run_synthesis_loop, modality=modality, provider=run_config.provider if run_config else self.provider,
                    provider_type=self.provider_type, model_name=self.model_name,
                    reasoning_effort=run_config.extraction_effort if run_config else self.reasoning_effort,
                    source_text=source_text, pa_context=pa_context, metadata=meta, out_p=out_p, start_t=start_t,
                    max_judge_retries=max_judge_retries, judge_model=judge_model,
                    judge_effort=run_config.judge_effort if run_config else (judge_effort or reasoning_effort),
                    timeout_seconds=timeout_seconds, notify=lambda s, d: _notify(progress_callback, s, d), pipeline=self,
                )
                tracker.complete_stage(PipelineStage.L2_SYNTHESIS, status="PASS", details={"attempts": attempt, "document_resume": meta.get("document_resume"), "transcript_resume": meta.get("transcript_resume")})

                _notify(progress_callback, 6, "Stage X1-X3: Exit QA & Micro-Judge Verification")
                current_stage = "X_EXIT_GATES"
                tracker.start_stage(PipelineStage.X_EXIT_GATES)
                try:
                    verify_exit_qa(tracker, out_p, eval_result, attempt, synthesis_result=synthesis_result, metadata=meta)
                    refresh_validated_transcript_checkpoint(synthesis_result, eval_result, attempt, meta)
                    refresh_validated_document_checkpoint(synthesis_result, eval_result, attempt, meta)
                except Exception as exit_qa_err:
                    _save_partial_qa_failure(out_p, attempt, meta, modality, synthesis_result, exit_qa_err)
                    raise exit_qa_err

                _notify(progress_callback, 7, "Stage EVALS: Authoritative Release & Publication")
                current_stage = "EVALS_RELEASE"
                check_deadline()
                result = self._execute_evals_release(
                    modality, meta, out_p, source_text, src_file_path, synthesis_result,
                    eval_result, mutate_graph, directive_id, reviewer, rationale,
                    embedding_backend, tracker, auto_mutate_on_pass=auto_mutate_on_pass,
                )
                checkpoint.clear()
                reset_document_checkpoint()
                reset_transcript_checkpoint()
                return result
            except Exception as exc:
                handle_pipeline_run_exception(exc, meta, current_stage, input_path)
