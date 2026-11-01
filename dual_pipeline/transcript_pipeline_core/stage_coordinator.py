"""
dual_pipeline/transcript_pipeline_core/stage_coordinator.py

Stage coordinators for RawTranscriptPipeline: preflight ingress, clearance lifecycle, and synthesis fork execution.
Extracts heavy coordination logic to keep pipeline.py under the 350-line modular budget.
"""
from __future__ import annotations

import concurrent.futures
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from dual_pipeline.transcript_pipeline_core.clearance_lifecycle import execute_clearance_lifecycle
from dual_pipeline.transcript_pipeline_core.context_builder import (
    assemble_pa_packet, initialize_remediation_state_machine,
    load_prior_meeting_context, resolve_c0_context_and_attribution,
)
from dual_pipeline.transcript_pipeline_core.ingress import process_u0_ingress
from dual_pipeline.transcript_pipeline_core.preflight_coordinator import (
    ingest_and_align_attachments, resolve_output_directory_and_slug, verify_meeting_deconfliction,
)
from dual_pipeline.transcript_pipeline_core.release_runner import (
    finalize_release_execution, prepare_and_verify_release_package, publish_release_to_graph,
)
from dual_pipeline.transcript_pipeline_core.stage_checkpoint import (
    evaluate_stage_01_checkpoint, evaluate_stage_02_checkpoint, evaluate_stage_03_checkpoint,
)
from dual_pipeline.transcript_pipeline_core.surgical_remediator import SurgicalRemediator
from dual_pipeline.transcript_pipeline_core.uwg_deployer import deploy_uwg_artifacts
from dual_pipeline.unified_agentic_pipeline import PipelineStage

logger = logging.getLogger(__name__)


def prepare_pipeline_execution(
    pipeline: Any, input_path: str, meta: Dict[str, Any], output_dir: str,
    ingestion_context: Optional[Dict[str, Any]], presentation_path: Optional[Union[str, Path]],
    attachments: Optional[Union[str, Path, List[Union[str, Path]]]],
    attachment_dir: Optional[Union[str, Path]], signal_envelope: Optional[Any],
    recorder: Any, tracker: Any, artifacts: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], str, Dict[str, Any], str, Optional[Union[str, Path]]]:
    """Coordinates upstream audio QA, multi-format ingress, path resolution, attachments, and prior context."""
    active_env = signal_envelope or meta.get("signal_envelope") or meta.get("forensic_signal_envelope")
    if active_env is not None:
        logger.info("ForensicSignalEnvelope detected. Evaluating upstream audio QA gates.")
        qa_report = pipeline.upstream_qa.evaluate(active_env, metadata=meta)
        meta["upstream_qa_report"] = qa_report
        if qa_report.verdict in ("REJECT", "NO_SPEECH"):
            from dual_pipeline.speaker_attribution_core.attribution_anomaly_gate import (
                handle_gate_c_attribution_failure,
            )
            qa_report, active_env = handle_gate_c_attribution_failure(
                gatekeeper=pipeline.upstream_qa,
                envelope=active_env,
                qa_rep=qa_report,
                metadata=meta,
                input_path=input_path,
                output_dir=output_dir,
            )
            meta["upstream_qa_report"] = qa_report
            if qa_report.verdict in ("REJECT", "NO_SPEECH"):
                reject_codes = [f.code for f in qa_report.findings if f.severity == "blocking"]
                raise ValueError(
                    f"Upstream Audio QA Gatekeeper REJECTED signal envelope with verdict {qa_report.verdict}. "
                    f"Blocking codes: {reject_codes}"
                )
        if hasattr(qa_report, "model_dump_json"):
            artifacts["upstream_qa_report.json"] = qa_report.model_dump_json(indent=2)

    ingress_res = process_u0_ingress(
        input_path=input_path, metadata=meta, output_dir=output_dir,
        presentation_path=presentation_path, signal_envelope=signal_envelope,
        recorder=recorder, mock=(pipeline.provider_type == "offline"),
    )
    if ingress_res.fast_idempotent:
        logger.warning(
            f"Fast-path idempotency hit: Source matches prior run in {output_dir}. "
            "Bypassing transcription and LLM execution."
        )
        return ingress_res.fast_idempotent, "", meta, output_dir, None

    transcript_text, meta, pres_file = ingress_res.transcript_text, ingress_res.metadata, ingress_res.pres_file
    output_dir, _ = resolve_output_directory_and_slug(
        metadata=meta, transcript_text=transcript_text, output_dir=output_dir, ingestion_context=ingestion_context,
    )
    transcript_text = ingest_and_align_attachments(
        metadata=meta, transcript_text=transcript_text, output_dir=output_dir,
        attachments=attachments, attachment_dir=attachment_dir, input_path=input_path,
    )
    tracker.metadata = meta
    verify_meeting_deconfliction(
        metadata=meta, transcript_text=transcript_text, output_dir=output_dir,
        repo_root=getattr(pipeline, "repo_root", None),
    )
    load_prior_meeting_context(output_dir, meta, transcript_text=transcript_text)

    return None, transcript_text, meta, output_dir, pres_file


