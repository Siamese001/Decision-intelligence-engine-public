"""
dual_pipeline/canonical_pipeline/lifecycle.py

Execution lifecycle helpers for CanonicalAgenticPipeline:
Handles routing resolution, metadata scaffolding, synthesis loop execution,
and release packaging and validation.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple, Union

from dual_pipeline.canonical_pipeline.attachments import stage_deliverable_attachments, try_resume_passing_attempt
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.strategies.qa import QAEvaluationResult, get_qa_strategy
from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult, get_synthesis_strategy
from dual_pipeline.core import ROOT
from dual_pipeline.meeting_deconfliction import MeetingCollisionDetector, MeetingDeconflictionError
from dual_pipeline.meeting_paths import normalize_meeting_date, standardize_meeting_slug
from tools.validate_output_hierarchy import DATE_FOLDER_REGEX
from .document_remediation import prepare_document_patch_or_handoff, synthesize_with_document_handoff
from .synthesis_loop_support import (
    check_candidate_loop_termination,
    check_deliverable_01_deploy_action,
    handle_identical_candidate_fallback,
    inject_prior_candidates_into_context,
    prepare_synthesis_attempts,
    purge_stale_meeting_deliverables_for_document,
    seed_regeneration_remediation_directives,
    select_high_water_candidates,
)
logger = logging.getLogger("CanonicalAgenticPipeline.lifecycle")
from .worktree_inheritance import (
    inherit_worktree_from_metadata as _inherit_worktree_from_input_path,
    hydrate_inherited_metadata_slug,
    resolve_inherited_target_path,
)


def calculate_target_path(
    output_dir: str,
    proj: str,
    date_fn: str,
    slug: str,
    no_subfolder: bool,
    modality: Optional[CanonicalModality],
    meta: Optional[Dict[str, Any]] = None,
) -> Path:
    """Calculate destination path according to output directory hierarchy and worktree isolation."""
    if output_dir == "auto":
        base_out = ROOT / "output" / proj / date_fn if no_subfolder else ROOT / "output" / proj / date_fn / slug
    else:
        base_out = Path(output_dir).resolve()
        if not (no_subfolder or base_out.name == slug or (base_out.parent and DATE_FOLDER_REGEX.match(base_out.parent.name))):
            if DATE_FOLDER_REGEX.match(base_out.name):
                base_out = base_out / slug
            elif base_out.name == proj:
                base_out = base_out / date_fn / slug
            elif base_out.name == "output" or modality == CanonicalModality.DOCUMENT:
                base_out = base_out / proj / date_fn / slug

    meta_dict = meta if meta is not None else {}
    meta_dict["enforce_worktree"] = True

    from .worktree_inheritance import resolve_target_worktree_path
    return resolve_target_worktree_path(
        base_out=base_out,
        meta_dict=meta_dict,
        proj=proj,
        date_fn=date_fn,
        slug=slug,
        no_subfolder=no_subfolder,
        output_dir=output_dir,
        repo_root=ROOT,
        modality=modality,
    )


def _resolve_project_name(meta: Dict[str, Any], output_dir: str) -> str:
    """Extract and validate governed project name from metadata or output directory."""
    from dual_pipeline.query_router_core.project_router import determine_project_directory
    cand = determine_project_directory(meta)
    if cand:
        meta["project"] = cand
        return cand
    proj = meta.get("project")
    if not proj and output_dir != "auto":
        out_parts = Path(output_dir).parts
        for idx, part in enumerate(out_parts[:-1]):
            if part == "output" and idx + 1 < len(out_parts):
                proj = out_parts[idx + 1]
                break
    if proj and proj.lower() != "auto":
        from dual_pipeline.transcript_pipeline_core.preflight_coordinator import (
            _validate_project_alias,
        )
        proj = _validate_project_alias(proj)
        meta["project"] = proj
        return proj
    return "internal_consulting"


def _is_in_place_continuation(meta: Dict[str, Any], existing_path: Optional[str]) -> bool:
    if meta.get("force_rerun") or meta.get("resume") or meta.get("regenerate"):
        return True
    inp = meta.get("input_path") or meta.get("source_path") or meta.get("raw_transcript_path")
    if inp:
        inp_p = Path(str(inp))
        if "hitl_resolved_transcript" in inp_p.name or "live_transcript" in inp_p.name:
            return True
        if existing_path and inp_p.exists():
            try:
                if Path(existing_path).resolve() in (inp_p.resolve(), *inp_p.resolve().parents):
                    return True
            except Exception:
                pass
    return False


def _check_meeting_collision(meta: Dict[str, Any], proj: str, date_fn: str, slug: str) -> None:
    """Verify meeting candidate does not collide with an existing ingested meeting."""
    if meta.get("allow_duplicate"):
        return
    detector = MeetingCollisionDetector()
    coll = detector.check_candidate_collision(
        project=proj, date_str=date_fn, candidate_slug=slug, candidate_time=meta.get("time")
    )
    if coll.is_collision:
        if _is_in_place_continuation(meta, coll.existing_path):
            meta["collision_existing_slug"] = coll.existing_slug
            meta["collision_existing_path"] = coll.existing_path
        else:
            raise MeetingDeconflictionError(coll)
    wt_dir_str = meta.get("worktree_dir")
    if wt_dir_str:
        wt_root = Path(wt_dir_str)
        if wt_root != ROOT and (wt_root / "output" / proj).exists():
            wt_detector = MeetingCollisionDetector(repo_root=wt_root)
            wt_coll = wt_detector.check_candidate_collision(
                project=proj, date_str=date_fn, candidate_slug=slug, candidate_time=meta.get("time")
            )
            if wt_coll.is_collision:
                if _is_in_place_continuation(meta, wt_coll.existing_path):
                    meta["collision_existing_slug"] = wt_coll.existing_slug
                    meta["collision_existing_path"] = wt_coll.existing_path
                else:
                    raise MeetingDeconflictionError(wt_coll)


def resolve_routing(
    meta: Dict[str, Any],
    output_dir: str,
    modality: Optional[CanonicalModality] = None,
) -> Path:
    """Deterministic routing resolution (Stage L0)."""
    date_fn, date_disp = normalize_meeting_date(meta.get("date") or "01-01-2026")
    meta["date_filename"], meta["date"] = date_fn, date_disp

    hydrate_inherited_metadata_slug(meta, ROOT)

    raw_title = meta.get("meeting_name") or meta.get("title") or "Ingested_Item"
    proj = _resolve_project_name(meta, output_dir)
    existing_slug = meta.get("filename_slug")
    slug = (
        standardize_meeting_slug(raw_title, initial_slug=existing_slug, max_len=40, project_name=proj)
        if existing_slug
        else standardize_meeting_slug(raw_title, attendees=meta.get("attendees"), max_len=40, project_name=proj)
    )
    meta["filename_slug"] = slug

    no_sub = bool(meta.get("no_subfolder", False))
    out_p = calculate_target_path(output_dir, proj, date_fn, slug, no_sub, modality, meta=meta)
    meta["output_dir"] = str(out_p)

    _check_meeting_collision(meta, proj, date_fn, slug)
    out_p.mkdir(parents=True, exist_ok=True)
    return out_p


def scaffold_metadata(
    modality: CanonicalModality, input_path: Union[str, Path, None], meta: Dict[str, Any], source_text: Optional[str] = None
) -> Dict[str, Any]:
    """Scaffold metadata according to detected canonical modality (Stage L1)."""
    if modality == CanonicalModality.DOCUMENT:
        from dual_pipeline.document_pipeline_core.metadata_helper import scaffold_document_metadata
        scaffolded = scaffold_document_metadata(
            input_path=input_path, metadata_path=meta.get("metadata_path"), project=meta.get("project"),
            title=meta.get("title") or meta.get("meeting_name"),
            author=meta.get("author") or meta.get("organizer"),
            date_str=meta.get("date") or meta.get("date_str"),
            classification=meta.get("classification"), audiences=meta.get("audiences"),
            metadata=meta, source_text=source_text,
        )
        scaffolded.update(meta)
        scaffolded.update({"is_document": True, "output3_only": True, "skip_output_1": True, "skip_output_2": True, "outputs": ["03"], "canonical_modality": CanonicalModality.DOCUMENT.value})
        scaffolded.setdefault("source_type", "document")
        return scaffolded

    from dual_pipeline.transcript_metadata_helper import scaffold_meeting_metadata
    scaffolded = scaffold_meeting_metadata(
        input_path=input_path,
        project=meta.get("project"),
        meeting_name=meta.get("meeting_name"),
        date_str=meta.get("date") or meta.get("date_str"),
        attendees=meta.get("attendees"),
        time_str=meta.get("time") or meta.get("time_str"),
        classification=meta.get("classification", "confidential"),
    )
    scaffolded.update(meta)
    if modality == CanonicalModality.MEETING_NOTES:
        scaffolded["notes_mode"] = True
        scaffolded["skip_output_1"] = True
    return scaffolded


def _extract_next_directive(
    eval_result: QAEvaluationResult,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    dec = eval_result.final_decision
    from dual_pipeline.remediation_core import RemediationDiffInjector  # noqa: E402, PLC0415

    directives = []
    out1_failed = not dec.get("output_1_deterministic_gates_pass", False) or not dec.get("output_1_pass", False)
    out2_failed = not dec.get("output_2_deterministic_gates_pass", False) or not dec.get("output_2_pass", False)
    out3_failed = not dec.get("output_3_deterministic_gates_pass", False) or not dec.get("output_3_pass", False)

    for out_failed, skip_key, okey, dkey in [
        (out1_failed, "output_1_skipped", "output_1", "remediation_directive_forensic"),
        (out2_failed, "output_2_skipped", "output_2", "remediation_directive_recap"),
        (out3_failed, "output_3_skipped", "output_3", "remediation_directive_graph"),
    ]:
        if out_failed and not dec.get(skip_key, False):
            d = RemediationDiffInjector.extract_directive(okey, eval_result.judge_report, eval_result.qa_report)
            if d:
                directives.append(d)
                if metadata is not None:
                    metadata[dkey] = d
        elif metadata is not None:
            metadata.pop(dkey, None)
            if isinstance(metadata.get("metadata"), dict):
                metadata["metadata"].pop(dkey, None)

    if not directives:
        t_key = "output_1" if not dec.get("output_1_pass") else ("output_2" if not dec.get("output_2_pass") else "output_3")
        return RemediationDiffInjector.extract_directive(t_key, eval_result.judge_report, eval_result.qa_report)

    combined = "\n\n".join(directives)
    if metadata is not None:
        metadata["remediation_directive"] = combined
    return combined


def _record_attempt_artifacts(
    out_p: Path, attempt: int, eval_result: QAEvaluationResult,
    synthesis_result: Optional[SynthesisResult] = None, meta: Optional[Dict[str, Any]] = None,
) -> None:
    attempt_dir = out_p / "attempts" / f"attempt_{attempt}"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    (attempt_dir / "qa_gates_report.json").write_text(json.dumps(eval_result.qa_report, indent=2), encoding="utf-8")
    (attempt_dir / "llm_judge_evaluation.json").write_text(json.dumps(eval_result.judge_report, indent=2), encoding="utf-8")
    (attempt_dir / "final_release_decision.json").write_text(json.dumps(eval_result.final_decision, indent=2), encoding="utf-8")
    if meta:
        keys = ("input_hash", "source_hash", "audio_sha256", "binding_sha256", "collateral_digest", "meeting_name", "date", "date_filename", "filename_slug", "attempt", "run_id")
        att_meta = {k: v for k in keys if (v := meta.get(k)) is not None and isinstance(v, (str, int, float, bool))}
        (attempt_dir / "metadata.json").write_text(json.dumps(att_meta, indent=2), encoding="utf-8")
    if synthesis_result and meta:
        slug, d_val = meta.get("filename_slug", "Item"), meta.get("date_filename", "01-01-2026")
        for attr, fname in [("output_1", "output_1.ckpt.md"), ("output_2", "output_2.ckpt.md")]:
            if val := getattr(synthesis_result, attr, None):
                (attempt_dir / fname).write_text(val, encoding="utf-8")
        if synthesis_result.output_3:
            (attempt_dir / f"03_{slug}_{d_val}_canonical_signal_projection.json").write_text(synthesis_result.output_3, encoding="utf-8")
            (attempt_dir / "output_3.ckpt.json").write_text(synthesis_result.output_3, encoding="utf-8")


def _resolve_judge_tier(
    metadata: Dict[str, Any], judge_model: Optional[str], judge_effort: Optional[str]
) -> Tuple[Optional[str], Optional[str]]:
    if not judge_model and metadata.get("judge_tier"):
        from dual_pipeline.model_registry import ModelTier, resolve_tier_model
        jt = str(metadata["judge_tier"]).strip().lower()
        tier = ModelTier.FAST_ROUTING if jt == "fast" else (ModelTier.FRONTIER_REASONING if jt == "standard" else ModelTier.DIALECTICAL_JUDGE)
        judge_model, tier_effort = resolve_tier_model(tier)
        judge_effort = judge_effort or tier_effort
    return judge_model, judge_effort


def _resolve_synthesis_limits(
    metadata: Dict[str, Any], modality: CanonicalModality, max_judge_retries: int,
) -> Tuple[int, bool]:
    if metadata.get("test_fast") or os.environ.get("NEE_FAST_TEST_MODE"):
        return 1, True
    return max(1, min(int(max_judge_retries) + 1, 2 if modality == CanonicalModality.DOCUMENT else 3)), False


def run_synthesis_loop(
    modality: CanonicalModality, provider: Any, provider_type: str, model_name: str, reasoning_effort: str,
    source_text: str, pa_context: Dict[str, Any], metadata: Dict[str, Any], out_p: Path, start_t: float,
    max_judge_retries: int = 2, judge_model: Optional[str] = None, judge_effort: Optional[str] = None,
    timeout_seconds: Optional[int] = None, notify: Optional[Callable[[int, str], None]] = None, pipeline: Optional[Any] = None,
) -> Tuple[int, SynthesisResult, QAEvaluationResult]:
    """Executes iterative dialectical synthesis and micro-judge loop (Stage L2) with monotonic progression."""
    from dual_pipeline.canonical_pipeline.attempt_binding import BoundAttemptLedger
    from config.request_deadline import check_deadline, current_suspended_seconds
    from dual_pipeline.l0.ingestion_timing import attempt_scope
    from dual_pipeline.remediation_core.state_machine import SelectiveRemediationStateMachine

    synthesis_strategy, qa_strategy = get_synthesis_strategy(modality, provider, pipeline=pipeline), get_qa_strategy(modality)
    max_attempts, fail_fast_remediation = _resolve_synthesis_limits(metadata, modality, max_judge_retries)
    synthesis_result: Optional[SynthesisResult] = None
    eval_result: Optional[QAEvaluationResult] = None
    current_directive: Optional[str] = metadata.get("remediation_directive")

    state_machine = SelectiveRemediationStateMachine(max_attempts=max_attempts, fail_fast=fail_fast_remediation)
    bound_attempts = BoundAttemptLedger(source_text, modality, output3_only=bool(metadata.get("output3_only")))
    metadata["state_machine"] = pa_context["state_machine"] = state_machine
    if modality == CanonicalModality.DOCUMENT:
        metadata.pop("document_patch_request", None)
        pa_context.pop("document_patch_request", None)

    judge_model, judge_effort = _resolve_judge_tier(metadata, judge_model, judge_effort)
    from .checkpoint_memoizer import check_fast_fail_or_circuit_breaker, memoize_substage_checkpoints
    from dual_pipeline.token_optimization.circuit_breaker import CandidateCircuitBreaker
    memoized = memoize_substage_checkpoints(out_p, state_machine, metadata, modality)
    if metadata.get("regenerate"):
        state_machine.seed_for_selective_regeneration(out_p, metadata["regenerate"], metadata=metadata)
        seed_regeneration_remediation_directives(out_p, state_machine, metadata, pa_context)
    elif metadata.get("resume") and any(memoized.values()) and not all(memoized.values()):
        seed_regeneration_remediation_directives(out_p, state_machine, metadata, pa_context)
    prepare_synthesis_attempts(modality, source_text, metadata, out_p)
    cb = metadata.get("circuit_breaker")
    if not isinstance(cb, CandidateCircuitBreaker):
        metadata["circuit_breaker"] = cb = CandidateCircuitBreaker(max_consecutive_failures=2)

    resumed = try_resume_passing_attempt(out_p, metadata, modality)
    if resumed is not None:
        return resumed

    prior_cand_digest: Optional[str] = None
    for attempt in range(1, max_attempts + 1):
        metadata.update({"attempt": attempt, "max_attempts": max_attempts, "retry_count": max(0, attempt - 1)})
        with attempt_scope(attempt) as attempt_trace:
            check_deadline()
            if notify:
                notify(5, f"Stage L2: Dialectical Synthesis (Attempt {attempt}/{max_attempts})")
            if timeout_seconds and (time.monotonic() - start_t - current_suspended_seconds()) > timeout_seconds:
                raise TimeoutError(f"Pipeline timed out after {int(time.monotonic() - start_t - current_suspended_seconds())}s at attempt {attempt}")

            inject_prior_candidates_into_context(state_machine, metadata, pa_context)
            from dual_pipeline.l0.ingestion_timing import timed_child_span
            with timed_child_span("extraction", {"attempt": attempt, "modality": str(modality)}):
                synthesis_result = synthesize_with_document_handoff(
                    synthesis_strategy, source_text=source_text, pa_context=pa_context,
                    metadata=metadata, attempt=attempt, directive=current_directive,
                    prior_synthesis=synthesis_result, prior_evaluation=eval_result,
                    output_dir=out_p, is_document=modality == CanonicalModality.DOCUMENT,
                )
            if synthesis_result is None:
                attempt_trace.finish(status="REVIEW_REQUIRED")
                break
            check_deadline()

            should_stop, new_cand_digest = check_candidate_loop_termination(attempt, synthesis_result, prior_cand_digest)
            if should_stop:
                can_continue, fallback_result, new_cand_digest = handle_identical_candidate_fallback(
                    attempt=attempt, max_attempts=max_attempts, metadata=metadata, pa_context=pa_context,
                    synthesis_strategy=synthesis_strategy, source_text=source_text,
                    current_directive=current_directive, prior_digest=prior_cand_digest,
                )
                if not can_continue:
                    attempt_trace.finish(status=eval_result.final_decision.get("status", "FAIL") if eval_result else "FAIL")
                    break
                synthesis_result = fallback_result
            prior_cand_digest = new_cand_digest

            with timed_child_span("judge", {"attempt": attempt, "judge_model": judge_model or model_name}):
                eval_result = qa_strategy.evaluate(
                    synthesis_result=synthesis_result, source_text=source_text, metadata=metadata,
                    attempt=attempt, max_attempts=max_attempts, mock=(provider_type == "offline"),
                    judge_model=judge_model or model_name, reasoning_effort=judge_effort or reasoning_effort,
                    fast_judge=bool(metadata.get("fast_judge", False)),
                )
            check_deadline()

            bound_attempts.record(attempt, synthesis_result, eval_result)
            _record_attempt_artifacts(out_p, attempt, eval_result, synthesis_result, metadata)
            artifacts_dict = {"output_1": synthesis_result.output_1 or "", "output_2": synthesis_result.output_2 or "", "output_3": synthesis_result.output_3 or ""}
            state_machine.evaluate_attempt(eval_result.final_decision, eval_result.judge_report, eval_result.qa_report, artifacts=artifacts_dict)

            if eval_result.authoritative_write_authorized:
                cb = metadata.get("circuit_breaker")
                if cb and hasattr(cb, "record_success"):
                    cb.record_success()
                attempt_trace.finish(status=eval_result.final_decision.get("status", "PASS"))
                break

            failures = eval_result.final_decision.get("failures") or []
            if any(any(k in str(f).lower() for k in ("collapse", "single_speaker", "contradiction", "unconsumed", "partner_organization", "generic_speaker", "unattributed")) for f in failures):
                from dual_pipeline.speaker_attribution_core.attribution_anomaly_gate import quarantine_unapproved_drafts
                from dual_pipeline.exceptions import AttributionAnomalyHaltError
                quarantined = quarantine_unapproved_drafts(out_p, container_dir=metadata.get("container_dir"))
                attempt_trace.finish(status="BLOCKED")
                raise AttributionAnomalyHaltError(
                    f"Gate 1.2 Attribution Anomaly detected in synthesis output. Blind LLM retries prohibited. Failures: {failures}",
                    quarantined_paths=quarantined,
                )

            cb = metadata.get("circuit_breaker")
            halt_status = check_fast_fail_or_circuit_breaker(cb, eval_result, synthesis_result, attempt, max_attempts)
            if not halt_status and attempt < max_attempts:
                metadata.update({"last_qa_report": eval_result.qa_report, "last_judge_report": eval_result.judge_report})
                current_directive = _extract_next_directive(eval_result, metadata=metadata)
                if modality == CanonicalModality.DOCUMENT and not prepare_document_patch_or_handoff(
                    metadata, candidate=synthesis_result.output_3, judge_report=eval_result.judge_report, source_text=source_text, directive=current_directive, output_dir=out_p, attempt=attempt
                ):
                    halt_status = "REVIEW_REQUIRED"
                else:
                    state_machine.advance_attempt()

            observed_status = halt_status or eval_result.final_decision.get("status", "FAIL")
            synthesis_result, eval_result = bound_attempts.restore(attempt, synthesis_result, eval_result, state_machine)
            attempt_trace.finish(status=observed_status)
            if halt_status:
                break

    if (modality != CanonicalModality.DOCUMENT and synthesis_result
            and not eval_result.authoritative_write_authorized
            and select_high_water_candidates(state_machine, modality, synthesis_result)):
        with attempt_scope(max_attempts + 1) as attempt_trace:
            for rem_k in ("remediation_directive", "remediation_directive_recap", "remediation_directive_forensic", "remediation_directive_graph"):
                metadata.pop(rem_k, None)
            eval_result = qa_strategy.evaluate(
                synthesis_result=synthesis_result, source_text=source_text, metadata=metadata,
                attempt=max_attempts + 1, max_attempts=max_attempts + 1, mock=(provider_type == "offline"),
                judge_model=judge_model or model_name, reasoning_effort=judge_effort or reasoning_effort,
            )
            bound_attempts.record(max_attempts + 1, synthesis_result, eval_result)
            _record_attempt_artifacts(out_p, max_attempts + 1, eval_result, synthesis_result, metadata)
            attempt = max_attempts + 1
            attempt_trace.finish(status=eval_result.final_decision.get("status", "FAIL"))

    return attempt, synthesis_result, eval_result


def deploy_deliverables(
    out_p: Path,
    meta: Dict[str, Any],
    modality: CanonicalModality,
    synthesis_result: SynthesisResult,
) -> Dict[str, str]:
    """Materializes final approved deliverable files to destination path."""
    slug = meta.get("filename_slug") or standardize_meeting_slug(meta.get("meeting_name") or "Meeting", max_len=40)
    date_dirname = meta.get("date_filename")
    if not date_dirname and meta.get("date"):
        date_dirname, _ = normalize_meeting_date(meta["date"])
    date_dirname = date_dirname or "01-01-2026"
    target_dir = Path(meta["target_output_dir"]) if meta.get("target_output_dir") else (out_p.parent / out_p.name[9:-9] if out_p.name.startswith(".staging_") else out_p); purge_stale_meeting_deliverables_for_document(out_p, modality)
    existing_01 = (list(target_dir.glob("01_*_hitl_resolved_transcript.md")) if target_dir.is_dir() else []) or list(out_p.glob("01_*_hitl_resolved_transcript.md"))
    t_fn = meta.get("time_filename") or (re.sub(r"[:\s\-\u2013]+", "", meta["time"]) if meta.get("time") else None)
    base = existing_01[0].name[3:-28] if existing_01 else (f"{slug}_{date_dirname}_{t_fn}" if t_fn else f"{slug}_{date_dirname}")
    artifacts_map: Dict[str, str] = {}

    if modality in (CanonicalModality.AUDIO, CanonicalModality.MEETING_TRANSCRIPT):
        missing = [k for k, v in (("01_hitl_resolved_transcript", synthesis_result.output_1), ("02_executive_recap", synthesis_result.output_2), ("03_canonical_signal_projection", synthesis_result.output_3)) if not v]
        if missing:
            raise RuntimeError(f"Transcript synthesis modality '{modality.value}' requires deliverables 01, 02, and 03. Missing required deliverable(s): {', '.join(missing)}")

    if synthesis_result.output_1 and modality in (CanonicalModality.AUDIO, CanonicalModality.MEETING_TRANSCRIPT):
        f1_path = out_p / f"01_{base}_hitl_resolved_transcript.md"
        should_write, text_to_write = check_deliverable_01_deploy_action(f1_path, out_p, synthesis_result, meta)
        if should_write:
            f1_path.write_text(text_to_write, encoding="utf-8")
        artifacts_map["01_transcript"] = str(f1_path)

    if synthesis_result.output_2 and modality in (CanonicalModality.AUDIO, CanonicalModality.MEETING_TRANSCRIPT, CanonicalModality.MEETING_NOTES):
        f2_path = out_p / f"02_{base}_executive_recap.md"
        f2_path.write_text(synthesis_result.output_2, encoding="utf-8")
        artifacts_map["02_recap"] = str(f2_path)

    if synthesis_result.output_3:
        f3_path = out_p / f"03_{base}_canonical_signal_projection.json"
        f3_path.write_text(synthesis_result.output_3, encoding="utf-8")
        artifacts_map["03_graph"] = str(f3_path)

    if modality == CanonicalModality.MEETING_NOTES:
        f_src = out_p / f"{base}_source.txt"
        f_src.write_text(meta.get("source_text") or "", encoding="utf-8")
        artifacts_map["source_notes"] = str(f_src)

    if meta.get("source_manifest"):
        sm_file = out_p / "source_manifest.json"
        sm_file.write_text(json.dumps(meta["source_manifest"], indent=2), encoding="utf-8")
        artifacts_map["source_manifest"] = str(sm_file)

    pres_p = meta.get("presentation_path")
    if pres_p:
        from dual_pipeline.transcript_pipeline_core.uwg_deployer import (
            _write_presentation_intake_if_present,
        )
        pres_file = _write_presentation_intake_if_present(out_p, pres_p, meta)
        if pres_file:
            artifacts_map["presentation_intake"] = pres_file

    if meta.get("backtest") or meta.get("run_mode") == "backtest":
        from dual_pipeline.backtest_core.comparison import generate_backtest_comparison_report
        prior_dir = Path(meta["target_output_dir"]) if meta.get("target_output_dir") else (
            out_p.parent / out_p.name[9:-9] if out_p.name.startswith(".staging_") else out_p
        )
        comp_report = generate_backtest_comparison_report(
            prior_dir=prior_dir,
            current_output_2=synthesis_result.output_2 or "",
            current_output_3=synthesis_result.output_3 or "{}",
            output_dir=out_p,
            metadata=meta,
        )
        artifacts_map["backtest_comparison_report"] = str(comp_report)

    if meta.get("dry_run_graph"):
        from dual_pipeline.graph_preview_core.preview import generate_graph_delta_preview, resolve_preview_graph_path
        graph_p = resolve_preview_graph_path(ROOT, meta)
        generate_graph_delta_preview(output_3=synthesis_result.output_3 or "{}", graph_path=graph_p, output_dir=out_p, metadata=meta)
        artifacts_map["graph_delta_preview_json"] = str(out_p / "graph_delta_preview.json")
        artifacts_map["graph_delta_preview_md"] = str(out_p / "graph_delta_preview.md")

    stage_deliverable_attachments(out_p, meta, target_dir)
    return artifacts_map


def resolve_raw_source_path(artifacts_map: Dict[str, str], src_file_path: Optional[Path], out_p: Path, source_text: str) -> Path:
    """Resolve and ensure existence of raw source file path."""
    t1, ts = artifacts_map.get("01_transcript"), artifacts_map.get("source_notes")
    if t1 or ts:
        raw_source_path = Path(t1 or ts)
    elif src_file_path and src_file_path.suffix.lower() in {".txt", ".md", ".vtt", ".srt", ".html", ".eml", ".csv"}:
        raw_source_path = src_file_path
    else:
        raw_source_path = out_p / "source_document.txt"

    if not raw_source_path.exists():
        raw_source_path.write_text(source_text, encoding="utf-8")
    if not t1 and not ts and not (out_p / "source_document.txt").exists() and source_text:
        (out_p / "source_document.txt").write_text(source_text, encoding="utf-8")
    return raw_source_path


def check_directory_compliance(out_p: Path, proj: Optional[str]) -> None:
    """Validate directory hierarchy against compliance invariants."""
    if out_p.name.startswith((".staging", ".tmp")):
        return
    with contextlib.suppress(Exception):
        is_output_repo = out_p.resolve().is_relative_to((ROOT / "output").resolve())

    has_date_in_name = bool(DATE_FOLDER_REGEX.match(out_p.name))
    has_date_in_parent = bool(out_p.parent and DATE_FOLDER_REGEX.match(out_p.parent.name))
    if not (is_output_repo or has_date_in_name or has_date_in_parent):
        return

    from dual_pipeline.transcript_pipeline_core import release_runner
    target_slug = out_p.name if has_date_in_parent else None
    target_val = out_p.parent if has_date_in_parent else out_p
    violations = release_runner.validate_meeting_directory(target_val, project_name=proj, allow_active_recording=False, target_slug=target_slug)
    if violations:
        raise RuntimeError(f"Output directory hierarchy compliance failed before publication: {'; '.join(violations)}")


def build_and_validate_intake_package(
    out_p: Path, artifacts_map: Dict[str, str], meta: Dict[str, Any], source_text: str, src_file_path: Optional[Path]
) -> Path:
    """Prepares and validates sealed intake package for downstream release."""
    intake_pkg_path = out_p / "intake_package.json"
    if intake_pkg_path.exists():
        intake_pkg_path.unlink()

    from dual_pipeline.transcript_pipeline_core import release_runner
    raw_source_path = resolve_raw_source_path(artifacts_map, src_file_path, out_p, source_text)
    release_runner.prepare_package(
        draft_path=Path(artifacts_map["03_graph"]), source_path=raw_source_path, output_path=intake_pkg_path,
        source_location=f"{out_p.name}/{raw_source_path.name}", classification=meta.get("classification", "confidential"),
        audiences=meta.get("audiences", ["internal_consulting", "nee-engineering"]), started_at=meta.get("date"),
        organizer=meta.get("author") or meta.get("organizer"), platform_reference=meta.get("platform") or "Microsoft Teams",
        authority_rank=int(meta.get("authority_rank", 7)),
        inline_canonical_promotion=bool(meta.get("inline_canonical_promotion", True)),
        is_visual_document=bool(meta.get("is_visual_document") or meta.get("allow_visual_image")),
    )
    check_directory_compliance(out_p, meta.get("project"))
    return intake_pkg_path
