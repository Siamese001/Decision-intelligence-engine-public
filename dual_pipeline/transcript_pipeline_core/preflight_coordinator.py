"""Pre-Flight Coordinator for Stage U0 Ingress (output dir, attachments, deconfliction)."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from config.credentials import CredentialManager
from dual_pipeline.attachment_core import (
    align_attachments_with_transcript, build_attachment_prompt_section, discover_attachments,
    extract_attachment_content, inject_attachment_annotations, serialize_attachment_records_with_content, stage_attachments_into_output,
)
from dual_pipeline.meeting_deconfliction import MeetingCollisionDetector, MeetingDeconflictionError
from dual_pipeline.meeting_paths import MeetingPathEnvelope, normalize_meeting_date
from dual_pipeline.query_router_core.project_router import determine_project_directory
from dual_pipeline.transcript_duration_helper import resolve_meeting_duration

logger = logging.getLogger(__name__)


def _resolve_duration_if_needed(metadata: Dict[str, Any], transcript_text: str) -> None:
    dur = resolve_meeting_duration(metadata, transcript_text)
    if dur:
        metadata["duration_minutes"] = metadata["meeting_duration_minutes"] = dur
        if metadata.get("time") and "Duration:" not in str(metadata["time"]):
            metadata["time"] = f"{metadata['time']} (Duration: ~{dur}m)"


def _resolve_auto_output_dir(
    metadata: Dict[str, Any],
    cand_project: Optional[str],
    raw_date: Optional[str],
    meeting_name: str,
    time_str: Optional[str],
    std_slug: str,
) -> str:
    if not cand_project or cand_project.upper() == "UNRESOLVED":
        from dual_pipeline.exceptions import ProjectResolutionRequiredError
        raise ProjectResolutionRequiredError(
            f"Target project could not be resolved for meeting '{meeting_name}'. "
            "Specify --project explicitly."
        )
    subfolder = metadata.get("subfolder") or metadata.get("use_meeting_subfolder")
    if subfolder is None and not metadata.get("no_subfolder", False):
        subfolder = std_slug
    env = MeetingPathEnvelope.resolve(
        project_name=cand_project,
        meeting_date=raw_date,
        meeting_name=meeting_name,
        meeting_time=time_str,
        subfolder=subfolder,
        attendees=metadata.get("attendees"),
        standardize_name=True,
        category=metadata.get("category"),
        short_deliverables=metadata.get("short_deliverables", False),
    )
    metadata["date_filename"] = env.date_dirname
    metadata["date"] = env.date_display
    return str(env.output_dir)


def _validate_project_alias(proj_candidate: str) -> str:
    from dual_pipeline.parameters import get_known_projects, resolve_project_name
    known = get_known_projects()
    resolved = resolve_project_name(proj_candidate)
    is_test_env = "test" in resolved.lower()
    if resolved not in known and not is_test_env:
        raise ValueError(f"Unknown project identifier '{resolved}'. Must be one of: {sorted(known)}")
    return resolved


def _extract_date_candidate(
    out_p: Path, raw_date: Optional[str], metadata: Dict[str, Any]
) -> Tuple[str, str]:
    date_candidate = raw_date
    date_regex = r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])-(19|20)\d{2}$"
    if not date_candidate:
        if out_p.name and re.match(date_regex, out_p.name):
            date_candidate = out_p.name
        elif out_p.parent.name and re.match(date_regex, out_p.parent.name):
            date_candidate = out_p.parent.name
            metadata.setdefault("subfolder", out_p.name)

    try:
        norm_date_dir, norm_date_disp = normalize_meeting_date(date_candidate)
    except ValueError:
        norm_date_dir, norm_date_disp = normalize_meeting_date(None)
    metadata["date_filename"] = norm_date_dir
    metadata["date"] = norm_date_disp
    return norm_date_dir, norm_date_disp


def _resolve_explicit_output_dir(
    metadata: Dict[str, Any],
    output_dir: str,
    raw_date: Optional[str],
    std_slug: str,
) -> str:
    out_p = Path(output_dir)
    proj_candidate = metadata.get("project")
    if not proj_candidate:
        for idx, part in enumerate(out_p.parts[:-1]):
            if part == "output" and idx + 1 < len(out_p.parts):
                proj_candidate = out_p.parts[idx + 1]
                break
    if proj_candidate:
        metadata["project"] = _validate_project_alias(proj_candidate)

    _extract_date_candidate(out_p, raw_date, metadata)

    date_regex = r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])-(19|20)\d{2}$"
    if re.match(date_regex, out_p.name) and not metadata.get("no_subfolder", False):
        out_p = out_p / std_slug
        metadata.setdefault("subfolder", std_slug)

    return str(out_p)


def resolve_output_directory_and_slug(
    metadata: Dict[str, Any],
    transcript_text: str,
    output_dir: str = "auto",
    ingestion_context: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """
    Standardizes meeting slug and resolves governed output directory.
    Returns: (output_dir, std_slug)
    """
    raw_date = (
        metadata.get("date")
        or metadata.get("date_filename")
        or (ingestion_context.get("date") if ingestion_context else None)
    )
    meeting_name = (
        metadata.get("meeting_name")
        or (ingestion_context.get("meeting_name") if ingestion_context else None)
        or "Meeting"
    )
    time_str = metadata.get("time_filename") or metadata.get("time")
    _resolve_duration_if_needed(metadata, transcript_text)

    cand_project = metadata.get("project")
    if not cand_project and output_dir.lower() == "auto":
        snippet = transcript_text[:2000] if transcript_text else ""
        cand_project = determine_project_directory(metadata, snippet)
    if cand_project:
        metadata["project"] = cand_project

    from dual_pipeline.naming import resolve_container_or_slug
    input_cand = (
        metadata.get("input_path")
        or (ingestion_context.get("input_path") if ingestion_context else None)
        or metadata.get("container_dir")
    )
    container_dir, std_slug = resolve_container_or_slug(
        input_cand, meeting_name, metadata.get("attendees"), cand_project
    )
    metadata["standardized_slug"] = std_slug
    if container_dir is not None:
        metadata["subfolder"] = std_slug
        if output_dir.lower() == "auto":
            resolved_dir = str(container_dir)
            _extract_date_candidate(container_dir, raw_date, metadata)
            logger.info("Pre-Flight: Reusing live recording container directory -> %s", resolved_dir)
        else:
            resolved_dir = _resolve_explicit_output_dir(metadata, output_dir, raw_date, std_slug)
    elif output_dir.lower() == "auto":
        resolved_dir = _resolve_auto_output_dir(metadata, cand_project, raw_date, meeting_name, time_str, std_slug)
    else:
        resolved_dir = _resolve_explicit_output_dir(metadata, output_dir, raw_date, std_slug)

    repo_root = Path(__file__).resolve().parents[2]
    verify_meeting_deconfliction(
        metadata=metadata, transcript_text=metadata.get("transcript_text", ""),
        output_dir=resolved_dir, repo_root=repo_root,
    )

    metadata["enforce_worktree"] = True
    from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
        ensure_live_transcription_worktree,
        is_inside_isolated_worktree,
    )
    resolved_p = Path(resolved_dir).resolve()
    try:
        in_repo = resolved_p.is_relative_to((repo_root / "output").resolve())
    except Exception:
        in_repo = False

    if (in_repo or output_dir.lower() == "auto") and not is_inside_isolated_worktree(resolved_p):
        eff_proj = cand_project or metadata.get("project")
        if not eff_proj or eff_proj.lower() == "auto":
            from dual_pipeline.exceptions import ProjectResolutionRequiredError
            raise ProjectResolutionRequiredError(
                f"Target project could not be resolved for meeting '{meeting_name}'. "
                "Specify --project explicitly."
            )
        wt_dir, wt_branch = ensure_live_transcription_worktree(
            project_name=eff_proj,
            meeting_name=meeting_name,
            date_dirname=metadata.get("date_filename", "01-01-2026"),
            clean_slug=std_slug,
            repo_root=repo_root,
        )
        sub_path = (
            resolved_p.relative_to((repo_root / "output").resolve())
            if in_repo
            else Path(eff_proj) / metadata.get("date_filename", "01-01-2026") / std_slug
        )
        resolved_dir = str(wt_dir / "output" / sub_path)
        metadata["worktree_dir"] = str(wt_dir)
        metadata["worktree_branch"] = wt_branch
        logger.info(f"Pre-Flight: Enforced worktree isolation -> {resolved_dir} (branch: {wt_branch})")

    logger.info(f"Pre-Flight: resolved governed output directory: {resolved_dir}")
    return resolved_dir, std_slug


def _stage_candidate_attachments(
    candidate_paths: List[Path], output_dir: str, metadata: Optional[Dict[str, Any]] = None,
) -> List[Any]:
    if not candidate_paths:
        return []
    target_out = output_dir if (output_dir and str(output_dir).lower() != "auto") else ((metadata or {}).get("output_dir") or (metadata or {}).get("container_dir"))
    if target_out:
        return stage_attachments_into_output(candidate_paths, target_out)

    import mimetypes
    from dual_pipeline.attachment_core.detector import classify_attachment_type, compute_sha256
    from dual_pipeline.attachment_core.types import AttachmentRecord
    return [
        AttachmentRecord(
            attachment_id=f"att-{compute_sha256(cp)[:12]}", filename=cp.name, original_path=cp, staged_path=cp,
            attachment_type=classify_attachment_type(cp.name), sha256=compute_sha256(cp),
            mime_type=mimetypes.guess_type(cp.name)[0] or "application/octet-stream", file_size_bytes=cp.stat().st_size,
            relative_staged_path=cp.name, attribution_mode="BACKGROUND",
        ) for cp in candidate_paths
    ]


def _align_and_annotate_attachments(
    transcript_text: str,
    discovered_records: List[Any],
    extracted_contents_map: Dict[str, Any],
    metadata: Dict[str, Any],
) -> str:
    if not discovered_records:
        return transcript_text

    from dual_pipeline.attachment_core.types import AttachmentType

    if transcript_text:
        alignment = align_attachments_with_transcript(
            transcript_text,
            discovered_records,
            extracted_contents=extracted_contents_map,
        )
        from dual_pipeline.attachment_core.review_provenance import has_authenticated_mention
        for rec in discovered_records:
            if rec.attribution_mode == "BACKGROUND" and has_authenticated_mention(
                rec.attachment_id, rec.filename, alignment
            ):
                rec.attribution_mode = "COLLATERAL_REVIEWED"

        if any(
            (getattr(c, "has_diagrams", False) and rec.attribution_mode == "COLLATERAL_REVIEWED")
            or getattr(c, "attachment_type", None) == AttachmentType.DIAGRAM
            for rec in discovered_records
            for c in [extracted_contents_map.get(rec.attachment_id)] if c is not None
        ):
            metadata["has_architecture_diagram"] = True

        updated_transcript = inject_attachment_annotations(transcript_text, alignment)
        metadata["attachment_alignment"] = alignment.to_dict()
    else:
        alignment = None
        updated_transcript = transcript_text

    att_prompt_block = build_attachment_prompt_section(
        discovered_records, extracted_contents=extracted_contents_map, alignment=alignment,
    )
    if att_prompt_block:
        metadata["attachment_prompt_block"] = att_prompt_block
    metadata["attachments"] = serialize_attachment_records_with_content(discovered_records, extracted_contents_map)
    return updated_transcript


def ingest_and_align_attachments(
    metadata: Dict[str, Any],
    transcript_text: str,
    output_dir: str,
    attachments: Optional[Union[str, Path, List[Union[str, Path]]]] = None,
    attachment_dir: Optional[Union[str, Path]] = None,
    input_path: Optional[str] = None,
) -> str:
    """Discovers, stages, extracts, and aligns multimodal attachments with transcript."""
    cli_attachments = attachments or metadata.get("attachments") or metadata.get("attachment")
    if isinstance(cli_attachments, (str, Path)):
        cli_attachments = [cli_attachments]
    cli_att_dir = attachment_dir or metadata.get("attachment_dir")
    eff_out = output_dir if (output_dir and str(output_dir).lower() != "auto") else (metadata.get("output_dir") or metadata.get("container_dir"))

    candidate_paths = discover_attachments(
        attachment_paths=cli_attachments, attachment_dir=cli_att_dir,
        meeting_output_dir=eff_out, transcript_path=input_path if (input_path and Path(input_path).is_file()) else None,
    )
    discovered_records = _stage_candidate_attachments(candidate_paths, str(eff_out) if eff_out else output_dir, metadata)

    cached_by_sha = {a["sha256"]: a for a in (metadata.get("attachments") or []) if isinstance(a, dict) and a.get("sha256")}
    cached_by_fn = {a["filename"]: a for a in (metadata.get("attachments") or []) if isinstance(a, dict) and a.get("filename")}

    from dual_pipeline.attachment_core.types import ExtractedAttachmentContent
    extracted_contents_map: Dict[str, Any] = {}
    for rec in discovered_records:
        c_val = cached_by_sha.get(rec.sha256) or cached_by_fn.get(rec.filename)
        if c_val and (c_val.get("components") or c_val.get("claims") or c_val.get("extracted_text") or c_val.get("text")):
            comps = c_val.get("components") or []
            extracted_contents_map[rec.attachment_id] = ExtractedAttachmentContent(
                attachment_id=rec.attachment_id, filename=rec.filename, attachment_type=rec.attachment_type,
                sha256=rec.sha256, title=c_val.get("title") or rec.filename,
                extracted_text=c_val.get("extracted_text") or c_val.get("text", ""), summary=c_val.get("summary") or f"Visual image '{rec.filename}'.",
                components=comps, connections=c_val.get("connections") or [], key_topics=c_val.get("key_topics") or c_val.get("topics") or [],
                tables=c_val.get("tables") or [], claims=c_val.get("claims") or [], milestones=c_val.get("milestones") or [],
                has_diagrams=bool(c_val.get("has_diagrams", bool(comps))), raw_payload=c_val.get("raw_payload") or {},
                extraction_status="SUCCESS", is_demo_or_sample=c_val.get("is_demo_or_sample", False),
            )
        else:
            try:
                extracted_contents_map[rec.attachment_id] = extract_attachment_content(rec)
            except Exception as e:
                logger.warning(f"Failed to extract attachment content for {rec.original_path}: {e}")

    return _align_and_annotate_attachments(
        transcript_text, discovered_records, extracted_contents_map, metadata
    )


def _resolve_collision_project(output_dir: str, metadata: Dict[str, Any]) -> str:
    cand_project = metadata.get("project")
    if cand_project:
        return cand_project
    try:
        p_parts = Path(output_dir).resolve().parts
        if "output" in p_parts:
            out_idx = p_parts.index("output")
            if len(p_parts) > out_idx + 1:
                return p_parts[out_idx + 1]
        return Path(output_dir).parents[1].name
    except Exception:
        return ""


def verify_meeting_deconfliction(
    metadata: Dict[str, Any],
    transcript_text: str,
    output_dir: str,
    repo_root: Optional[Any] = None,
) -> None:
    """Evaluates meeting deconfliction and deduplication gate."""
    allow_dup = metadata.get("allow_duplicate", False)
    is_aug = metadata.get("augmentation_mode", False)

    if allow_dup:
        logger.warning(
            f"[DUPLICATE_MEETING_OVERRIDE] Ingesting candidate meeting with --allow-duplicate. "
            f"Rationale: {metadata.get('collision_override_rationale', 'No rationale provided')}"
        )
        return

    if is_aug:
        return

    detector = MeetingCollisionDetector(repo_root=repo_root)
    cand_project = _resolve_collision_project(output_dir, metadata)
    cand_date = metadata.get("date") or metadata.get("date_filename")
    cand_slug = metadata.get("standardized_slug") or Path(output_dir).name
    cand_time = metadata.get("time")
    cand_attendees = metadata.get("attendees")

    collision_res = detector.check_candidate_collision(
        project=cand_project,
        date_str=cand_date,
        candidate_slug=cand_slug,
        candidate_time=cand_time,
        candidate_attendees=cand_attendees,
        candidate_text=transcript_text[:15000] if transcript_text else None,
    )
    if collision_res.is_collision:
        logger.error(
            f"[DUPLICATE_MEETING_COLLISION] Candidate meeting '{cand_slug}' on {cand_date} "
            f"collides with existing sibling '{collision_res.existing_slug}' "
            f"(confidence={collision_res.confidence * 100:.1f}%)."
        )
        raise MeetingDeconflictionError(collision_res)