def _execute_l2_forensic(
    pipeline: Any,
    u0_context: Dict[str, Any],
    plan: Any,
    transcript_text: str,
    meta: Dict[str, Any],
    state_machine: Any,
    artifacts: Dict[str, Any],
    recorder: Any,
    notify_step: Callable[[int, str], None],
    attempt: int,
    is_notes_mode: bool,
    qa_report: Optional[Dict[str, Any]] = None,
    judge_report: Optional[Dict[str, Any]] = None,
) -> str:
    if is_notes_mode:
        artifacts["source_notes"] = transcript_text
        notify_step(6, f"L2-Stage01: skipped (notes_mode, A{attempt})")
        return ""
    if not state_machine.is_frozen("output_1"):
        prior_f1 = artifacts.get("01_hitl_resolved_transcript.md", "")
        if attempt > 1 and prior_f1 and (qa_report or judge_report):
            repaired_f1, did_surg_1, _ = SurgicalRemediator.remediate_output_1(
                prior_f1,
                qa_report=qa_report or {},
                judge_report=judge_report or {},
                directive=meta.get("remediation_directive_forensic", ""),
                metadata=meta,
            )
            if did_surg_1:
                artifacts["01_hitl_resolved_transcript.md"] = repaired_f1
                notify_step(6, f"L2-Stage01: surgical micro-remediation (A{attempt})")
                return repaired_f1
        with recorder.span("nee.uc1.l2.stage01.forensic"):
            forensic_transcript = pipeline.l2_forensic.execute(
                u0_context, plan, remediation_directive=meta.get("remediation_directive_forensic"),
            )
            artifacts["01_hitl_resolved_transcript.md"] = forensic_transcript
            notify_step(6, f"L2-Stage01: forensic normalizer (A{attempt})")
            return forensic_transcript
    forensic_transcript = state_machine.get_frozen_content("output_1") or transcript_text
    artifacts["01_hitl_resolved_transcript.md"] = forensic_transcript
    return forensic_transcript


def _execute_l2_concurrent_fork(
    pipeline: Any,
    input_s2_s3: str,
    pa_context: Dict[str, Any],
    meta: Dict[str, Any],
    rerun_s2: bool,
    rerun_s3: bool,
    recorder: Any,
    notify_step: Callable[[int, str], None],
    attempt: int,
    current_recap: str,
    current_graph: str,
    artifacts: Dict[str, Any],
) -> Tuple[str, str]:
    from dual_pipeline.canonical_pipeline.child_dispatch import run_parallel_l2_fork
    return run_parallel_l2_fork(
        pipeline=pipeline,
        input_s2_s3=input_s2_s3,
        pa_context=pa_context,
        meta=meta,
        rerun_s2=rerun_s2,
        rerun_s3=rerun_s3,
        recorder=recorder,
        notify_step=notify_step,
        attempt=attempt,
        current_recap=current_recap,
        current_graph=current_graph,
        artifacts=artifacts,
    )


