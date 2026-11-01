"""
dual_pipeline/canonical_pipeline/c0_pa_runner.py

Governed stage runner for Stage C0-PA: Context & Packet Assembly.
Ensures that resolved_transcript from speaker attribution is preserved across
downstream synthesis stages and across --resume checkpoints.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple, Union

from dual_pipeline.canonical_pipeline.operation_contracts import CanonicalModality
from dual_pipeline.unified_agentic_pipeline import PipelineStage
from dual_pipeline.canonical_pipeline.context_binding import BoundedContext
from dual_pipeline.pii_scanner import enforce_pii_clean

logger = logging.getLogger("dual_pipeline.canonical_pipeline.c0_pa_runner")


from .u0_speaker_hitl_gate import compute_speaker_binding_digest, reconciliate_resumed_c0_bindings


def _notify(
    progress_callback: Optional[Callable[[int, int, str], None]],
    current: int,
    stage_name: str,
) -> None:
    if progress_callback:
        progress_callback(current, 8, stage_name)


def _restore_pre_enrichment_roles(meta: Dict[str, Any]) -> None:
    roles = meta.get("pre_enrichment_roles")
    roster = meta.get("roster")
    if not isinstance(roles, dict) or not isinstance(roster, list):
        return
    for item in roster:
        if isinstance(item, dict) and item.get("name") in roles:
            item["role"] = roles[item["name"]]


def _learn_speaker_memory(meta: Dict[str, Any], source_text: str) -> None:
    is_verified = bool(
        meta.get("gate_1_7_passed")
        or meta.get("hitl_verified_speakers")
        or meta.get("verified_speaker_map")
        or meta.get("speaker_attribution_receipt")
        or meta.get("hitl_attribution_receipt")
        or meta.get("persist_speaker_memory") is True
    )
    if not is_verified:
        return
    from dual_pipeline.speaker_memory import get_speaker_memory
    from storage.credentials import CredentialManager
    mem = meta.get("speaker_memory") or get_speaker_memory()
    default_persist = (
        not bool(meta.get("test_fast") or meta.get("dry_run"))
        and (not CredentialManager.has_credential("PYTEST_CURRENT_TEST") or meta.get("speaker_memory") is not None)
    )
    should_persist = bool(meta.get("persist_speaker_memory", default_persist))
    mem.learn_from_resolved_transcript(source_text, speaker_map=meta.get("speaker_map"), persist=should_persist)


def hydrate_resumed_l1_l0_metadata(
    pipeline: Any,
    l1_data: Dict[str, Any],
    meta: Dict[str, Any],
    input_path: Union[str, Path, None],
) -> None:
    """Restores L1/L0 metadata while preserving fresh speaker_map and live container sidecars."""
    l1_meta = l1_data.get("metadata")
    if not isinstance(l1_meta, dict):
        return
    fresh_map = meta.get("speaker_map") if isinstance(meta.get("speaker_map"), dict) else {}
    for k, v in l1_meta.items():
        if k not in meta or meta[k] is None:
            meta[k] = v
    source_map = fresh_map or (l1_meta.get("speaker_map") if isinstance(l1_meta.get("speaker_map"), dict) else None)
    if source_map:
        meta["speaker_map"] = dict(source_map)
    if hasattr(pipeline.u0, "execute"):
        from dual_pipeline.canonical_pipeline.u0_ingress import hydrate_live_container_artifacts
        from dual_pipeline.transcript_stages_core.u0_ingestion import _normalize_roster_entities
        hydrate_live_container_artifacts(meta, input_path=input_path)
        _restore_pre_enrichment_roles(meta)
        _normalize_roster_entities(meta)


def _run_c0_meeting_stages(pipeline: Any, source_text: str, input_hash: str, meta: Dict[str, Any]) -> str:
    diar = meta.get("diarization_status")
    if not diar:
        raise ValueError(
            "Pipeline error: 'diarization_status' must be explicitly set in metadata "
            "('NATIVE', 'DIARIZED_ACOUSTIC_RESOLVED', or 'UNDIARIZED'). Cannot default to NATIVE."
        )
    ctx = {"transcript": source_text, "hash": input_hash, "metadata": meta, "diarization_status": diar}
    (
        pipeline.c0_native
        if diar in ("NATIVE", "DIARIZED_ACOUSTIC_RESOLVED", "VERIFIED_STEREO_SEPARATION")
        else pipeline.c0_undiarized
    ).execute(ctx)

    from dual_pipeline.transcript_stages_core.output01_parser import is_completed_output_01_path, is_completed_output_01_text
    sm = meta.get("state_machine")
    is_out1_frozen = (
        bool(meta.get("freeze_output_1"))
        or (sm is not None and hasattr(sm, "is_frozen") and sm.is_frozen("output_1"))
        or is_completed_output_01_path(meta.get("input_path"))
        or is_completed_output_01_text(source_text)
    )
    if not is_out1_frozen:
        from dual_pipeline.speaker_attribution_core import AudioSpeakerAttributionEngine
        attribution_engine = AudioSpeakerAttributionEngine(decision_store=meta.get("hitl_decision_store"))
        attribution_res = attribution_engine.analyze_and_resolve(
            transcript=source_text, metadata=meta, interactive_resolver=meta.get("interactive_resolver"),
            autonomous_if_decisive=bool(meta.get("autonomous_attribution", True)),
        )
        if attribution_res.speaker_map:
            meta["speaker_map"] = attribution_res.speaker_map
            from dual_pipeline.transcript_pipeline_core.context_builder import _sync_attributed_speakers_to_roster
            _sync_attributed_speakers_to_roster(meta)
        if getattr(attribution_res, "resolved_transcript", None):
            source_text = attribution_res.resolved_transcript
            meta["resolved_transcript"] = source_text
            _learn_speaker_memory(meta, source_text)
    else:
        meta["resolved_transcript"] = source_text

    c0_context = {"metadata": meta, "transcript": source_text, "transcript_text": source_text, "hash": input_hash}
    pipeline.c05_title.execute(c0_context)
    pipeline.c06_role.execute(c0_context)
    from dual_pipeline.canonical_pipeline.u0_speaker_hitl_gate import enforce_pre_l2_speaker_hitl_gate
    enforce_pre_l2_speaker_hitl_gate(
        transcript=source_text, metadata=meta, interactive_resolver=meta.get("interactive_resolver"),
    )
    from dual_pipeline.transcript_pipeline_core.context_builder import _refresh_graph_prior_context_after_c0
    _refresh_graph_prior_context_after_c0(meta, source_text)
    return source_text


def execute_c0_pa(
    pipeline: Any,
    modality: CanonicalModality,
    source_text: str,
    input_hash: str,
    meta: Dict[str, Any],
    presentation_path: Optional[Union[str, Path]],
    out_p: Path,
    tracker: Any,
) -> Tuple[Dict[str, Any], str]:
    """
    Executes Stage C0-PA and strictly preserves resolved_transcript for downstream synthesis.
    """
    tracker.start_stage(PipelineStage.C0_PA_CONTEXT)
    fail_closed_pii = bool(meta.get("enforce_pii_fail_closed", False))
    source_text = enforce_pii_clean(source_text, fail_closed=fail_closed_pii)

    if "bounded_context" not in meta:
        scope = meta.get("project") or meta.get("tenant") or "GLOBAL"
        meta["bounded_context"] = BoundedContext(
            authorized_scope=scope,
            source_ids=[meta.get("file_hash") or ""],
            bounds={"max_tokens": 8000, "confidentiality": "STRICT"},
            availability_status="AVAILABLE",
        )

    if modality in (
        CanonicalModality.AUDIO,
        CanonicalModality.MEETING_TRANSCRIPT,
        CanonicalModality.MEETING_NOTES,
    ):
        from dual_pipeline.transcript_pipeline_core.context_builder import load_prior_meeting_context
        load_prior_meeting_context(str(out_p), meta, transcript_text=source_text)
        if modality != CanonicalModality.MEETING_NOTES and not meta.get("notes_mode"):
            source_text = _run_c0_meeting_stages(pipeline, source_text, input_hash, meta)

    if modality == CanonicalModality.DOCUMENT or meta.get("is_document") or meta.get("output3_only") or meta.get("source_type") in ("document", "visual_document"):
        meta["archetype"] = "DOCUMENT_MODE"
        meta["meeting_type"] = "DOCUMENT_MODE"
        meta["meeting_type_hint"] = "DOCUMENT_MODE"
        from dual_pipeline.document_pipeline_core.ingestion_planner import (
            build_document_ingestion_plan,
            persist_document_ingestion_plan,
        )
        doc_plan = build_document_ingestion_plan(source_text, meta)
        meta["document_ingestion_plan"] = doc_plan
        meta["document_archetype"] = doc_plan.get("document_archetype")
        slug = meta.get("filename_slug") or meta.get("meeting_name") or (Path(str(presentation_path)).stem if presentation_path else "document")
        plan_path = persist_document_ingestion_plan(doc_plan, out_p, slug=str(slug))
        meta["document_ingestion_plan_path"] = str(plan_path)
    else:
        if not meta.get("archetype"):
            if meta.get("meeting_type"):
                from dual_pipeline.prompts.contracts.output_contract import MeetingArchetype
                meta["archetype"] = MeetingArchetype.from_string(meta["meeting_type"]).value
            else:
                from dual_pipeline.prompts.contracts.output_contract import OutputContract
                meta["archetype"] = OutputContract.detect_archetype(title=meta.get("meeting_name") or "", text=source_text or "").value
        meta["meeting_type"] = meta.get("meeting_type") or meta.get("archetype")
        meta["meeting_type_hint"] = meta.get("archetype")

    if not meta.get("speaker_role_resolutions"):
        from dual_pipeline.canonical_pipeline.u0_ingress import _load_ledger_role_resolutions
        _load_ledger_role_resolutions(meta)

    meta["binding_sha256"] = compute_speaker_binding_digest(meta)
    clean_meta = {
        k: v for k, v in meta.items()
        if k not in ("source_text", "raw_transcript")
        and not (isinstance(v, str) and v.startswith("<dual_pipeline"))
    }
    _ensure_lens_entity_clarifications(meta, clean_meta)
    pa_context = {
        "metadata": clean_meta,
        "presentation_path": str(presentation_path) if presentation_path else None,
        "output_dir": str(out_p),
    }
    if hasattr(pipeline, "pa") and pipeline.pa is not None and hasattr(pipeline.pa, "execute"):
        try:
            pa_stage_res = pipeline.pa.execute(
                {"transcript": source_text, "hash": input_hash, "metadata": clean_meta, "statements": meta.get("statements", [])},
                clean_meta,
            )
            if isinstance(pa_stage_res, dict):
                for k, v in pa_stage_res.items():
                    if k != "metadata":
                        pa_context.setdefault(k, v)
        except Exception as pa_exc:
            logger.debug("Optional PA stage execution skipped: %s", pa_exc)
    tracker.complete_stage(
        PipelineStage.C0_PA_CONTEXT, status="PASS", details={"keys": list(pa_context.keys())}
    )
    return pa_context, source_text


def _ensure_lens_entity_clarifications(meta: Dict[str, Any], pa_meta: Optional[Dict[str, Any]] = None) -> None:
    proj = str(meta.get("project") or (pa_meta or {}).get("project") or "").lower()
    if "lens" not in proj:
        return
    default_clarifications = [
        {"token": "tony", "canonical_name": "Anthony (Tony) K.M.", "rationale": "Canonical GraphDB Grounding (Rule C-16)"},
        {"token": "anthony", "canonical_name": "Anthony (Tony) K.M.", "rationale": "Canonical GraphDB Grounding (Rule C-16)"},
        {"token": "jason", "canonical_name": "Jason Helton", "rationale": "Canonical GraphDB Grounding (Rule C-16)"},
        {"token": "alex", "canonical_name": "Alex Sanchez", "rationale": "Canonical GraphDB Grounding (Rule C-16)"},
        {"token": "richard", "canonical_name": "Richard", "rationale": "Canonical GraphDB Grounding (Rule C-16)"},
        {"token": "jen", "canonical_name": "Jen Pan", "rationale": "Canonical GraphDB Grounding (Rule C-16)"},
        {"token": "ed", "canonical_name": "Eduardo (Ed) Juan More", "rationale": "Canonical GraphDB Grounding (Rule C-16)"},
        {"token": "eduardo", "canonical_name": "Eduardo (Ed) Juan More", "rationale": "Canonical GraphDB Grounding (Rule C-16)"},
        {"token": "jake", "canonical_name": "Jake Stark", "rationale": "Canonical GraphDB Grounding (Rule C-16)"},
        {"token": "miles", "canonical_name": "Miles", "rationale": "Canonical GraphDB Grounding (Rule C-16)"},
        {"token": "brett", "canonical_name": "Brett Gavagni", "rationale": "Canonical GraphDB Grounding (Rule C-16)"},
        {"token": "daniel", "canonical_name": "Daniel Sabet", "rationale": "Canonical GraphDB Grounding (Rule C-16)"},
        {"token": "mandy", "canonical_name": "harper Hoffman", "rationale": "Canonical GraphDB Grounding (Rule C-16)"},
        {"token": "amanda", "canonical_name": "harper Hoffman", "rationale": "Canonical GraphDB Grounding (Rule C-16)"},
        {"token": "karen", "canonical_name": "Morgan Vance Krutilla", "rationale": "Canonical GraphDB Grounding (Rule C-16)"},
        {"token": "karyn", "canonical_name": "Morgan Vance Krutilla", "rationale": "Canonical GraphDB Grounding (Rule C-16)"},
    ]
    existing = list(meta.get("entity_clarifications") or (pa_meta or {}).get("entity_clarifications") or [])
    seen = {str(c.get("token", "")).lower() for c in existing if isinstance(c, dict)}
    for item in default_clarifications:
        if item["token"] not in seen:
            existing.append(item)
            seen.add(item["token"])
    meta["entity_clarifications"] = existing
    if isinstance(pa_meta, dict):
        pa_meta["entity_clarifications"] = existing


def step_c0_pa_with_resume(
    pipeline: Any,
    checkpoint: Any,
    should_resume: bool,
    modality: CanonicalModality,
    source_text: str,
    input_hash: str,
    meta: Dict[str, Any],
    presentation_path: Optional[Union[str, Path]],
    out_p: Path,
    tracker: Any,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> Tuple[Dict[str, Any], str]:
    """
    Executes or resumes Stage C0-PA, restoring resolved_transcript losslessly without re-running models.
    """
    if should_resume and not meta.get("force_rerun") and checkpoint.has_stage("C0_PA"):
        c0_data = checkpoint.get_stage("C0_PA") or {}
        pa_context = c0_data.get("pa_context")
        if pa_context is None:
            raise ValueError(
                "Fail-Fast: Resumed C0_PA checkpoint is incomplete (missing pa_context)."
            )
        # Restore resolved transcript from checkpoint
        source_text = c0_data.get("resolved_transcript") or c0_data.get(
            "source_text", source_text
        )
        if "resolved_transcript" in c0_data:
            meta["resolved_transcript"] = c0_data["resolved_transcript"]
        source_text = reconciliate_resumed_c0_bindings(c0_data, meta, pa_context, source_text, checkpoint)

        pa_meta = pa_context.get("metadata") if isinstance(pa_context, dict) else None
        if isinstance(pa_meta, dict):
            for sync_k in ("attendees", "roster", "identity_corrections", "diarization_status"):
                if sync_k in pa_meta and pa_meta[sync_k] is not None:
                    if sync_k == "attendees" and meta.get("attendees"):
                        pa_meta["attendees"] = meta["attendees"]
                    else:
                        meta[sync_k] = pa_meta[sync_k]
            for override_k in (
                "entity_resolutions", "entity_clarifications", "attachment_prompt_block",
                "attachments", "attachment_alignment", "archetype", "meeting_type",
                "meeting_archetype", "audio_certificate", "qa_report", "upstream_qa_report",
            ):
                if meta.get(override_k):
                    pa_meta[override_k] = meta[override_k]
                elif pa_meta.get(override_k):
                    meta[override_k] = pa_meta[override_k]
        _ensure_lens_entity_clarifications(meta, pa_meta if isinstance(pa_meta, dict) else None)

        tracker.start_stage(PipelineStage.C0_PA_CONTEXT)
        tracker.complete_stage(
            PipelineStage.C0_PA_CONTEXT, status="PASS", details={"resumed": True}
        )
        return pa_context, source_text

    _notify(progress_callback, 4, "Stage C0-PA: Context & Packet Assembly")
    pa_context, source_text = execute_c0_pa(
        pipeline=pipeline,
        modality=modality,
        source_text=source_text,
        input_hash=input_hash,
        meta=meta,
        presentation_path=presentation_path,
        out_p=out_p,
        tracker=tracker,
    )
    checkpoint.save_stage(
        "C0_PA",
        {
            "pa_context": pa_context,
            "source_text": source_text,
            "resolved_transcript": source_text,
            "speaker_map": meta.get("speaker_map", {}),
            "speaker_hints": meta.get("speaker_hints", []),
            "voiceprint_matches": meta.get("voiceprint_matches", {}),
            "binding_sha256": meta.get("binding_sha256") or compute_speaker_binding_digest(meta),
        },
    )
    return pa_context, source_text
