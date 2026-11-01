"""
dual_pipeline/canonical_pipeline/modalities.py

Single Source of Truth (SSOT) for Canonical Pipeline modality classification.
Categorizes incoming meeting and document material into four polymorphic modalities:
1. AUDIO: Speech/recording requiring upstream STT transcription -> yields [01, 02, 03].
2. MEETING_TRANSCRIPT: Diarized spoken transcript text -> yields [01, 02, 03].
3. MEETING_NOTES: Undiarized notes/summaries (Copilot, Zoom) -> yields [02, 03].
4. DOCUMENT: Written text, memos, emails, SOWs, architecture specs -> yields [03].
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from dual_pipeline.source_classification.capability_registry import (
    get_audio_extensions,
    get_cue_extensions,
    get_document_extensions,
    get_image_extensions,
)

AUDIO_EXTENSIONS = get_audio_extensions()


class CanonicalModality(str, Enum):
    """The four canonical ingestion modalities."""
    AUDIO = "AUDIO"
    MEETING_TRANSCRIPT = "MEETING_TRANSCRIPT"
    MEETING_NOTES = "MEETING_NOTES"
    DOCUMENT = "DOCUMENT"


DOCUMENT_EXTENSIONS = get_document_extensions()
IMAGE_EXTENSIONS = get_image_extensions()
TRANSCRIPT_EXTENSIONS = frozenset(get_cue_extensions() | {".transcript"})


def get_deliverables_for_modality(modality: CanonicalModality) -> List[str]:
    """Returns the list of required deliverables for a given modality."""
    if modality in (CanonicalModality.AUDIO, CanonicalModality.MEETING_TRANSCRIPT):
        return ["01", "02", "03"]
    if modality == CanonicalModality.MEETING_NOTES:
        return ["02", "03"]
    if modality == CanonicalModality.DOCUMENT:
        return ["03"]
    return ["01", "02", "03"]


def _detect_from_explicit_metadata(meta: Dict[str, Any]) -> Optional[CanonicalModality]:
    explicit_modality = meta.get("modality") or meta.get("canonical_modality")
    if explicit_modality:
        mod_upper = str(explicit_modality).upper()
        if mod_upper in CanonicalModality.__members__:
            return CanonicalModality[mod_upper]
        raise ValueError(
            f"Invalid or unrecognized explicit modality: '{explicit_modality}'. "
            f"Allowed: {list(CanonicalModality.__members__.keys())}"
        )
    return None


def _is_doc_metadata(meta: Dict[str, Any]) -> bool:
    doc_keys = ("is_document", "document", "is_envelope")
    if any(meta.get(k) for k in doc_keys) or meta.get("signal_envelope") is True:
        return True
    return meta.get("source_type") in ("document", "evidence_envelope", "signal_envelope")


def _is_notes_metadata(meta: Dict[str, Any]) -> bool:
    notes_keys = ("notes_mode", "is_notes")
    if any(meta.get(k) for k in notes_keys):
        return True
    return meta.get("source_type") in ("copilot_notes", "meeting_notes", "notes")


def _is_transcript_metadata(meta: Dict[str, Any]) -> bool:
    trans_keys = ("is_transcript", "transcript_mode")
    if any(meta.get(k) for k in trans_keys):
        return True
    return meta.get("source_type") in ("meeting_transcript", "transcript", "diarized_transcript")


def _read_content_sample(path_obj: Optional[Path], content: Optional[str], ext: str) -> str:
    if content:
        return content
    if not (path_obj and path_obj.is_file()):
        return ""
    try:
        if ext == ".docx":
            from dual_pipeline.transcript_pipeline_core.normalizer import _extract_docx_text
            return _extract_docx_text(path_obj)
        return path_obj.read_text(encoding="utf-8", errors="ignore")[:20000]
    except Exception:
        return ""


def _detect_json_sample(text: str) -> Optional[CanonicalModality]:
    try:
        import json
        data = json.loads(text[:10000])
        if isinstance(data, dict):
            if any(k in data for k in ("graph_mutations", "entity_upserts", "signals", "canonical_digest", "evidence_items", "schema")):
                return CanonicalModality.DOCUMENT
            if any(k in data for k in ("turns", "transcript", "dialogue", "utterances", "segments", "speaker_turns")):
                return CanonicalModality.MEETING_TRANSCRIPT
        elif isinstance(data, list) and data and isinstance(data[0], dict):
            if any(k in data[0] for k in ("speaker", "text", "start", "end", "timestamp", "utterance")):
                return CanonicalModality.MEETING_TRANSCRIPT
    except Exception:
        pass
    return None


def _detect_from_content_sample(text: str, filename: str) -> Optional[CanonicalModality]:
    if not text:
        return None
    from dual_pipeline.transcript_pipeline_core.normalizer import is_copilot_notes
    if is_copilot_notes(text):
        return CanonicalModality.MEETING_NOTES
    if filename.lower().endswith(".json") or text.strip().startswith(("{", "[")):
        json_res = _detect_json_sample(text)
        if json_res:
            return json_res
    try:
        from dual_pipeline.transcript_metadata_helper import (
            _has_spoken_transcript_markers,
            is_document_content,
        )
        if is_document_content(text, filename):
            return CanonicalModality.DOCUMENT
        if _has_spoken_transcript_markers(text):
            return CanonicalModality.MEETING_TRANSCRIPT
    except ImportError:
        pass
    return None


def _validate_conflicting_metadata(meta: Dict[str, Any]) -> None:
    is_doc = _is_doc_metadata(meta)
    is_notes = _is_notes_metadata(meta)
    is_trans = _is_transcript_metadata(meta)
    if sum([is_doc, is_notes, is_trans]) > 1:
        raise ValueError("Conflicting modality metadata flags: multiple incompatible modalities specified.")


def _resolve_metadata_modality(
    meta: Dict[str, Any],
    ext: str,
    path_obj: Optional[Path],
) -> Optional[CanonicalModality]:
    _validate_conflicting_metadata(meta)
    is_doc = _is_doc_metadata(meta)
    if ext in AUDIO_EXTENSIONS:
        if is_doc:
            name = path_obj.name if path_obj else ""
            raise ValueError(f"Audio file '{name}' cannot be coerced to DOCUMENT modality via metadata (L08).")
        return CanonicalModality.AUDIO

    explicit = _detect_from_explicit_metadata(meta)
    if explicit:
        return explicit
    if is_doc:
        return CanonicalModality.DOCUMENT
    if _is_notes_metadata(meta):
        return CanonicalModality.MEETING_NOTES
    if _is_transcript_metadata(meta):
        return CanonicalModality.MEETING_TRANSCRIPT
    if meta.get("_from_audio_transcription"):
        return CanonicalModality.AUDIO
    return None


DUAL_USE_TEXT_EXTENSIONS = frozenset({".txt", ".md", ".text", ".markdown", ".json", ".docx"})


def is_unambiguous_document_extension(ext: str) -> bool:
    """Return True if the file extension unambiguously identifies a document or visual collateral."""
    clean_ext = f".{ext.lstrip('.').lower()}" if ext else ""
    return (clean_ext in DOCUMENT_EXTENSIONS and clean_ext not in DUAL_USE_TEXT_EXTENSIONS) or clean_ext in IMAGE_EXTENSIONS


def _resolve_fallback_modality(
    ext: str,
    path_obj: Optional[Path],
    content: Optional[str],
    input_path: Union[str, Path, None],
) -> CanonicalModality:
    if ext in TRANSCRIPT_EXTENSIONS:
        return CanonicalModality.MEETING_TRANSCRIPT
    if is_unambiguous_document_extension(ext):
        return CanonicalModality.DOCUMENT

    sample = _read_content_sample(path_obj, content, ext)
    from_sample = _detect_from_content_sample(sample, path_obj.name if path_obj else "")
    if from_sample:
        return from_sample

    if ext in {".txt", ".md", ".text", ".markdown"}:
        return CanonicalModality.MEETING_TRANSCRIPT
    if ext in {".json", ".docx"}:
        return CanonicalModality.DOCUMENT
    if not ext and content:
        return CanonicalModality.MEETING_TRANSCRIPT

    raise ValueError(f"Unrecognized or unsupported input format/modality for: '{input_path}'.")


def detect_modality(
    input_path: Union[str, Path, None] = None,
    content: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> CanonicalModality:
    """Deterministically classifies source material into one of the four canonical modalities."""
    meta = metadata or {}
    path_obj = Path(input_path).resolve() if input_path else None
    ext = path_obj.suffix.lower() if path_obj else ""

    resolved = _resolve_metadata_modality(meta, ext, path_obj)
    if resolved is not None:
        return resolved

    return _resolve_fallback_modality(ext, path_obj, content, input_path)
