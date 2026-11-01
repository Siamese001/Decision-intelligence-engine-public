"""
dual_pipeline/canonical_pipeline/u0_ingress.py

Stage U0 Unified Preflight Ingress & Live Container Auto-Handoff.
Auto-hydrates time, title, speaker_map, and speaker_hints.json from live session
artifacts and executes batched U0/L1/C0.6 preflight validation in a single pass.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .u0_preflight_prompts import build_preflight_speaker_prompts, hydrate_generic_speaker_preflight_prompts
from .u0_preflight_types import (
    PreflightBatchReport,
    PreflightDiagnostic,
    UnifiedPreflightCascadeError,
)
from .u0_time_hydration import (
    _format_iso_window,
    _format_session_tag_time,
    _hydrate_time_from_container,
)
from .u0_speaker_hints import (
    _hint_dedup_key,
    _hydrate_acoustic_receipt,
    _load_hints_from_dirs,
    resolve_pre_hitl_speaker_hints,
)
from .u0_manifest_discovery import (
    _find_manifest_in_dirs,
    _hydrate_container_attachments,
    _needs_manifest_attendee_enrichment,
    _resolve_live_artifact_dirs,
)
from .u0_live_artifacts import (
    _hydrate_speaker_artifacts,
    hydrate_live_container_artifacts,
)
from .u0_ledger_checks import (
    _check_acoustic_unknown_utterances,
    _load_ledger_role_resolutions,
)

logger = logging.getLogger("dual_pipeline.canonical_pipeline.u0_ingress")


def _has_verified_hitl_resolved_transcript(metadata: Dict[str, Any]) -> bool:
    raw_p = str(metadata.get("input_path") or metadata.get("source_path") or "")
    if "hitl_resolved_transcript" not in raw_p:
        return False
    p = Path(raw_p)
    if p.is_file():
        mf_p = p.with_name(f"{p.name}.manifest.json")
        if mf_p.is_file():
            return True
        if (p.parent / "stage_seals.json").is_file() or (p.parent / "final_release_decision.json").is_file():
            return True
    if metadata.get("hitl_role_resolutions") or metadata.get("attendee_roles_confirmed_by_operator"):
        return True
    return False


def _has_authentic_live_container_proof(metadata: Dict[str, Any]) -> bool:
    rec_p = metadata.get("record_start_receipt_path")
    if rec_p and Path(str(rec_p)).is_file():
        return True
    c_dir = metadata.get("container_dir")
    if c_dir:
        cp = Path(str(c_dir))
        if cp.is_dir():
            if (cp / "record_start_receipt.json").is_file() or list(cp.glob("*_manifest.json")):
                return True
            if not metadata.get("strict_u0_provenance"):
                return True
    return False


def _is_notes_or_doc_exempt(metadata: Dict[str, Any]) -> bool:
    return bool(
        metadata.get("notes_mode")
        or metadata.get("is_notes")
        or metadata.get("is_document")
        or metadata.get("output3_only")
        or metadata.get("test_fast")
        or _has_verified_hitl_resolved_transcript(metadata)
        or metadata.get("canonical_modality") in ("MEETING_NOTES", "DOCUMENT")
        or str(metadata.get("source_type") or "").lower() in ("copilot_notes", "notes", "meeting_notes", "document")
    )


def _validate_mandatory_time_with_inline_fallback(
    metadata: Dict[str, Any],
    raw_transcript: str,
    hydrated: List[str],
    missing: List[str],
    diagnostics: List[PreflightDiagnostic],
) -> None:
    is_doc = bool(
        metadata.get("is_document")
        or metadata.get("canonical_modality") == "DOCUMENT"
        or str(metadata.get("source_type") or "").lower() == "document"
    )
    if not metadata.get("time") and not is_doc and raw_transcript:
        import re as _re
        m_inline_time = _re.search(
            r"(?im)^(?:\*\*Time:\*\*|Time:)\s*(\d{1,2}:\d{2}\s*(?:AM|PM)?\s*[-–]\s*\d{1,2}:\d{2}\s*(?:AM|PM)(?:\s*(?:ET|EST|EDT|CT|PT|MT|UTC))?)\b",
            str(raw_transcript)[:2500],
        )
        if m_inline_time:
            metadata["time"] = m_inline_time.group(1).strip()
            if "time" not in hydrated:
                hydrated.append("time")
    if not metadata.get("time") and not is_doc:
        missing.append("time")
        diagnostics.append(
            PreflightDiagnostic(
                check_name="U0_MANDATORY_TIME",
                severity="BLOCKING",
                field_name="time",
                message="Meeting time is missing and could not be discovered from container manifests.",
                remediation="Provide --time 'HH:MM AM - HH:MM PM ET' or ensure live session manifest exists.",
            )
        )


def _has_signed_attendee_receipt(metadata: Dict[str, Any]) -> bool:
    receipt = metadata.get("attendee_confirmation_receipt")
    return (
        isinstance(receipt, dict)
        and receipt.get("status") == "RESOLVED_HITL"
        and bool(receipt.get("subject_sha256"))
        and not str(receipt.get("subject_sha256")).startswith("urn:nee:")
    )


def batch_validate_preflight_checks(
    metadata: Dict[str, Any],
    raw_transcript: str = "",
    input_path: Union[str, Path, None] = None,
) -> PreflightBatchReport:
    """
    Executes a single unified preflight validation pass over U0, L1, and C0.6 requirements.
    Collects ALL missing fields and issues into a combined report.
    """
    hydrated = hydrate_live_container_artifacts(metadata, input_path=input_path)
    missing: List[str] = []
    diagnostics: List[PreflightDiagnostic] = []
    raw_transcript = metadata.get("raw_transcript") or raw_transcript
    _validate_mandatory_time_with_inline_fallback(metadata, raw_transcript, hydrated, missing, diagnostics)

    resolve_pre_hitl_speaker_hints(metadata)

    from dual_pipeline.transcript_pipeline_core.source_modality import (
        _collect_generic_labels,
        _find_dialogue_body,
        _has_unmapped_generic_speakers,
        extract_all_turn_speakers,
    )

    dialogue = _find_dialogue_body(raw_transcript) if raw_transcript else ""
    generic_labels = _collect_generic_labels(dialogue, extract_all_turn_speakers(dialogue)) if dialogue else set()
    if generic_labels:
        spk_map = metadata.get("speaker_map")
        has_hints = bool(metadata.get("turn_hints") or metadata.get("speaker_hints"))
        has_c0_audio = bool(
            (metadata.get("is_audio") or metadata.get("has_acoustic_diarization") or metadata.get("_from_audio_transcription") or metadata.get("audio_sha256"))
            and (metadata.get("attendees") or metadata.get("roster"))
        )
        if not spk_map or _has_unmapped_generic_speakers(dialogue, spk_map):
            if not has_hints and not has_c0_audio:
                missing.append("speaker_map")
                hydrate_generic_speaker_preflight_prompts(metadata, raw_transcript, sorted(generic_labels))
                diagnostics.append(
                    PreflightDiagnostic(
                        check_name="U0_UNMAPPED_SPEAKERS",
                        severity="BLOCKING",
                        field_name="speaker_map",
                        message=f"Transcript contains generic speakers {sorted(generic_labels)} without complete mapping.",
                        remediation="Provide --speaker-map or populate speaker_hints.json in the meeting container.",
                    )
                )
            else:
                logger.info("[U0-PREFLIGHT] Generic speakers %s detected, deferred to C0-PA speaker attribution engine.", sorted(generic_labels))

    _check_acoustic_unknown_utterances(metadata, missing, diagnostics)

    from dual_pipeline.transcript_metadata_core.transcript_placeholder_detector import is_placeholder_role
    from dual_pipeline.transcript_stages_core.c0_gates import _extract_pre_enrichment_roles
    from dual_pipeline.transcript_metadata_core.entity_resolver import resolve_person_entity

    is_notes_exempt = _is_notes_or_doc_exempt(metadata)
    roster = metadata.get("roster") or metadata.get("attendees")
    pre_roles = metadata.get("pre_enrichment_roles")
    if (not pre_roles or not isinstance(pre_roles, dict)) and roster and not is_notes_exempt:
        pre_roles = _extract_pre_enrichment_roles(roster)
    if isinstance(pre_roles, dict) and not is_notes_exempt:
        from dual_pipeline.hitl_governance_core.speaker_resolution_contract import verify_ledger_backed_role_resolutions
        from dual_pipeline.transcript_stages_core.c06_graphdb_role_gate import _sync_hitl_roles_to_roster
        _load_ledger_role_resolutions(metadata)
        verify_ledger_backed_role_resolutions(metadata, repo_root=metadata.get("repo_root"))
        _sync_hitl_roles_to_roster(metadata)
        pre_roles = metadata.get("pre_enrichment_roles") or pre_roles
        resolved_roles = metadata.get("hitl_role_resolutions", {})
        auto_roles = metadata.get("auto_role_resolutions", {})
        unverified_roles = []
        for name, r in pre_roles.items():
            if not name or name in resolved_roles or name in auto_roles:
                continue
            ent = resolve_person_entity(
                name,
                repo_root=metadata.get("repo_root"),
                runtime_root=metadata.get("runtime_root"),
                generation_id=metadata.get("generation_id"),
                as_of_timestamp=metadata.get("date") or metadata.get("as_of_timestamp"),
                explicit_role=str(r) if r and not is_placeholder_role(str(r)) else None,
            )
            if (
                ent and ent.role and not is_placeholder_role(ent.role)
                and not getattr(ent, "requires_operator_confirmation", False)
                and getattr(ent, "status", "") not in ("AMBIGUOUS_CONFLICT", "UNRESOLVED_GRAPH_ATTENDEE")
            ):
                from dual_pipeline.transcript_stages_core.c05_title_gate import apply_hitl_role_resolution
                apply_hitl_role_resolution(metadata, name, ent.role, is_human_confirmed=False, provenance=getattr(ent, "source", None) or "ENTERPRISE_DIRECTORY")
                continue
            unverified_roles.append(name)
        if unverified_roles and not metadata.get("attendee_roles_confirmed_by_operator"):
            missing.append("attendee_roles")
            diagnostics.append(
                PreflightDiagnostic(
                    check_name="C05_TITLE_COMPLETENESS",
                    severity="BLOCKING",
                    field_name="attendee_roles",
                    message=f"Attendees {sorted(unverified_roles)} lack verified roles or titles in GraphDB.",
                    remediation="Confirm attendee roles via enterprise directory or operator resolution.",
                )
            )

    from dual_pipeline.transcript_stages_core.attendee_gate import (
        find_unrecognized_single_token_attendees,
        is_audio_or_raw_source,
    )

    unrec_singles = [] if is_notes_exempt else find_unrecognized_single_token_attendees(metadata, raw_transcript)
    if not is_notes_exempt and (is_audio_or_raw_source(metadata) or unrec_singles) and not metadata.get("attendees_confirmed_by_operator"):
        if is_audio_or_raw_source(metadata) and not unrec_singles and metadata.get("attendees") and _has_signed_attendee_receipt(metadata):
            logger.info("Preflight: Confirming attendees from verified signed operator receipt.")
            metadata["attendees_confirmed_by_operator"] = True
        else:
            missing.append("attendee_confirmation")
            msg = f"Source contains unrecognized single-token attendee(s) {sorted(unrec_singles)} requiring explicit operator confirmation." if unrec_singles and not is_audio_or_raw_source(metadata) else "Audio or raw transcript source requires explicit attendee confirmation."
            diagnostics.append(PreflightDiagnostic(check_name="U0_ATTENDEE_CONFIRMATION", severity="BLOCKING", field_name="attendees_confirmed_by_operator", message=msg, remediation="Confirm attendees via interactive prompt or provide attendee receipt."))

    return PreflightBatchReport(
        is_valid=(len(missing) == 0),
        missing_fields=missing,
        diagnostics=diagnostics,
        hydrated_fields=hydrated,
        review_contexts=list(metadata.get("preflight_speaker_review_prompts") or []),
        speaker_options=list(metadata.get("preflight_speaker_options") or []),
    )


def raise_if_preflight_failed(report: PreflightBatchReport) -> None:
    """Raises UnifiedPreflightCascadeError if any preflight check failed."""
    if not report.is_valid:
        raise UnifiedPreflightCascadeError(report)


__all__ = [
    "PreflightDiagnostic",
    "PreflightBatchReport",
    "UnifiedPreflightCascadeError",
    "hydrate_live_container_artifacts",
    "batch_validate_preflight_checks",
    "raise_if_preflight_failed",
    "_format_session_tag_time",
    "_format_iso_window",
    "_hydrate_time_from_container",
    "_hint_dedup_key",
    "_load_hints_from_dirs",
    "_hydrate_acoustic_receipt",
    "resolve_pre_hitl_speaker_hints",
    "_hydrate_speaker_artifacts",
    "_resolve_live_artifact_dirs",
    "_needs_manifest_attendee_enrichment",
    "_find_manifest_in_dirs",
    "_hydrate_container_attachments",
    "_check_acoustic_unknown_utterances",
    "_load_ledger_role_resolutions",
]