def _execute_l1_post_review(
    pipeline: Any, artifacts: Dict[str, Any], plan: Any, meta: Dict[str, Any], attempt: int, recorder: Any,
) -> None:
    with recorder.span("nee.uc1.l1.post_l2_review"):
        l1_rev = pipeline.l1_reviewer.review_execution(
            artifacts=artifacts, plan=plan, metadata=meta, attempt=attempt
        )
        meta["l1_post_l2_review"] = l1_rev.to_dict()
        artifacts["l1_post_l2_review.json"] = l1_rev.to_dict()
        if l1_rev.verdict.value == "INSUFFICIENT_REPLAN" and l1_rev.replan_directive:
            for r_k, r_v in l1_rev.replan_directive.items():
                if r_k.startswith("remediation_directive_"):
                    meta[r_k] = r_v


def _sync_prior_candidates(
    meta: Dict[str, Any], pa_context: Dict[str, Any], state_machine: Any,
    executive_recap: str, graph_json: str,
) -> None:
    cand_recap = executive_recap or (
        state_machine.get_prior_candidate("output_2") if hasattr(state_machine, "get_prior_candidate") else None
    )
    if cand_recap:
        meta["prior_recap_candidate"] = cand_recap
        pa_context["prior_recap_candidate"] = cand_recap
    cand_graph = graph_json or (
        state_machine.get_prior_candidate("output_3") if hasattr(state_machine, "get_prior_candidate") else None
    )
    if cand_graph:
        meta["prior_graph_candidate"] = cand_graph
        pa_context["prior_graph_candidate"] = cand_graph


def _remediate_output_2_appendix(
    meta: Dict[str, Any], executive_recap: str, qa_report: Dict[str, Any],
    judge_report: Dict[str, Any], artifacts: Dict[str, Any], attempt: int,
    notify_step: Callable[[int, str], None],
) -> Tuple[str, bool]:
    planner = meta.get("component_retry_planner")
    plan = None
    if planner is not None:
        plan = planner.plan_appendix_retry(
            executive_recap, judge_report, attempt=attempt,
            defect_message=str(meta.get("remediation_directive_recap", "")),
        )
        meta["component_plan"] = plan
    repaired, did_surg, _ = SurgicalRemediator.remediate_output_2(
        executive_recap, qa_report=qa_report, judge_report=judge_report,
        directive=meta.get("remediation_directive_recap", ""), metadata=meta,
    )
    if not did_surg:
        return executive_recap, True
    if plan is not None and planner is not None:
        from dual_pipeline.transcript_pipeline_core.surgical_remediator import compute_section_hashes
        inv_ok, violations = planner.verify_protected_component_invariance(
            plan, compute_section_hashes(repaired)
        )
        if not inv_ok:
            logger.warning("Protected section invariance violated: %s", violations)
    artifacts["02_executive_recap.md"] = repaired
    notify_step(7, f"L2-Stage02: surgical micro-remediation (A{attempt})")
    return repaired, False


