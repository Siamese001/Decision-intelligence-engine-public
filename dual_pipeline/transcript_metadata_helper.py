"""
dual_pipeline/transcript_metadata_helper.py

Utility for auto-scaffolding and resolving meeting metadata when ingesting
audio recordings or raw transcripts without a pre-authored metadata.json.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from dual_pipeline.meeting_paths import standardize_meeting_slug
from dual_pipeline.source_classification.capability_registry import get_audio_extensions
from dual_pipeline.transcript_duration_helper import resolve_meeting_duration
from dual_pipeline.transcript_metadata_core import transcript_attendee_parser

build_roster_from_attendees = transcript_attendee_parser.build_roster_from_attendees
extract_attendees_from_header_block = transcript_attendee_parser.extract_attendees_from_header_block
extract_speakers_from_dialogue_turns = transcript_attendee_parser.extract_speakers_from_dialogue_turns
is_blacklisted_attendee = transcript_attendee_parser.is_blacklisted_attendee

AUDIO_EXTENSIONS: frozenset[str] = get_audio_extensions()

logger = logging.getLogger(__name__)


MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def extract_date_from_text(text: str) -> tuple[str, str]:
    """Extracts date from filename or string. Returns (date_slash: MM/DD/YYYY, date_hyphen: MM-DD-YYYY)."""
    m = re.search(r"(?:^|[^0-9a-zA-Z])(20\d{2})[-._/](0[1-9]|1[0-2])[-._/](0[1-9]|[12]\d|3[01])(?:$|[^0-9a-zA-Z])", text)
    if m:
        y, mo, d = m.groups()
        return f"{mo}/{d}/{y}", f"{mo}-{d}-{y}"
    m = re.search(r"(?:^|[^0-9a-zA-Z])(0?[1-9]|1[0-2])[-._/](0?[1-9]|[12]\d|3[01])[-._/](20\d{2})(?:$|[^0-9a-zA-Z])", text)
    if m:
        mo, d, y = m.groups()
        return f"{int(mo):02d}/{int(d):02d}/{y}", f"{int(mo):02d}-{int(d):02d}-{y}"
    m_mo = re.search(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+([0-3]?\d)(?:st|nd|rd|th)?,?\s+(20\d{2})\b", text, re.I)
    if m_mo:
        mo_name, d, y = m_mo.groups()
        mo = MONTH_NAMES[mo_name.lower()]
        return f"{mo:02d}/{int(d):02d}/{y}", f"{mo:02d}-{int(d):02d}-{y}"
    now = datetime.now(timezone.utc)
    return f"{now.month:02d}/{now.day:02d}/{now.year}", f"{now.month:02d}-{now.day:02d}-{now.year}"


def migrate_metadata_payload(raw_meta: Dict[str, Any]) -> Dict[str, Any]:
    """Metadata schema version migration dispatcher."""
    if not isinstance(raw_meta, dict):
        return {}
    data = dict(raw_meta)
    if str(data.get("schema_version") or "1.0.0") == "1.0.0":
        if "date" in data and isinstance(data["date"], str):
            data.setdefault("date_slash", extract_date_from_text(data["date"])[0])
            data.setdefault("date_filename", extract_date_from_text(data["date"])[1])
        if "meeting_name" in data and "standardized_slug" not in data:
            data["standardized_slug"] = standardize_meeting_slug(str(data["meeting_name"]), attendees=data.get("attendees"), project_name=data.get("project"))
        if "source_type" not in data:
            is_doc_meta = bool(data.get("is_document") or data.get("output3_only") or data.get("canonical_modality") == "DOCUMENT")
            data["source_type"] = "document" if is_doc_meta else ("AUDIO_ONLY_UNDIARIZED" if data.get("is_audio") else ("copilot_notes" if data.get("notes_mode") else "TRANSCRIPT_DIARIZED"))
        data["schema_version"] = "2.0.0"
        history = list(data.get("migration_history") or [])
        history.append({"from_version": "1.0.0", "to_version": "2.0.0", "migrated_at": datetime.now(timezone.utc).isoformat()})
        data["migration_history"] = history
    return data


def _load_existing_metadata(metadata_path: Optional[Union[str, Path]]) -> Dict[str, Any]:
    """Load JSON metadata file if path provided and exists, applying schema migrations."""
    if not metadata_path or str(metadata_path).lower() == "auto":
        return {}
    p = Path(metadata_path).expanduser().resolve()
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            logger.info(f"Loaded existing metadata from {p}")
            if isinstance(data, dict):
                return migrate_metadata_payload(data)
            return {}
        except Exception as e:
            logger.warning(f"Could not read metadata JSON at {p}: {e}")
    return {}


def _resolve_dates(date_str: Optional[str], meta: Dict[str, Any], stem: str, sample_text: str = "", parent_name: str = "", p: Optional[Path] = None) -> tuple[str, str]:
    """Resolve slash and hyphen date representations."""
    if date_str:
        return extract_date_from_text(date_str)
    if "date" in meta:
        return extract_date_from_text(str(meta["date"]))
    if re.search(r"20\d{2}", stem):
        return extract_date_from_text(stem)
    if parent_name and re.search(r"20\d{2}", parent_name):
        return extract_date_from_text(parent_name)
    if p:
        for parent in p.parents:
            if re.search(r"20\d{2}", parent.name):
                return extract_date_from_text(parent.name)
    if sample_text:
        d_res = extract_date_from_text(sample_text)
        now_dt = datetime.now(timezone.utc)
        if d_res[0] != f"{now_dt.month:02d}/{now_dt.day:02d}/{now_dt.year}":
            return d_res
    return extract_date_from_text(stem)


def _resolve_meeting_name(
    meeting_name: Optional[str],
    meta: Dict[str, Any],
    stem: str,
    project: Optional[str],
    attendees: Optional[Any] = None,
    sample_text: str = "",
) -> str:
    """Derive clean, standardized meeting name from override or filename stem."""
    if meeting_name:
        return meeting_name
    if meta.get("meeting_name"):
        return str(meta["meeting_name"])
    if sample_text:
        m_title = re.search(r"^#\s+([^\n]+)", sample_text.strip())
        if m_title and not is_blacklisted_attendee(m_title.group(1).strip()) and len(m_title.group(1).strip().split()) > 2:
            return standardize_meeting_slug(m_title.group(1).strip(), attendees=attendees or meta.get("attendees"), project_name=project)
    clean_name = re.sub(r"_\d{1,4}_\d{1,2}_\d{2,4}", "", re.sub(r"[-._\s]+", "_", stem).strip("_"))
    return standardize_meeting_slug(clean_name or "Meeting_Audio_Ingest", attendees=attendees or meta.get("attendees"), project_name=project)


def _resolve_project(
    project: Optional[str],
    meta: Dict[str, Any],
    stem: str,
    meeting_name: Optional[str] = None,
    sample_text: str = "",
) -> Optional[str]:
    """Resolve project identifier from CLI, meta, stem hints, or dynamic project router."""
    from dual_pipeline.parameters import resolve_project_name
    from dual_pipeline.query_router_core.project_router import (
        determine_project_directory,
        infer_project_from_keyword_signals,
    )
    if project and project.lower() != "auto":
        return resolve_project_name(project)
    if meta.get("project") and str(meta["project"]).lower() != "auto":
        return resolve_project_name(str(meta["project"]))
    stem_proj = infer_project_from_keyword_signals(stem, check_partner_alliances=True)
    if stem_proj:
        return stem_proj

    try:
        router_meta = dict(meta)
        target_name = meeting_name or meta.get("meeting_name") or meta.get("title")
        if target_name:
            router_meta["meeting_name"] = target_name
        elif stem:
            router_meta["meeting_name"] = stem.replace("_", " ").replace("-", " ")
        cand = determine_project_directory(
            router_meta,
            transcript_snippet=sample_text[:1000] if sample_text else "",
        )
        if cand:
            return cand
    except Exception:
        pass

    return None


def _resolve_time(
    time_str: Optional[str],
    meta: Dict[str, Any],
    sample_text: str = "",
    input_path: Optional[Union[str, Path]] = None,
) -> tuple[str, str]:
    """Resolve meeting time and sanitized time filename."""
    if time_str:
        t_clean = re.sub(r"[:]", "", time_str)
        t_clean = re.sub(r"(\d+)\s+([AP]M)", r"\1\2", t_clean, flags=re.IGNORECASE)
        t_clean = re.sub(r"\s*[-–]\s*", "_", t_clean)
        t_clean = re.sub(r"\s+", "_", t_clean)
        return time_str, t_clean

    from dual_pipeline.temporal_resolution import resolve_temporal_window
    resolved = resolve_temporal_window(input_path or meta.get("input_path"), meta, raw_text=sample_text)
    if resolved and resolved[0] != "TIME_UNRESOLVED":
        return resolved

    t_val = meta.get("time")
    if not t_val:
        raise ValueError(
            "Meeting time could not be determined from input or metadata. "
            "Specify --time (e.g. '03:00 PM - 03:30 PM ET') or include time in metadata."
        )
    t_fn = meta.get("time_filename") or re.sub(r"[:\s\-\u2013]+", "", t_val)
    return t_val, t_fn


def _split_outside_brackets(text: str, delimiter: str = ",") -> List[str]:
    """Split string by delimiter only when not inside brackets/parentheses."""
    parts: List[str] = []
    current: List[str] = []
    depth = 0
    for ch in text:
        if ch in "([{":
            depth += 1
            current.append(ch)
        elif ch in ")]}":
            depth = max(0, depth - 1)
            current.append(ch)
        elif ch == delimiter and depth == 0:
            val = "".join(current).strip()
            if val:
                parts.append(val)
            current = []
        else:
            current.append(ch)
    last = "".join(current).strip()
    if last:
        parts.append(last)
    return parts


def _parse_attendees_input(
    attendees: Optional[Union[str, List[str], Dict[str, List[str]]]]
) -> Optional[Any]:
    """Parse attendees argument from CLI or caller."""
    if not attendees:
        return None
    if isinstance(attendees, str):
        try:
            return json.loads(attendees)
        except Exception:
            if "," in attendees:
                return _split_outside_brackets(attendees, ",")
            if " and " in attendees:
                return [p.strip() for p in attendees.split(" and ") if p.strip()]
            return [attendees.strip()]
    if isinstance(attendees, (list, dict)):
        return attendees
    return None


def _default_attendees_for_project(meta: Dict[str, Any], stem: str) -> Any:
    """Return project-specific default attendees if defined in metadata, else None."""
    return None


def _build_roster_from_attendees(att_obj: Any) -> List[Dict[str, Any]]:
    """Build standardized roster list from attendees dict or list."""
    return build_roster_from_attendees(att_obj)



def _read_sample_and_detect_notes(p: Path) -> tuple[str, bool]:
    suf = p.suffix.lower()
    if not (p.is_file() and suf in (".txt", ".md", ".docx")):
        return "", False
    try:
        if suf == ".docx":
            from dual_pipeline.transcript_pipeline_core.normalizer import _extract_docx_text  # noqa: lazy-decouple
            sample_text = _extract_docx_text(p)
        else:
            sample_text = p.read_text(encoding="utf-8", errors="ignore")
        from dual_pipeline.transcript_pipeline_core.normalizer import is_copilot_notes  # noqa: lazy-decouple
        return sample_text, is_copilot_notes(sample_text)
    except Exception:
        return "", False


def _resolve_notes_mode(
    notes_mode: Optional[bool],
    source_type: Optional[str],
    meta: Dict[str, Any],
    detected_notes: bool,
) -> bool:
    if notes_mode is not None:
        return bool(notes_mode)
    if meta.get("notes_mode") is not None:
        return bool(meta.get("notes_mode"))
    st = str(source_type or meta.get("source_type", "")).lower()
    if st in ("copilot_notes", "notes", "meeting_notes"):
        return True
    return detected_notes


def _apply_notes_mode_defaults(meta: Dict[str, Any], source_type: Optional[str]) -> None:
    meta["notes_mode"] = True
    meta["is_notes"] = True
    meta["source_type"] = source_type or meta.get("source_type") or "copilot_notes"
    meta["skip_output_1"] = True
    meta["outputs"] = ["02", "03"]
    if not meta.get("platform"):
        meta["platform"] = "Copilot Notes"


_DOC_FILENAME_KEYWORDS = (
    "sow", "statement_of_work", "purchase_contract", "agreement", "policy",
    "specification", "memo", "memorandum", "briefing", "whitepaper", "rfc",
    "report", "charter", "architecture", "handbook", "manual", "doc", "document",
    "proposal", "deck", "presentation", "slides", "immersion", "playbook", "roadmap",
)
_DOC_CONTENT_MARKERS = (
    "statement of work", "purchase contract no", "master services agreement", "corporate policy",
    "this document", "strategic memorandum", "architecture briefing", "project charter",
    "scope of work", "executive summary", "project overview", "delivery timeline",
)


def _has_spoken_transcript_markers(text: str) -> bool:
    sample = text[:5000]
    low = sample.lower()
    if "started transcription" in low or "stopped transcription" in low:
        return True
    if re.search(r"^\s*webvtt\b", sample, re.IGNORECASE | re.MULTILINE):
        return True
    if re.search(r"\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3}\s*-->\s*\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3}", sample):
        return True
    has_timestamps = bool(re.search(r"\[\d{1,2}:\d{2}(?::\d{2})?\]", sample))
    teams_turns = bool(re.search(r"^[A-Z][a-zA-Z\s,]{2,30}\s+\d{1,2}:\d{2}(?:\s*[AP]M)?", sample, re.MULTILINE))
    return has_timestamps or teams_turns


def is_document_content(text: str, filename: Optional[str] = None) -> bool:
    """Detect if content is a written document (SOW, contract, spec, email) rather than a spoken transcript."""
    if text and _has_spoken_transcript_markers(text):
        return False

    if filename:
        fn_low = filename.lower()
        ext = Path(fn_low).suffix
        if ext in AUDIO_EXTENSIONS:
            return False
        from dual_pipeline.canonical_pipeline.modalities import is_unambiguous_document_extension
        if ext == ".docx" or is_unambiguous_document_extension(ext):
            return True
        if any(k in fn_low for k in _DOC_FILENAME_KEYWORDS) and not any(
            k in fn_low for k in ("transcript", "audio", "recording", "vtt")
        ):
            return True
    if not text:
        return False
    sample = text[:5000].lower()
    if any(m in sample for m in _DOC_CONTENT_MARKERS):
        has_timestamps = bool(re.search(r"\[\d{2}:\d{2}(?::\d{2})?\]", text[:3000]))
        has_turns = bool(re.search(r"^#{2,4}\s+\*\*[A-Za-z0-9\s\.\,\'\-]+\*\*:\s*", text[:3000], re.MULTILINE))
        if not (has_timestamps and has_turns):
            return True
    return bool(
        "from:" in sample and "subject:" in sample and "date:" in sample and not re.search(r"\[\d{2}:\d{2}\]", text[:2000])
    )


def _extract_speakers_from_sample(sample_text: str) -> List[str]:
    if not sample_text:
        return []
    turn_speakers = extract_speakers_from_dialogue_turns(sample_text)
    if turn_speakers:
        return turn_speakers
    extracted = re.findall(r"^#{2,5}\s+\*\*([A-Za-z0-9\s\.\,\'\-]+?)\*\*\s*$", sample_text, re.MULTILINE)
    seen = set()
    deduped = []
    for s in extracted:
        s_clean = s.strip()
        if s_clean and s_clean not in seen and not is_blacklisted_attendee(s_clean):
            seen.add(s_clean)
            deduped.append(s_clean)
    return deduped


def _resolve_attendees(
    attendees: Any, meta: Dict[str, Any], sample_text: str, stem: str
) -> None:
    parsed_att = _parse_attendees_input(attendees)
    if parsed_att:
        meta["attendees"] = parsed_att
        return
    if meta.get("attendees"):
        return
    header_att = extract_attendees_from_header_block(sample_text)
    if header_att:
        meta["attendees"] = header_att
        return
    speakers = _extract_speakers_from_sample(sample_text)
    att = speakers or _default_attendees_for_project(meta, stem)
    if att:
        meta["attendees"] = att


def _handle_audio_preflight_inference(
    p: Path,
    sample_text: str,
    project: Optional[str],
    meeting_name: Optional[str],
    attendees: Any,
    date_str: Optional[str],
    meta: Dict[str, Any],
) -> tuple[str, Optional[str], Optional[str], Any, Optional[str]]:
    """Infer missing metadata from audio recording snippet using Gemini."""
    if sample_text or (project and meeting_name and attendees):
        return sample_text, project, meeting_name, attendees, date_str
    try:
        import tempfile
        from dual_pipeline.audio_ingestion import (
            LocalMLXAudioTranscriber,
            slice_audio_chunk,
        )
        from dual_pipeline.l1_google_adapter import L1GoogleMetadataAdapter

        logger.info(f"Inferring missing metadata from first 3 minutes of {p.name}")
        with tempfile.TemporaryDirectory(prefix="preflight_audio_") as tmp_dir:
            sliced_path = str(Path(tmp_dir) / "preflight_slice.mp3")
            slice_audio_chunk(str(p), sliced_path, 0.0, duration_sec=180.0)
            transcriber = LocalMLXAudioTranscriber()
            sample_text = transcriber.transcribe(sliced_path)
        if sample_text:
            adapter = L1GoogleMetadataAdapter()
            inferred = adapter.extract_header_metadata(sample_text)
            if inferred.get("meeting_name") and not meeting_name:
                meeting_name = inferred["meeting_name"]
            if inferred.get("project_name") and not project:
                project = inferred["project_name"]
            if (inferred.get("attendee_roster") or inferred.get("attendees")) and not attendees:
                attendees = inferred.get("attendee_roster") or inferred.get("attendees")
                meta["attendee_provenance"] = "llm_inferred"
            if inferred.get("date") and not date_str:
                date_str = inferred["date"]
    except Exception as e:
        logger.warning(f"Metadata AI inference failed: {e}")
    return sample_text, project, meeting_name, attendees, date_str


def _populate_roster_and_grouped_attendees(meta: Dict[str, Any]) -> None:
    """Populate roster and grouped attendees in metadata, preserving existing explicit roster entries."""
    if not meta.get("attendees") and not meta.get("roster"):
        return
    existing_roster = meta.get("roster")
    if not existing_roster or not isinstance(existing_roster, list):
        meta["roster"] = _build_roster_from_attendees(meta.get("attendees"))
    else:
        # Preserve existing roster, merging any missing attendees
        built = _build_roster_from_attendees(meta.get("attendees"))
        existing_names = {str(r.get("name", "")).strip().lower() for r in existing_roster if isinstance(r, dict)}
        for b in built:
            if isinstance(b, dict) and str(b.get("name", "")).strip().lower() not in existing_names:
                existing_roster.append(b)
        meta["roster"] = existing_roster

    if meta.get("attendee_provenance"):
        for r in meta.get("roster", []):
            if isinstance(r, dict):
                r.setdefault("provenance", meta["attendee_provenance"])
    if meta.get("roster"):
        c_grouped: Dict[str, List[str]] = {}
        for r in meta["roster"]:
            if isinstance(r, dict) and r.get("name"):
                r_str = f"{r['name']} ({r['role']})" if r.get("role") and r['role'] != "Participant" else r['name']
                c_grouped.setdefault(r.get("org", "Client / Partner"), []).append(r_str)
        if c_grouped:
            meta["attendees"] = c_grouped


def scaffold_meeting_metadata(
    input_path: Union[str, Path],
    metadata_path: Optional[Union[str, Path]] = None,
    project: Optional[str] = None,
    meeting_name: Optional[str] = None,
    date_str: Optional[str] = None,
    attendees: Optional[Union[str, List[str], Dict[str, List[str]]]] = None,
    time_str: Optional[str] = None,
    classification: str = "confidential",
    notes_mode: Optional[bool] = None,
    source_type: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Builds or completes a governed meeting metadata dictionary.
    If metadata_path exists and is readable, it merges and overlays CLI overrides.
    Otherwise, it auto-scaffolds from the input path, date, and project hints.
    """
    meta = _load_existing_metadata(metadata_path)
    p = Path(input_path).expanduser().resolve()
    if not meta:
        cand_dirs = [p] if p.is_dir() else [p.parent]
        for cd in cand_dirs:
            if (cd / "metadata.json").is_file():
                meta = _load_existing_metadata(cd / "metadata.json")
                if meta:
                    break
    stem = p.stem

    sample_text, detected_notes = _read_sample_and_detect_notes(p)
    if p.suffix.lower() in AUDIO_EXTENSIONS:
        meta["is_document"] = False
        meta["output3_only"] = False
        sample_text, project, meeting_name, attendees, date_str = _handle_audio_preflight_inference(
            p, sample_text, project, meeting_name, attendees, date_str, meta
        )
    elif _resolve_notes_mode(notes_mode, source_type, meta, detected_notes):
        _apply_notes_mode_defaults(meta, source_type)
    elif is_document_content(sample_text, p.name) or meta.get("is_document") or meta.get("output3_only"):
        meta["is_document"] = True
        meta["output3_only"] = True
        meta["source_type"] = "document"

    meta["date"], meta["date_filename"] = _resolve_dates(date_str, meta, stem, sample_text, parent_name=p.parent.name, p=p)
    proj = _resolve_project(project, meta, stem, meeting_name=meeting_name, sample_text=sample_text)
    if proj:
        meta["project"] = proj
    _resolve_attendees(attendees, meta, sample_text, stem)

    meta["meeting_name"] = _resolve_meeting_name(
        meeting_name, meta, stem, project, attendees=meta.get("attendees"), sample_text=sample_text
    )

    meta["time"], meta["time_filename"] = _resolve_time(time_str, meta, sample_text, input_path=p)
    dur = resolve_meeting_duration(meta, sample_text)
    if dur:
        meta["duration_minutes"] = dur
        meta["meeting_duration_minutes"] = dur
        if "Duration:" not in str(meta["time"]):
            meta["time"] = f"{meta['time']} (Duration: ~{dur}m)"

    _populate_roster_and_grouped_attendees(meta)

    meta["classification"] = meta.get("classification", classification)
    if not meta.get("audiences"):
        proj_clean = (meta.get("project") or "project").replace("_", "-")
        meta["audiences"] = ["internal-delivery-team", f"{proj_clean}-stakeholders"]
    if not meta.get("platform"):
        meta["platform"] = "Audio Recording"

    if not meta.get("archetype"):
        from dual_pipeline.prompts.contracts.output_contract import OutputContract
        detected_arch = OutputContract.detect_archetype(
            title=meta.get("meeting_name") or "",
            text=sample_text or "",
        )
        meta["archetype"] = detected_arch.value
    if not meta.get("meeting_type"):
        meta["meeting_type"] = meta.get("archetype")
    meta["meeting_type_hint"] = meta.get("archetype")

    return meta


from dual_pipeline.l0.receipts import sanitize_metadata_for_persistence


def sanitize_loaded_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Cleans up any legacy stringified state machine or circuit breaker objects upon loading."""
    if not isinstance(metadata, dict):
        return {}
    if isinstance(metadata.get("state_machine"), str):
        metadata.pop("state_machine", None)
    if isinstance(metadata.get("circuit_breaker"), str):
        metadata.pop("circuit_breaker", None)
    for k, v in list(metadata.items()):
        if isinstance(v, str) and ("object at 0x" in v and v.startswith("<")):
            metadata.pop(k, None)
    return metadata
