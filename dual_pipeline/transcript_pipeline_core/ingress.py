"""
dual_pipeline/transcript_pipeline_core/ingress.py

Dedicated Stage U0 Ingress Processor.
Encapsulates audio transcription caching, docx text extraction, Teams normalization,
presentation alignment, and metadata auto-scaffolding for Stage U0.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

from dual_pipeline.audio_ingestion import get_default_transcriber
from dual_pipeline.transcript_pipeline_core.caching import (
    cache_audio_transcript,
    check_idempotent_meeting_run,
    compute_source_file_hash,
    get_cached_audio_transcript,
)
from dual_pipeline.transcript_pipeline_core.constants import AUDIO_EXTENSIONS, TEXT_EXTENSIONS
from dual_pipeline.transcript_pipeline_core.normalizer import (
    _extract_docx_text,
    normalize_raw_input_text,
)
from dual_pipeline.transcript_pipeline_core.recorder import PipelineSpanRecorder
from dual_pipeline.transcript_pipeline_core.source_modality import (
    SourceModality,
    classify_source_modality,
)

logger = logging.getLogger("dual_pipeline.transcript_pipeline_core.ingress")


@dataclass
class U0IngressResult:
    """Standardized output payload from Stage U0 Ingress processing."""

    transcript_text: str
    metadata: Dict[str, Any]
    input_hash: str
    source_file_hash: Optional[str] = None
    is_file: bool = False
    input_ext: str = ""
    pres_file: Optional[str] = None
    fast_idempotent: Optional[Dict[str, Any]] = None


def _apply_metadata_sidecar_dict(m_data: Any, meta: Dict[str, Any]) -> None:
    if not isinstance(m_data, dict):
        return
    for k in ("speaker_map", "diarization_status", "cluster_label_map", "acoustic_speaker_map"):
        if m_data.get(k) and not meta.get(k):
            meta[k] = m_data[k]


def _hydrate_metadata_sidecars(cf: Path, c_dir: Path, p: Path, meta: Dict[str, Any]) -> None:
    meta_sidecars = (
        cf.with_name(cf.name.replace(".segments.json", "_metadata.json")),
        c_dir / f"{p.stem}_metadata.json",
        p.parent / f"{p.stem}_metadata.json",
    )
    for mf in meta_sidecars:
        if mf.is_file():
            with contextlib.suppress(Exception):
                _apply_metadata_sidecar_dict(json.loads(mf.read_text(encoding="utf-8")), meta)
            break


def _is_skipped_live_preview(seal_info: Dict[str, Any], meta: Dict[str, Any], cf_name: str) -> bool:
    if seal_info.get("cache_tier") == "LIVE_PREVIEW" and not meta.get("allow_live_preview_cache"):
        logger.info(
            "[U0-FAST-PATH] Skipping LIVE_PREVIEW segments cache %s; authoritative master ASR/diarization required.",
            cf_name,
        )
        return True
    return False


def transcribe_audio_with_cache(
    audio_path: str,
    metadata: Dict[str, Any],
    recorder: PipelineSpanRecorder,
    mock: bool = False,
) -> str:
    """Run on-premise audio transcription with content-addressed caching."""
    audio_p = Path(audio_path)
    audio_mtime = audio_p.stat().st_mtime if audio_p.exists() else 0

    def _hydrate_segments_from_cache(a_path: Union[str, Path], meta: Dict[str, Any]) -> None:
        if meta.get("structured_segments"):
            return
        p = Path(a_path)
        c_dir = Path(meta.get("transcript_cache_dir") or "output/transcript_cache")
        sha = compute_source_file_hash(p)
        cand_files: list[Path] = []
        if sha:
            cand_files.extend(list(c_dir.glob(f"*{sha[:12]}*.segments.json")))
            cand_files.append(c_dir / f"{sha}.segments.json")
        cand_files.extend(list(c_dir.glob(f"*{p.stem}*.segments.json")))
        cand_files.extend(list(p.parent.glob("*.segments.json")))

        from dual_pipeline.exceptions import CacheIntegrityViolationError
        from dual_pipeline.transcript_pipeline_core.cache_seal import verify_cache_seal

        seen_paths: set[Path] = set()
        for cf in cand_files:
            if not cf.is_file() or cf in seen_paths:
                continue
            seen_paths.add(cf)
            try:
                seal_info = verify_cache_seal(structured_file=cf)
                if _is_skipped_live_preview(seal_info, meta, cf.name):
                    continue
                loaded = json.loads(cf.read_text(encoding="utf-8"))
                if loaded and isinstance(loaded, list):
                    meta["structured_segments"] = loaded
                    meta["structured_segments_path"] = str(cf)
                    if seal_info.get("sealed"):
                        meta["cache_seal_verified"] = True
                    _hydrate_metadata_sidecars(cf, c_dir, p, meta)
                    break
            except CacheIntegrityViolationError as cive:
                logger.error("Tampered or invalid transcript cache detected: %s", cive)
                raise
            except Exception as e:
                logger.debug("Failed candidate segment parse %s: %s", cf, e)

    pre_transcript = metadata.get("transcript_cache_path") or metadata.get("raw_transcript_path")
    if pre_transcript and os.path.isfile(str(pre_transcript)):
        pre_path = Path(pre_transcript)
        if audio_mtime > 0 and audio_mtime > pre_path.stat().st_mtime:
            logger.warning(f"Rejecting stale pre-computed transcript {pre_path} (audio file is newer).")
        else:
            content = pre_path.read_text(encoding="utf-8").strip()
            if len(content) > 50:
                logger.info(f"Using pre-computed transcript from {pre_transcript} ({len(content)} chars)")
                _hydrate_segments_from_cache(audio_path, metadata)
                return content

    cached = None if (metadata.get("force_rerun") and not metadata.get("resume")) else get_cached_audio_transcript(
        audio_path,
        audio_sha=None, # Force hash recomputation to avoid stale cache from metadata
        cache_dir=metadata.get("transcript_cache_dir"),
    )
    if cached:
        _hydrate_segments_from_cache(audio_path, metadata)
        return cached

    import inspect
    import sys
    pipeline_mod = sys.modules.get("dual_pipeline.transcript_pipeline_core.pipeline")
    pipeline_transcriber = getattr(pipeline_mod, "get_default_transcriber", None)
    transcriber_fn = pipeline_transcriber if (hasattr(pipeline_transcriber, "assert_called") or hasattr(pipeline_transcriber, "return_value")) else get_default_transcriber
    req_backend = str(metadata.get("backend") or "auto")
    req_whisper_model = metadata.get("whisper_model")
    try:
        sig = inspect.signature(transcriber_fn)
        call_kwargs: Dict[str, Any] = {"mock": mock}
        if "backend" in sig.parameters:
            call_kwargs["backend"] = req_backend
        if "model_name" in sig.parameters and req_whisper_model:
            call_kwargs["model_name"] = str(req_whisper_model)
        transcriber = transcriber_fn(**call_kwargs)
    except (ValueError, TypeError):
        transcriber = transcriber_fn(mock=mock)
    try:
        with recorder.span("nee.uc1.u-1.audio_transcribe"):
            text = transcriber.transcribe(audio_path, metadata)
            cache_audio_transcript(
                audio_path,
                text,
                audio_sha=metadata.get("source_file_hash"),
                cache_dir=metadata.get("transcript_cache_dir"),
            )
            if metadata.get("structured_segments"):
                c_dir = Path(metadata.get("transcript_cache_dir") or "output/transcript_cache")
                sha = metadata.get("source_file_hash") or compute_source_file_hash(audio_path)
                if sha:
                    with contextlib.suppress(Exception):
                        seg_path = c_dir / f"{sha}.segments.json"
                        seg_path.write_text(
                            json.dumps(metadata["structured_segments"], indent=2), encoding="utf-8"
                        )
                        from dual_pipeline.transcript_pipeline_core.cache_seal import create_cache_seal
                        create_cache_seal(
                            structured_file=seg_path,
                            audio_sha256=sha or "",
                        )
            return text
    except Exception as e:
        logger.error(f"Audio transcription failed: {e}")
        raise RuntimeError(f"U-1 audio pre-processing failed: {e}") from e


def _inspect_file_target(
    input_path: str, meta: Dict[str, Any]
) -> Tuple[bool, Optional[Path], str, Optional[str]]:
    if input_path.startswith(("http://", "https://")):
        return False, None, ".url", None

    is_file = False
    try:
        if len(input_path) < 4096 and "\n" not in input_path and os.path.exists(input_path):
            is_file = True
    except (ValueError, TypeError):
        is_file = False
    except OSError as e:
        logger.error(f"Failed to inspect file target '{input_path}': {e}")
        raise

    if not is_file and len(input_path) < 4096 and "\n" not in input_path:
        from dual_pipeline.canonical_pipeline.resource_discovery import discover_resource_fuzzy
        from dual_pipeline.exceptions import HITLRequiredError

        proj = meta.get("project")
        date = meta.get("date")
        res = discover_resource_fuzzy(input_path, project=proj, date=date)
        if res.requires_hitl:
            opts = [
                {"label": str(c.path), "score": c.score, "description": f"Match score: {c.score} (modality: {c.modality})"}
                for c in res.candidates
            ]
            raise HITLRequiredError(
                message=f"Ambiguous resource match for '{input_path}'. Multiple candidates found within 30% margin.",
                hitl_type="resource_resolution",
                options=opts,
                evidence={"query": input_path, "candidates": [str(c.path) for c in res.candidates], "margin": res.confidence_margin},
            )
        elif res.best_match:
            logger.info(
                f"[FUZZY_RESOURCE_DISCOVERY] Ingress resolved '{input_path}' -> '{res.best_match.path}' "
                f"(score={res.best_match.score}, elapsed={res.elapsed_seconds}s)"
            )
            matched_p = res.best_match.path
            is_file = True
            input_path = str(matched_p)
            meta["input_path"] = input_path
            if res.best_match.metadata_path and not meta.get("metadata_path"):
                meta["_colocated_metadata"] = str(res.best_match.metadata_path)

    if not is_file:
        return False, None, "", None

    input_p = Path(input_path).expanduser().resolve()
    input_ext = input_p.suffix.lower()
    if input_ext in (".wav", ".wave") and input_p.is_file():
        try:
            from dual_pipeline.audio_ingestion_core.container_validator import repair_wav_riff_header
            repair_wav_riff_header(input_p)
        except Exception as e:
            logger.debug("Pre-hash WAV header repair non-fatal: %s", e)
    source_file_hash = compute_source_file_hash(input_p)
    if source_file_hash:
        meta["source_file_hash"] = source_file_hash
    return True, input_p, input_ext, source_file_hash


def _read_transcript_content(
    input_path: str,
    is_file: bool,
    input_p: Optional[Path],
    input_ext: str,
    meta: Dict[str, Any],
    rec: PipelineSpanRecorder,
    mock: bool,
) -> str:
    if input_ext == ".url" or input_path.startswith(("http://", "https://")):
        logger.info(f"Remote URL source detected ({input_path}). Running Stage U0 Hyperlink Ingress with SSO Guard.")
        from dual_pipeline.url_ingress import fetch_url_with_sso_guard
        content, capture_receipt = fetch_url_with_sso_guard(input_path, allow_browser_escalation=not mock)
        meta["capture_receipt"] = capture_receipt
        meta["source_location"] = input_path
        meta["platform_reference"] = input_path
        meta["source_file_hash"] = f"urn:nee:hyperlink-capture:sha256:{capture_receipt['sha256']}"
        meta["trust_tier"] = "TIER_1_AUTHENTICATED_DOM" if "AUTHENTICATED" in capture_receipt["method"] else "TIER_2_REMOTE_HTTP"
        return normalize_raw_input_text(content, input_ext=".md", metadata=meta)

    if input_ext in AUDIO_EXTENSIONS:
        logger.info(f"Audio input detected ({input_ext}). Running audio pre-processing.")
        text = transcribe_audio_with_cache(str(input_p), meta, rec, mock=mock)
        meta["_from_audio_transcription"] = True
        return text

    if input_ext == ".docx":
        logger.info(f"Word DOCX transcript detected ({input_path}). Extracting text for U0 ingestion.")
        raw_docx = _extract_docx_text(input_p)
        text = normalize_raw_input_text(raw_docx, input_ext=".docx", metadata=meta)
        if "platform" not in meta:
            meta["platform"] = "Microsoft Teams"
        return text

    if input_ext in TEXT_EXTENSIONS or input_ext == "":
        if is_file:
            target_p = str(input_p) if input_p else input_path
            with open(target_p, "r", encoding="utf-8") as f:
                raw_text = f.read()
            if "VERIFIED_STEREO_SEPARATION" in raw_text:
                meta.setdefault("diarization_status", "VERIFIED_STEREO_SEPARATION")
                meta.setdefault("has_acoustic_diarization", True)
                meta.setdefault("source_type", "AUDIO_DIARIZED")
            elif "DIARIZED_ACOUSTIC_RESOLVED" in raw_text:
                meta.setdefault("diarization_status", "DIARIZED_ACOUSTIC_RESOLVED")
                meta.setdefault("has_acoustic_diarization", True)
                meta.setdefault("source_type", "AUDIO_DIARIZED")
            elif "DIARIZED_ACOUSTIC" in raw_text:
                meta.setdefault("diarization_status", "DIARIZED_ACOUSTIC")
                meta.setdefault("has_acoustic_diarization", True)
                meta.setdefault("source_type", "LIVE_AUDIO")
            elif "# Live Meeting Transcript:" in raw_text:
                meta.setdefault("source_type", "LIVE_AUDIO")
        elif "\n" in input_path or len(input_path) > 255:
            raw_text = input_path
            clean_blob = raw_text.strip()
            blob_hash = hashlib.sha256(clean_blob.encode("utf-8")).hexdigest()
            meta["source_file_hash"] = f"urn:nee:unanchored-text:sha256:{blob_hash}"
            meta["trust_tier"] = "TIER_3_UNANCHORED_QUARANTINED"
        else:
            raise FileNotFoundError(f"Input file not found: {input_path}")
        return normalize_raw_input_text(raw_text, input_ext=input_ext, metadata=meta)

    raise ValueError(
        f"Unsupported input format: '{input_ext}'. "
        f"Supported audio: {sorted(AUDIO_EXTENSIONS)}. "
        f"Supported text: {sorted(TEXT_EXTENSIONS)}."
    )


def _align_presentation_if_present(
    transcript_text: str,
    presentation_path: Optional[Union[str, Path]],
    meta: Dict[str, Any],
) -> Tuple[str, Optional[str]]:
    pres_file = presentation_path or meta.get("presentation_path")
    if not pres_file:
        return transcript_text, None

    pres_path = Path(pres_file)
    if not pres_path.exists() or pres_path.suffix.lower() != ".pptx":
        return transcript_text, str(pres_file)

    logger.info(f"Presentation input detected ({pres_path}). Extracting slides.")
    from dual_pipeline.pptx_source import extract_pptx_presentation
    from dual_pipeline.presentation_aligner import (
        align_transcript_presentation,
        inject_slide_annotations,
    )

    pres = extract_pptx_presentation(pres_path)
    meta["presentation_meta"] = {
        "title": pres.title,
        "slide_count": pres.slide_count,
        "path": str(pres_path),
        "sha256": pres.extracted_markdown_sha256,
    }
    alignment = align_transcript_presentation(transcript_text, pres)
    meta["presentation_alignment"] = {
        "total_turns": alignment.total_turns,
        "slide_count": alignment.slide_count,
        "speaker_slide_coverage": alignment.speaker_slide_coverage,
        "unreferenced_slides": alignment.unreferenced_slides,
        "mentions_count": len(alignment.mentions),
    }
    annotated = inject_slide_annotations(transcript_text, alignment, pres)
    return annotated, str(pres_file)


def process_u0_ingress(
    input_path: str,
    metadata: Dict[str, Any],
    output_dir: str = "auto",
    presentation_path: Optional[Union[str, Path]] = None,
    signal_envelope: Optional[Any] = None,
    recorder: Optional[PipelineSpanRecorder] = None,
    mock: bool = False,
) -> U0IngressResult:
    """Executes Stage U0 multi-format ingress, source routing, and pre-flight scaffolding."""
    rec = recorder or PipelineSpanRecorder()
    meta = dict(metadata)
    from dual_pipeline.transcript_stages_core.hitl_guard import validate_diarization_toggle
    meta = validate_diarization_toggle(meta)
    from dual_pipeline.canonical_pipeline.u0_ingress import hydrate_live_container_artifacts, _load_ledger_role_resolutions
    hydrate_live_container_artifacts(meta, input_path=input_path)
    _load_ledger_role_resolutions(meta)
    from dual_pipeline.transcript_stages_core.c06_graphdb_role_gate import _sync_hitl_roles_to_roster
    _sync_hitl_roles_to_roster(meta)

    active_envelope = signal_envelope or meta.get("signal_envelope") or meta.get("forensic_signal_envelope")
    if not input_path and active_envelope is not None:
        transcript_text = active_envelope.render_verbatim_transcript()
        is_file, input_p, input_ext, source_file_hash = False, None, "", None
    else:
        is_file, input_p, input_ext, source_file_hash = _inspect_file_target(input_path, meta)

        # Fast-path idempotency check
        if output_dir and output_dir.lower() != "auto" and source_file_hash:
            fast_idempotent = check_idempotent_meeting_run(
                output_dir, meta, source_file_hash=source_file_hash
            )
            if fast_idempotent:
                return U0IngressResult(
                    transcript_text="",
                    metadata=meta,
                    input_hash="",
                    source_file_hash=source_file_hash,
                    fast_idempotent=fast_idempotent,
                )

        transcript_text = _read_transcript_content(
            input_path, is_file, input_p, input_ext, meta, rec, mock
        )

    if is_file and input_p and "speaker_map" not in meta:
        from dual_pipeline.transcript_pipeline_core.speaker_map_loader import (
            load_colocated_speaker_map,
        )

        sidecar_map = load_colocated_speaker_map(input_p)
        if sidecar_map:
            meta["speaker_map"] = sidecar_map

    # Source Modality Classification
    modality = classify_source_modality(transcript_text, meta, input_ext=input_ext)
    meta["source_modality"] = modality.value
    if modality == SourceModality.AUDIO_ONLY_UNDIARIZED:
        meta.setdefault("source_type", "AUDIO_ONLY_UNDIARIZED")
        meta["diarization_status"] = "UNDIARIZED"

    # Presentation Alignment
    transcript_text, resolved_pres_file = _align_presentation_if_present(
        transcript_text, presentation_path, meta
    )

    final_text = str(transcript_text.text) if hasattr(transcript_text, "text") else str(transcript_text)
    input_hash = hashlib.sha256(final_text.encode("utf-8")).hexdigest()

    return U0IngressResult(
        transcript_text=final_text,
        metadata=meta,
        input_hash=input_hash,
        source_file_hash=source_file_hash,
        is_file=is_file,
        input_ext=input_ext,
        pres_file=resolved_pres_file,
    )