def execute_l2_attempt(
    pipeline: Any, u0_context: Dict[str, Any], plan: Any, pa_context: Dict[str, Any],
    transcript_text: str, meta: Dict[str, Any], state_machine: Any, artifacts: Dict[str, Any],
    recorder: Any, notify_step: Callable[[int, str], None], attempt: int, is_notes_mode: bool,
    qa_report: Dict[str, Any], judge_report: Dict[str, Any],
) -> Tuple[str, str, str]:
    """Coordinates L2 forensic normalizer, concurrent fork (recap + graph), and L1 post-L2 review."""
    forensic_transcript = _execute_l2_forensic(
        pipeline, u0_context, plan, transcript_text, meta, state_machine,
        artifacts, recorder, notify_step, attempt, is_notes_mode,
        qa_report=qa_report, judge_report=judge_report,
    )
    forensic_transcript = evaluate_stage_01_checkpoint(
        pipeline=pipeline, forensic_transcript=forensic_transcript, source_text=transcript_text,
        metadata=meta, state_machine=state_machine, artifacts=artifacts, recorder=recorder,
        notify_step=notify_step, attempt=attempt, is_notes_mode=is_notes_mode,
        u0_context=u0_context, plan=plan,
    )
    if not is_notes_mode:
        artifacts["01_hitl_resolved_transcript.md"] = forensic_transcript
    if state_machine.is_frozen("output_2") and not artifacts.get("02_executive_recap.md"):
        artifacts["02_executive_recap.md"] = state_machine.get_frozen_content("output_2")
    if state_machine.is_frozen("output_3") and not artifacts.get("03_canonical_signal_projection.json"):
        artifacts["03_canonical_signal_projection.json"] = state_machine.get_frozen_content("output_3")

    rerun_s2 = not state_machine.is_frozen("output_2") or not artifacts.get("02_executive_recap.md")
    rerun_s3 = not state_machine.is_frozen("output_3") or not artifacts.get("03_canonical_signal_projection.json")
    input_s2_s3 = transcript_text if is_notes_mode else forensic_transcript
    executive_recap = artifacts.get("02_executive_recap.md", "")
    graph_json = artifacts.get("03_canonical_signal_projection.json", "")

    _sync_prior_candidates(meta, pa_context, state_machine, executive_recap, graph_json)

    if attempt > 1 and rerun_s2 and executive_recap:
        executive_recap, rerun_s2 = _remediate_output_2_appendix(
            meta, executive_recap, qa_report, judge_report, artifacts, attempt, notify_step
        )

    executive_recap, graph_json = _execute_l2_concurrent_fork(
        pipeline, input_s2_s3, pa_context, meta, rerun_s2, rerun_s3,
        recorder, notify_step, attempt, executive_recap, graph_json, artifacts,
    )
    if rerun_s2:
        executive_recap = evaluate_stage_02_checkpoint(
            pipeline=pipeline, executive_recap=executive_recap, source_text=transcript_text,
            metadata=meta, state_machine=state_machine, artifacts=artifacts, recorder=recorder,
            notify_step=notify_step, attempt=attempt, is_notes_mode=is_notes_mode,
            forensic_transcript=forensic_transcript, plan=plan,
        )
    else:
        executive_recap = state_machine.get_frozen_content("output_2") or executive_recap
        artifacts["02_executive_recap.md"] = executive_recap
    if rerun_s3:
        graph_json = evaluate_stage_03_checkpoint(
            pipeline=pipeline, canonical_projection=graph_json, forensic_transcript=forensic_transcript,
            metadata=meta, state_machine=state_machine, artifacts=artifacts, recorder=recorder,
            notify_step=notify_step, attempt=attempt, is_notes_mode=is_notes_mode,
        )
    else:
        graph_json = state_machine.get_frozen_content("output_3") or graph_json
        artifacts["03_canonical_signal_projection.json"] = graph_json

    _execute_l1_post_review(pipeline, artifacts, plan, meta, attempt, recorder)
    return forensic_transcript, executive_recap, graph_json


