"""
dual_pipeline/canonical_pipeline/content_loader.py

Source content loading and normalization helpers for CanonicalAgenticPipeline.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from config.credentials import CredentialManager
from dual_pipeline.canonical_pipeline.modalities import (
    AUDIO_EXTENSIONS,
    DOCUMENT_EXTENSIONS,
    IMAGE_EXTENSIONS,
)
from dual_pipeline.l0.ingestion_timing import (
    SPAN_SOURCE_ASSEMBLY,
    SPAN_SOURCE_DISCOVERY,
    timed_child_span,
)

logger = logging.getLogger(__name__)


def _normalize_and_extract(raw: str, meta: dict, ext: str = "") -> str:
    from dual_pipeline.transcript_pipeline_core.normalizer import normalize_raw_input_text
    norm_result = normalize_raw_input_text(raw, input_ext=ext, metadata=meta)
    if hasattr(norm_result, 'text'):
        meta['normalized_turns'] = getattr(norm_result, 'turns', [])
        return norm_result.text
    return str(norm_result)


def _try_load_frozen_transcript(meta: Dict[str, Any]) -> Optional[str]:
    regen = meta.get("regenerate")
    out_dir = meta.get("target_output_dir") or meta.get("output_dir")
    if not (regen and out_dir and Path(out_dir).is_dir()):
        return None
    from dual_pipeline.remediation_core import parse_regenerate_spec, validate_frozen_artifact_gate
    try:
        spec = parse_regenerate_spec(regen)
        if "output_1" in spec:
            return None
        val_res = validate_frozen_artifact_gate(Path(out_dir), "output_1", meta)
        if val_res.valid and val_res.content:
            meta.setdefault("raw_transcript", val_res.content)
            return val_res.content
    except Exception as exc:
        logger.debug("Frozen transcript validation skipped: %s", exc)
    return None


def _resolve_mapped_speaker(raw_spk: str, spk_label: str, spk_map: Dict[str, Any]) -> Optional[str]:
    mapped = spk_map.get(raw_spk) or spk_map.get(spk_label)
    if not mapped and not raw_spk.strip().lower().startswith(("speaker", "voice", "unknown")) and not raw_spk.strip().isdigit() and len(raw_spk.strip()) > 1:
        mapped = raw_spk.strip()
    if not mapped and not spk_label.lower().startswith(("speaker", "voice", "unknown")):
        mapped = spk_label
    if mapped and not str(mapped).lower().startswith(("speaker", "voice", "unknown")):
        return str(mapped)
    return None


def _build_turn_signals(
    segments: List[Dict[str, Any]],
    transcript: str,
    meta: Dict[str, Any],
    sample_rate: int = 16000,
) -> List[Any]:
    from dual_pipeline.audio_contracts import ForensicTurnSignal, SampleSpan
    from dual_pipeline.audio_ingestion_core.ffmpeg import clean_speaker_label
    try:
        from dual_pipeline.canonical_pipeline.u0_speaker_hints import resolve_pre_hitl_speaker_hints
        resolve_pre_hitl_speaker_hints(meta)
    except Exception as exc:
        logger.debug("Pre-HITL speaker hint resolution skipped: %s", exc)
    spk_map = meta.get("speaker_map") if isinstance(meta.get("speaker_map"), dict) else {}
    turns = []
    last_end_samp = 0
    for i, s in enumerate(segments):
        start_sec = max(0.0, float(s.get("start", s.get("start_time_sec", 0.0))))
        end_sec = max(start_sec + 0.2, float(s.get("end", s.get("end_time_sec", start_sec + 3.0))))
        start_samp = max(last_end_samp, int(start_sec * sample_rate))
        end_samp = max(start_samp + 160, int(end_sec * sample_rate))
        last_end_samp = end_samp
        raw_spk = str(s.get("speaker") or "Speaker 1")
        spk_label = clean_speaker_label(raw_spk)
        resolved_person = _resolve_mapped_speaker(raw_spk, spk_label, spk_map)
        turns.append(
            ForensicTurnSignal(
                turn_id=f"turn-{i+1:03d}",
                span=SampleSpan(start_sample=start_samp, end_sample=end_samp),
                transcript_verbatim=str(s.get("text") or "").strip() or "...",
                speaker_cluster_ids=(spk_label,),
                roster_person_id=resolved_person,
                identity_state="enrollment_matched" if resolved_person else "cluster_only",
                identity_evidence_id=f"evidence-{i+1:03d}" if resolved_person else None,
                commitment_state="committed",
            )
        )
    if not turns:
        dur_s = float(meta.get("declared_duration_seconds") or 30.0)
        turns.append(
            ForensicTurnSignal(
                turn_id="turn-001",
                span=SampleSpan(start_sample=0, end_sample=max(1600, int(dur_s * sample_rate))),
                transcript_verbatim=transcript.strip()[:500] or "...",
                speaker_cluster_ids=("Speaker 1",),
                identity_state="cluster_only",
                commitment_state="committed",
            )
        )
    return turns


def _record_and_enforce_qa_report(qa_rep: Any, envelope: Any, meta: Dict[str, Any]) -> None:
    cert = qa_rep.audio_certificate
    if cert and cert.verdict.value == "WARN" and bool(meta.get("hitl_approved") or meta.get("allow_warn_certificate", True)):
        cert = cert.model_copy(update={"hitl_approved": bool(meta.get("hitl_approved") or meta.get("allow_warn_certificate", True))})
        qa_rep = qa_rep.model_copy(update={"audio_certificate": cert})
    meta.setdefault("signal_envelope", envelope)
    meta.setdefault("upstream_qa_report", qa_rep)
    meta.setdefault("qa_report", qa_rep)
    if cert:
        meta.setdefault("audio_certificate", cert)
    if qa_rep.verdict in ("REJECT", "NO_SPEECH") and bool(meta.get("enforce_upstream_audio_qa")):
        reject_codes = [f.code for f in qa_rep.findings if f.severity == "blocking"]
        raise ValueError(
            f"Upstream Audio QA Gatekeeper REJECTED standalone audio with verdict {qa_rep.verdict}. Blocking codes: {reject_codes}"
        )


def _mint_standalone_audio_certificate(src_path: Path, transcript: str, meta: Dict[str, Any]) -> None:
    """Builds a ForensicSignalEnvelope and Gate F AudioCaptureCertificate for standalone audio inputs."""
    if meta.get("audio_certificate") and meta.get("signal_envelope"):
        return
    try:
        from dual_pipeline.audio_contracts import ForensicSignalEnvelope
        from dual_pipeline.audio_ingestion_core.base import _parse_raw_segments
        from dual_pipeline.core import sha256_file
        from dual_pipeline.upstream_audio_qa import UpstreamAudioQAGatekeeper

        sample_rate = 16000
        segments = _parse_raw_segments(transcript, meta)
        turns = _build_turn_signals(segments, transcript, meta, sample_rate)
        decl_dur = float(meta.get("declared_duration_seconds") or 0.0)
        max_turn_samp = turns[-1].span.end_sample if turns else sample_rate
        total_samples = max(max_turn_samp, int(decl_dur * sample_rate))
        file_sha = meta.get("raw_source_sha256") or meta.get("audio_sha256") or sha256_file(src_path)
        meta["audio_sha256"] = file_sha
        safe_stem = src_path.stem
        source_hash = f"sha256:{file_sha}"
        digest = ForensicSignalEnvelope.compute_signal_digest(
            recording_id=safe_stem,
            source_hashes=(source_hash,),
            turns=tuple(turns),
        )
        envelope = ForensicSignalEnvelope(
            schema_version="2.1.0",
            recording_id=safe_stem,
            source_hashes=(source_hash,),
            canonical_sample_rate_hz=sample_rate,
            timeline_length_samples=total_samples,
            turns=tuple(turns),
            signal_digest=digest,
        )
        gatekeeper = UpstreamAudioQAGatekeeper()
        exp_dur = float(meta.get("expected_duration") or meta.get("expected_duration_seconds") or decl_dur or (total_samples / sample_rate))
        qa_rep = gatekeeper.evaluate(envelope, expected_duration_s=exp_dur, metadata=meta)
        _record_and_enforce_qa_report(qa_rep, envelope, meta)
    except ValueError:
        raise
    except Exception as exc:
        logger.debug("Standalone audio certificate minting skipped: %s", exc)


def _load_audio_transcript(src_path: Path, meta: Dict[str, Any], offline: bool) -> str:
    if meta.get("raw_transcript"):
        text = str(meta["raw_transcript"])
        _mint_standalone_audio_certificate(src_path, text, meta)
        return text
    if meta.get("transcript"):
        text = str(meta["transcript"])
        _mint_standalone_audio_certificate(src_path, text, meta)
        return text
    frozen = _try_load_frozen_transcript(meta)
    if frozen:
        _mint_standalone_audio_certificate(src_path, frozen, meta)
        return frozen
    from dual_pipeline.transcript_pipeline_core.ingress import transcribe_audio_with_cache
    from dual_pipeline.telemetry import NullPipelineSpanRecorder

    rec = meta.get("recorder") or NullPipelineSpanRecorder()
    transcript = transcribe_audio_with_cache(
        str(src_path),
        metadata=meta,
        recorder=rec,
        mock=bool(meta.get("mock", False)),
    )
    if not transcript or not transcript.strip():
        from dual_pipeline.exceptions import TranscriptEmptyError
        raise TranscriptEmptyError(f"Transcriber returned empty text for audio file '{src_path.name}'.")
    _mint_standalone_audio_certificate(src_path, transcript, meta)
    return transcript


def _load_text_or_markdown(src_path: Path, ext: str, meta: Dict[str, Any]) -> str:
    if _is_document_source(meta):
        from dual_pipeline.document_pipeline_core.content_loader import load_document_content
        text, _, _ = load_document_content(src_path, metadata=meta)
        return text
    raw = src_path.read_text(encoding="utf-8", errors="replace")
    norm_text = _normalize_and_extract(raw, meta, ext)
    meta.setdefault("raw_transcript", norm_text if ext == ".json" else raw)
    return norm_text


def load_file_content(src_path: Path, ext: str, meta: Dict[str, Any], offline: bool) -> str:
    """Load text content from a supported file format."""
    if ext in AUDIO_EXTENSIONS:
        return _load_audio_transcript(src_path, meta, offline)
    if ext == ".docx":
        if _is_document_source(meta):
            from dual_pipeline.document_pipeline_core.content_loader import load_document_content
            text, _, _ = load_document_content(src_path, metadata=meta)
            return text
        from dual_pipeline.transcript_pipeline_core.normalizer import _extract_docx_text
        return _normalize_and_extract(_extract_docx_text(src_path), meta, ext)
    if ext in {".txt", ".md", ".text", ".markdown", ".vtt", ".srt", ".json"}:
        return _load_text_or_markdown(src_path, ext, meta)
    if ext in DOCUMENT_EXTENSIONS or ext in IMAGE_EXTENSIONS:
        from dual_pipeline.document_pipeline_core.content_loader import load_document_content
        text, _, _ = load_document_content(src_path, metadata=meta)
        return text
    if ext in {".csv", ".tsv"}:
        return _load_text_or_markdown(src_path, ext, meta)
    raise ValueError(f"Unsupported input format: '{ext}'. To inspect supported formats, run: nee formats")


def align_presentation_text(
    text: str, presentation_path: Optional[Union[str, Path]], meta: Dict[str, Any]
) -> str:
    """Align presentation collateral text if provided."""
    pres_file = presentation_path or meta.get("presentation_path")
    if not pres_file:
        return text
    from dual_pipeline.transcript_pipeline_core.ingress import _align_presentation_if_present
    aligned_text, _ = _align_presentation_if_present(text, pres_file, meta)
    return aligned_text


def _is_document_source(meta: Dict[str, Any]) -> bool:
    return bool(
        meta.get("is_document")
        or meta.get("source_type") == "document"
        or meta.get("canonical_modality") == "DOCUMENT"
    )


def _validate_pdf_container(src_path: Path) -> None:
    from dual_pipeline.exceptions import SourceIntegrityError
    with open(src_path, "rb") as f:
        header = f.read(5)
    if not header.startswith(b"%PDF-"):
        raise SourceIntegrityError(
            f"Physical format mismatch for '{src_path.name}': expected PDF magic bytes '%PDF-', got {header!r}."
        )


def _check_zip_payload_names(names: set[str], ext: str, name: str) -> None:
    from dual_pipeline.exceptions import SourceIntegrityError
    if ext == ".docx" and "word/document.xml" not in names:
        raise SourceIntegrityError(f"Invalid DOCX container for '{name}': missing 'word/document.xml'.")
    prefix_map = {".xlsx": ("xl/", "spreadsheet"), ".pptx": ("ppt/", "presentation"), ".vsdx": ("visio/", "Visio")}
    if ext in prefix_map:
        pref, label = prefix_map[ext]
        if "[Content_Types].xml" not in names and not any(k.startswith(pref) for k in names):
            raise SourceIntegrityError(f"Invalid {ext[1:].upper()} container for '{name}': missing {label} XML structures.")


def _validate_office_zip_container(src_path: Path, ext: str) -> None:
    import zipfile
    from dual_pipeline.exceptions import SourceIntegrityError
    if not zipfile.is_zipfile(src_path):
        raise SourceIntegrityError(
            f"Corrupted or invalid container for '{src_path.name}': file is not a valid zip archive."
        )
    try:
        with zipfile.ZipFile(src_path, "r") as z:
            _check_zip_payload_names(set(z.namelist()), ext, src_path.name)
    except zipfile.BadZipFile as exc:
        raise SourceIntegrityError(
            f"Corrupted container for '{src_path.name}': {exc}"
        ) from exc


def _validate_text_container(src_path: Path) -> None:
    from dual_pipeline.exceptions import SourceIntegrityError
    with open(src_path, "rb") as f:
        sample = f.read(1024)
    if b"\x00" in sample:
        raise SourceIntegrityError(
            f"Physical format mismatch for '{src_path.name}': binary data detected in text input."
        )


def _validate_source_container(src_path: Path, ext: str) -> None:
    """Preflight check on container integrity and magic bytes before any model/provider dispatch."""
    if ext == ".pdf":
        _validate_pdf_container(src_path)
    elif ext in {".docx", ".xlsx", ".pptx", ".vsdx"}:
        _validate_office_zip_container(src_path, ext)
    elif ext in {".txt", ".md", ".text", ".markdown", ".vtt", ".srt", ".json", ".svg", ".drawio"}:
        _validate_text_container(src_path)


def _inspect_audio_or_doc_headers(src_path: Path, ext: str, meta: Dict[str, Any]) -> None:
    if ext in AUDIO_EXTENSIONS:
        from dual_pipeline.audio_ingestion_core.container_validator import validate_audio_container_headers
        spec = validate_audio_container_headers(src_path)
        meta["is_audio"] = True
        meta["audio_path"] = str(src_path)
        meta["audio_spec"] = spec
        if spec.declared_duration_seconds is not None:
            meta["declared_duration_seconds"] = spec.declared_duration_seconds
            if not meta.get("duration_minutes") and spec.declared_duration_seconds > 0:
                meta["duration_minutes"] = max(1, int(round(spec.declared_duration_seconds / 60.0)))
    elif _is_document_source(meta):
        from dual_pipeline.document_pipeline_core.preflight import validate_intake_preflight
        preflight_rep = validate_intake_preflight(src_path, meta)
        meta["intake_preflight"] = {
            "valid": preflight_rep.valid,
            "dependencies_verified": preflight_rep.dependencies_verified,
            "detected_formats": preflight_rep.detected_formats,
            "total_bytes": preflight_rep.total_bytes,
        }


def _load_from_source_file(src_path: Path, meta: Dict[str, Any], offline: bool) -> str:
    with timed_child_span(SPAN_SOURCE_DISCOVERY, {"source_path": str(src_path)}):
        from dual_pipeline.exceptions import SourceIntegrityError
        file_size = src_path.stat().st_size
        if file_size == 0:
            raise SourceIntegrityError(f"Input source file is empty (0 bytes): {src_path}")
        ext = src_path.suffix.lower()
        max_bytes = 500 * 1024 * 1024 if ext in AUDIO_EXTENSIONS else 50 * 1024 * 1024
        if file_size > max_bytes:
            raise SourceIntegrityError(
                f"Input source file '{src_path.name}' exceeds the maximum allowed size of {max_bytes // (1024 * 1024)} MiB ({file_size} bytes)."
            )
        _validate_source_container(src_path, ext)
        from dual_pipeline.core import sha256_file
        actual_file_hash = sha256_file(src_path)
        meta["raw_source_sha256"] = actual_file_hash
        expected_hash = meta.get("expected_sha256")
        if expected_hash and actual_file_hash.lower() != expected_hash.strip().lower():
            raise SourceIntegrityError(
                f"Source file SHA-256 verification failed for '{src_path.name}': expected {expected_hash}, got {actual_file_hash}"
            )
        _inspect_audio_or_doc_headers(src_path, ext, meta)
    text = load_file_content(src_path, src_path.suffix.lower(), meta, offline=offline)
    if "speaker_map" not in meta:
        from dual_pipeline.transcript_pipeline_core.speaker_map_loader import load_colocated_speaker_map
        sidecar_map = load_colocated_speaker_map(src_path)
        if sidecar_map:
            meta["speaker_map"] = sidecar_map
    if "DIARIZED_ACOUSTIC_RESOLVED" in text:
        meta.setdefault("diarization_status", "DIARIZED_ACOUSTIC_RESOLVED")
    return text


def _extract_source_from_ckpt(ckpt_f: Path, dir_p: Path) -> Optional[Path]:
    import json
    try:
        cdata = json.loads(ckpt_f.read_text(encoding="utf-8"))
        u0_block = cdata.get("U0") or cdata.get("u0_data") or {}
        u0_src = u0_block.get("src_file_path") or u0_block.get("original_source_path") or cdata.get("original_source_path")
        if u0_src and Path(u0_src).is_file() and dir_p.resolve() in Path(u0_src).resolve().parents:
            return Path(u0_src)
    except Exception as exc:
        logger.debug("Checkpoint source extraction skipped for %s: %s", ckpt_f, exc)
    return None


def _scan_checkpoint_source_path(dir_p: Path) -> Optional[Path]:
    from dual_pipeline.core import ROOT
    ckpt_dirs = [dir_p]
    sandbox = CredentialManager.get_credential("TEST_SANDBOX_DIR")
    if sandbox:
        ckpt_dirs.append(Path(sandbox) / "dual_pipeline" / "checkpoints")
    ckpt_dirs.append(ROOT / ".local" / "dual_pipeline" / "checkpoints")
    for c_dir in ckpt_dirs:
        if not c_dir.is_dir():
            continue
        for ckpt_f in sorted(c_dir.glob("checkpoint_*.json")):
            found = _extract_source_from_ckpt(ckpt_f, dir_p)
            if found:
                return found
    return None


def _resolve_resume_source_path(dir_p: Path, meta: Dict[str, Any]) -> Optional[Path]:
    import json
    orig_cand = meta.get("original_source_path") or meta.get("audio_path")
    if orig_cand and Path(str(orig_cand)).is_file():
        return Path(str(orig_cand))
    meta_json = dir_p / "metadata.json"
    if meta_json.is_file():
        try:
            mdata = json.loads(meta_json.read_text(encoding="utf-8"))
            m_src = mdata.get("original_source_path") or mdata.get("audio_path") or mdata.get("source_path")
            if m_src and Path(str(m_src)).is_file():
                return Path(str(m_src))
        except Exception as exc:
            logger.debug("Resume metadata parsing skipped for %s: %s", meta_json, exc)
    return _scan_checkpoint_source_path(dir_p)


def _first_matching_file(dir_p: Path, patterns: Tuple[str, ...]) -> Optional[Path]:
    for pat in patterns:
        found = sorted(dir_p.glob(pat))
        if found:
            return found[0]
    return None


def _resolve_container_source_file(dir_p: Path, meta: Optional[Dict[str, Any]] = None) -> Optional[Path]:
    """Resolves a meeting container directory to its canonical source transcript or audio file.

    Priority order:
      1. HITL-resolved transcript (`01_*_hitl_resolved_transcript.md`)
      2. Authoritative Master WAV (`00_*_master*.wav` in container or `.cache/`)
      3. Non-authoritative live preview segments/markdown (`01_*_segments.jsonl`, `01_*_live_transcript.md`)
    """
    if not dir_p.is_dir():
        return None
    if meta and meta.get("resume"):
        resumed = _resolve_resume_source_path(dir_p, meta)
        if resumed:
            return resumed
    hitl_tx = _first_matching_file(dir_p, ("01_*_hitl_resolved_transcript.md", "*_hitl_resolved_transcript.md"))
    if hitl_tx:
        return hitl_tx
    cache_d = dir_p / ".cache"
    wav = _first_matching_file(dir_p, ("00_*_master*.wav", "*master*.wav", "*.wav")) or (_first_matching_file(cache_d, ("00_*_master*.wav", "*.wav")) if cache_d.is_dir() else None)
    if wav:
        return wav
    live_tx = _first_matching_file(dir_p, ("01_*_live_transcript.md", "*_live_transcript.md"))
    if live_tx:
        return live_tx
    seg = (_first_matching_file(cache_d, ("01_*_segments.jsonl", "*_segments.jsonl")) if cache_d.is_dir() else None) or _first_matching_file(dir_p, ("01_*_segments.jsonl", "*_segments.jsonl"))
    if seg:
        return seg
    return _first_matching_file(dir_p, ("01_*.md",))


def _resolve_dir_source_into_meta(target_dir: Path, meta: Dict[str, Any]) -> Optional[Path]:
    resolved = _resolve_container_source_file(target_dir, meta)
    if resolved:
        meta["source_path"] = str(resolved)
    return resolved


def _fetch_http_source_content(input_path: str, meta: Dict[str, Any], offline: bool) -> str:
    from dual_pipeline.url_ingress import fetch_url_with_sso_guard
    content, capture_receipt = fetch_url_with_sso_guard(input_path, allow_browser_escalation=not offline)
    meta["capture_receipt"] = capture_receipt
    meta["source_location"] = input_path
    meta["source_file_hash"] = f"urn:nee:hyperlink-capture:sha256:{capture_receipt['sha256']}"
    return content if _is_document_source(meta) else _normalize_and_extract(content, meta, ".md")


def _extract_raw_source_text(
    input_path: Union[str, Path, None],
    meta: Dict[str, Any],
    src_path: Optional[Path],
    offline: bool,
) -> str:
    if meta.get("raw_transcript"):
        return str(meta["raw_transcript"])
    if src_path and src_path.is_dir():
        src_path = _resolve_dir_source_into_meta(src_path, meta) or src_path
    elif input_path and Path(str(input_path)).is_dir():
        src_path = _resolve_dir_source_into_meta(Path(str(input_path)), meta) or src_path
    if src_path and src_path.is_file():
        return _load_from_source_file(src_path, meta, offline)
    if meta.get("signal_envelope"):
        import json
        env = meta["signal_envelope"]
        return json.dumps(env, indent=2) if isinstance(env, dict) else str(env)
    if isinstance(input_path, str) and input_path.startswith(("http://", "https://")):
        return _fetch_http_source_content(input_path, meta, offline)
    return _fallback_raw_input_text(input_path, meta)


def _fallback_raw_input_text(input_path: Union[str, Path, None], meta: Dict[str, Any]) -> str:
    if input_path and (isinstance(input_path, Path) or (isinstance(input_path, str) and Path(input_path).suffix)):
        candidate = Path(str(input_path)).resolve()
        if not candidate.is_file():
            raise FileNotFoundError(f"Input source file does not exist: {candidate}. Verify the --input path is correct.")
    raw = str(input_path or "")
    return raw if _is_document_source(meta) else _normalize_and_extract(raw, meta, "")


def _collect_multi_sources(src_path: Path, meta: Dict[str, Any]) -> list[Path]:
    source_list = [src_path]
    resolved = {src_path.resolve()}
    extra = meta.get("source_attachments") or meta.get("sources") or meta.get("attachments") or []
    for item in (extra if isinstance(extra, (list, tuple)) else []):
        p = Path(item) if not isinstance(item, Path) else item
        if p.is_file() and p.resolve() not in resolved:
            source_list.append(p)
            resolved.add(p.resolve())
    return source_list


def _populate_document_source_manifest(src_path: Optional[Path], text: str, meta: Dict[str, Any]) -> None:
    if not src_path:
        return
    from dual_pipeline.document_pipeline_core.source_manifest import build_source_manifest
    sources = _collect_multi_sources(src_path, meta)
    manifest = build_source_manifest(sources, metadata=meta, converted_texts={str(src_path): text})
    meta["source_manifest"] = manifest.to_dict()


def _resolve_safe_source_path(input_path: Union[str, Path, None], meta: Dict[str, Any]) -> Optional[Path]:
    if not (input_path and (isinstance(input_path, Path) or Path(str(input_path)).exists())):
        return None
    from dual_pipeline.core import ROOT
    from dual_pipeline.security_paths import assert_safe_source_path
    from dual_pipeline.transcript_pipeline_core.caching import compute_source_file_hash
    src_path = assert_safe_source_path(ROOT, input_path)
    if src_path and src_path.is_dir():
        resolved = _resolve_container_source_file(src_path, meta)
        if resolved:
            meta["container_dir"] = str(src_path)
            src_path = resolved
    if src_path and src_path.is_file():
        meta["input_path"] = str(src_path)
        meta["source_path"] = str(src_path)
        meta["source_file_hash"] = compute_source_file_hash(src_path)
    return src_path


def _ensure_canonical_modality(input_path: Union[str, Path, None], meta: Dict[str, Any]) -> None:
    if "canonical_modality" in meta:
        return
    try:
        from dual_pipeline.canonical_pipeline.modalities import detect_modality
        pre_mod = detect_modality(input_path, None, meta)
        meta["canonical_modality"] = pre_mod.value
        if pre_mod.value == "DOCUMENT":
            meta["is_document"] = True
        elif pre_mod.value == "MEETING_NOTES":
            meta["is_notes"] = True
    except Exception as exc:
        logger.debug("Canonical modality detection skipped: %s", exc)


def load_canonical_source_content(
    input_path: Union[str, Path, None],
    meta: Dict[str, Any],
    presentation_path: Optional[Union[str, Path]] = None,
    offline: bool = True,
) -> Tuple[str, str, Optional[Path]]:
    """Load, normalize, align, and hash canonical source text."""
    src_path = _resolve_safe_source_path(input_path, meta)
    _ensure_canonical_modality(input_path, meta)

    with timed_child_span(SPAN_SOURCE_ASSEMBLY, {"has_presentation": bool(presentation_path)}):
        text = _extract_raw_source_text(input_path, meta, src_path, offline)
        text = align_presentation_text(text, presentation_path, meta)
        if _is_document_source(meta):
            from dual_pipeline.evidence_field_limits import validate_source_preflight
            meta["source_preflight"] = validate_source_preflight(text, meta)
            _populate_document_source_manifest(src_path, text, meta)
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return text, h, src_path