def execute_analysis_and_context_stages(
    pipeline: Any, tracker: Any, recorder: Any, u0_context: Dict[str, Any], transcript_text: str,
    input_path: str, is_notes_mode: bool, meta: Dict[str, Any], output_dir: str,
    max_judge_retries: int, notify_step: Callable[[int, str], None],
) -> Tuple[Any, Dict[str, Any], Dict[str, Any], str, Any, Dict[str, Any]]:
    """Executes stages L1 (Plan), L0 (Routing), C0 (Context Gate & Attribution), and PA (Packet Assembly)."""
    tracker.start_stage(PipelineStage.L1_IDENTITY)
    with recorder.span("nee.uc1.l1.plan"):
        plan = pipeline.l1.plan(u0_context)
        notify_step(2, f"L1-plan: {plan.source_type}")
    tracker.complete_stage(
        PipelineStage.L1_IDENTITY,
        status="PASS",
        details={"source_type": plan.source_type, "diarization_status": plan.diarization_status},
    )

    tracker.start_stage(PipelineStage.L0_ROUTING)
    with recorder.span("nee.uc1.l0.route"):
        eff_tx = u0_context.get("clean_transcript") or transcript_text
        l0_context = pipeline.l0.execute(u0_context, eff_tx, plan)
        notify_step(3, "L0: deterministic router")
    tracker.complete_stage(
        PipelineStage.L0_ROUTING,
        status="PASS",
        details={"statements_count": len(l0_context.get("statements", []))},
    )

    tracker.start_stage(PipelineStage.C0_PA_CONTEXT)
    with recorder.span("nee.uc1.c0.entity_resolution"):
        c0_context, transcript_text, is_pre_resolved_input = resolve_c0_context_and_attribution(
            plan=plan, u0_context=u0_context, l0_context=l0_context, transcript_text=transcript_text,
            input_path=input_path, is_notes_mode=is_notes_mode, c0_native=pipeline.c0_native,
            c0_undiarized=pipeline.c0_undiarized, auto_scaffolder=pipeline.auto_scaffolder,
        )
        notify_step(4, "C0: context gate")

    state_machine = initialize_remediation_state_machine(
        metadata=meta, output_dir=output_dir, max_judge_retries=max_judge_retries,
        is_pre_resolved_input=is_pre_resolved_input, is_notes_mode=is_notes_mode,
        transcript_text=transcript_text,
    )

    with recorder.span("nee.uc1.pa.assemble"):
        pa_context = assemble_pa_packet(pipeline.pa, l0_context, meta)
        notify_step(5, "PA: packet assembly")

    tracker.complete_stage(
        PipelineStage.C0_PA_CONTEXT,
        status="PASS",
        details={
            "conformance_checked": l0_context.get("conformance_checked", True),
            "roster_gate_cleared": l0_context.get("roster_gate_cleared", True),
        },
    )
    return plan, l0_context, c0_context, transcript_text, state_machine, pa_context


def execute_evals_and_release(
    pipeline: Any, tracker: Any, artifacts: Dict[str, Any], metadata: Dict[str, Any],
    output_dir: str, qa_report: Dict[str, Any], judge_report: Dict[str, Any],
    final_decision: Dict[str, Any], pres_file: Optional[Union[str, Path]],
    is_notes_mode: bool, mutate_graph: bool, directive_id: Optional[str],
    reviewer: Optional[str], rationale: Optional[str], embedding_backend: str,
) -> Dict[str, Any]:
    """Executes Stage EVALS: UWG deployment, release package verification, graph publication, and finalization."""
    tracker.start_stage(PipelineStage.EVALS_RELEASE)
    write_fn = getattr(pipeline, "_uwg_write", deploy_uwg_artifacts)
    written = write_fn(
        artifacts=artifacts, metadata=metadata, output_dir=output_dir,
        qa_report=qa_report, judge_report=judge_report, final_decision=final_decision,
        presentation_path=pres_file,
    )
    package_path = prepare_and_verify_release_package(
        written=written, metadata=metadata, artifacts=artifacts, is_notes_mode=is_notes_mode,
    )
    if mutate_graph:
        publish_release_to_graph(
            package_path=package_path, metadata=metadata, written=written,
            directive_id=directive_id, reviewer=reviewer, rationale=rationale,
            embedding_backend=embedding_backend,
        )
    finalize_release_execution(
        tracker=tracker, written=written, package_path=package_path, mutate_graph=mutate_graph, metadata=metadata,
    )
    return written
