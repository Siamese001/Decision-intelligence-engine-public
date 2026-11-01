"""
Attachment discovery, classification, and staging engine.
"""
from __future__ import annotations

import hashlib
import logging
import mimetypes
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple, Union

from dual_pipeline.source_classification.capability_registry import (
    get_diagram_extensions,
    get_document_extensions,
    get_image_extensions,
    get_presentation_extensions,
    get_spreadsheet_extensions,
)
from .types import AttachmentRecord, AttachmentType

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS: Set[str] = set(get_image_extensions())
DIAGRAM_EXTENSIONS: Set[str] = set(get_diagram_extensions())
PRESENTATION_EXTENSIONS: Set[str] = set(get_presentation_extensions())
SPREADSHEET_EXTENSIONS: Set[str] = set(get_spreadsheet_extensions())
DOCUMENT_EXTENSIONS: Set[str] = (
    set(get_document_extensions())
    - PRESENTATION_EXTENSIONS
    - SPREADSHEET_EXTENSIONS
    - DIAGRAM_EXTENSIONS
    | {".txt", ".md", ".text", ".markdown", ".doc"}
)

ALL_ATTACHMENT_EXTENSIONS = (
    IMAGE_EXTENSIONS
    | DIAGRAM_EXTENSIONS
    | PRESENTATION_EXTENSIONS
    | DOCUMENT_EXTENSIONS
    | SPREADSHEET_EXTENSIONS
)

IGNORED_FILENAMES: Set[str] = {
    ".DS_Store", "qa_gates_report.json", "llm_judge_evaluation.json",
    "final_release_decision.json", "intake_package.json", "metadata.json",
    "source_document.txt", "pipeline_execution_receipt.json", "speaker_hints.json",
    "speaker_hints_queue.json", "speaker_hint_conflicts.jsonl", "speaker_map.json",
    "copilot_answers.jsonl", "copilot_attempts.jsonl", "judge_failure_diagnostics.json",
    "collateral_drop_log.json", "graph_mutation_breakdown.md",
    "publication_coverage_receipt.json", "graph_delta_preview.json",
    "graph_delta_preview.md", "backtest_promotion_receipt.json",
    "stage_seals.json", "execution_receipt.json", "entity_resolutions.json",
}


def classify_attachment_type(path: Union[str, Path]) -> AttachmentType:
    """Classifies a file path into a governed AttachmentType enum."""
    suffix = Path(path).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return AttachmentType.IMAGE
    if suffix in DIAGRAM_EXTENSIONS:
        return AttachmentType.DIAGRAM
    if suffix in PRESENTATION_EXTENSIONS:
        return AttachmentType.PRESENTATION
    if suffix in SPREADSHEET_EXTENSIONS:
        return AttachmentType.SPREADSHEET
    if suffix in DOCUMENT_EXTENSIONS:
        return AttachmentType.DOCUMENT
    return AttachmentType.UNKNOWN


def compute_sha256(file_path: Path) -> str:
    """Computes SHA-256 digest of a local file."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def _is_blacklisted_artifact(name_lower: str) -> bool:
    if name_lower.startswith(("00_", "01_", "02_", "03_", "output_")) or ".ckpt." in name_lower:
        return True
    if name_lower.endswith((
        "_receipt.json", "_report.json", "_manifest.json", "_summary.md", "_ledger.json", ".log",
        ".cache_seal.json", ".segments.json", ".transcript_cache.json", ".utterances.json",
        ".voiceprints.json", ".raw_asr.json", "_diarization_tracks.json",
    )):
        return True
    if any(tok in name_lower for tok in ("hitl_decision_ledger", "test_report", "junit", "speaker_hint")):
        return True
    return name_lower.endswith("_speaker_map.json") or name_lower == "speaker_map.json"


def _is_valid_attachment_file(file_path: Path, transcript_path: Optional[Path] = None) -> bool:
    """Checks if a file is a candidate meeting attachment."""
    if not file_path.is_file():
        return False
    if "transcript_cache" in file_path.parts:
        return False
    name_lower = file_path.name.lower()
    if name_lower.startswith(".") or file_path.name in IGNORED_FILENAMES or name_lower in IGNORED_FILENAMES:
        return False
    if transcript_path and file_path.resolve() == transcript_path.resolve():
        return False
    if file_path.suffix.lower() in {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}:
        return False
    if _is_blacklisted_artifact(name_lower):
        return False
    if file_path.suffix.lower() in {".txt", ".md"}:
        if any(tok in name_lower for tok in ("_transcript", "transcript_", "live_transcript", "-transcript")):
            return False
    return file_path.suffix.lower() in ALL_ATTACHMENT_EXTENSIONS


def _scan_directory_for_attachments(
    directory: Path,
    trans_p: Optional[Path],
    seen: Set[Path],
    candidates: List[Path],
) -> None:
    """Scans a directory for valid attachment files and appends them to candidates."""
    if not directory.is_dir():
        return
    for sub_p in sorted(directory.glob("*")):
        res = sub_p.resolve()
        if res not in seen and _is_valid_attachment_file(res, trans_p):
            candidates.append(res)
            seen.add(res)


def _find_fallback_attachment(
    p: Path, trans_p: Optional[Path], meeting_output_dir: Optional[Union[str, Path]]
) -> Optional[Path]:
    alt_dirs = ([trans_p.parent / "referenced_artifacts", trans_p.parent / "attachments", trans_p.parent] if (trans_p and trans_p.parent.is_dir()) else []) + (
        [Path(meeting_output_dir) / "referenced_artifacts", Path(meeting_output_dir) / "attachments", Path(meeting_output_dir)] if meeting_output_dir else []
    )
    for ad in alt_dirs:
        cand_f = ad / p.name
        if cand_f.is_file() and _is_valid_attachment_file(cand_f, trans_p):
            return cand_f.resolve()
    return None


def _extract_path_candidate(raw: Union[str, Path, Mapping[str, Any]]) -> Optional[Path]:
    if not raw:
        return None
    if isinstance(raw, Mapping):
        cand = raw.get("staged_path") or raw.get("original_path") or raw.get("path")
        return Path(cand) if cand else None
    return Path(raw)


def _collect_from_explicit_paths(
    attachment_paths: Iterable[Union[str, Path, Mapping[str, Any]]],
    trans_p: Optional[Path],
    seen: Set[Path],
    candidates: List[Path],
    meeting_output_dir: Optional[Union[str, Path]] = None,
) -> None:
    paths = [attachment_paths] if isinstance(attachment_paths, (str, Path, Mapping)) else attachment_paths
    for raw in paths:
        p = _extract_path_candidate(raw)
        if not p:
            continue
        p_res = p.resolve()
        if p.is_file() and p_res not in seen and _is_valid_attachment_file(p, trans_p):
            candidates.append(p_res)
            seen.add(p_res)
            continue
        fb = _find_fallback_attachment(p, trans_p, meeting_output_dir)
        if fb and fb not in seen:
            candidates.append(fb)
            seen.add(fb)
        elif p.is_dir():
            _scan_directory_for_attachments(p_res, trans_p, seen, candidates)


def _scan_scoped_hierarchy_subdirs(
    base_dir: Path, trans_p: Optional[Path], seen: Set[Path], candidates: List[Path]
) -> None:
    ancestors = [base_dir]
    if base_dir.parent.is_dir():
        ancestors.extend([base_dir.parent] + ([base_dir.parent.parent] if base_dir.parent.parent.is_dir() else []))

    for anc in ancestors:
        for sub in ("referenced_artifacts", "attachments", "artifacts", "interim_artifacts", "copilot_artifacts"):
            _scan_directory_for_attachments(anc / sub, trans_p, seen, candidates)
    _scan_directory_for_attachments(base_dir, trans_p, seen, candidates)


def discover_attachments(
    attachment_paths: Optional[Iterable[Union[str, Path]]] = None,
    attachment_dir: Optional[Union[str, Path]] = None,
    meeting_output_dir: Optional[Union[str, Path]] = None,
    transcript_path: Optional[Union[str, Path]] = None,
) -> List[Path]:
    """
    Discovers candidate attachment files across multiple potential ingress sources:
    1. Explicit caller-supplied paths
    2. Explicit attachment directory
    3. Existing referenced_artifacts/ folder in meeting output directory or transcript folder
    4. Sibling files in the transcript folder
    """
    candidates: List[Path] = []
    seen: Set[Path] = set()
    trans_p = Path(transcript_path).resolve() if transcript_path else None

    if attachment_paths:
        _collect_from_explicit_paths(attachment_paths, trans_p, seen, candidates, meeting_output_dir=meeting_output_dir)
        if candidates:
            return candidates

    if attachment_dir:
        _scan_directory_for_attachments(Path(attachment_dir).resolve(), trans_p, seen, candidates)

    if meeting_output_dir:
        _scan_scoped_hierarchy_subdirs(Path(meeting_output_dir).resolve(), trans_p, seen, candidates)

    if trans_p and trans_p.parent.is_dir():
        tp_dir = trans_p.parent.resolve()
        shared_containers = frozenset({
            "intake", "inputs", "dual_pipeline", "tests", "reports", "output",
            "transcripts", "raw_transcripts", "transcript_cache", "audio",
            "recordings", "sessions", ".local",
        })
        if (
            tp_dir.name not in shared_containers
            and tp_dir.parent.name not in ("inputs", "intake", "output")
        ):
            _scan_scoped_hierarchy_subdirs(tp_dir, trans_p, seen, candidates)

    # In-flight zero-touch discovery from current working tree
    cwd = Path.cwd().resolve()
    for sub in ("referenced_artifacts", "output/referenced_artifacts"):
        p_sub = cwd / sub
        if p_sub.is_dir():
            _scan_directory_for_attachments(p_sub, trans_p, seen, candidates)

    return candidates



def _find_drop_log_path(out_dir: Path, target_ref_dir: Path) -> Optional[Path]:
    search_dirs = [out_dir, target_ref_dir, target_ref_dir.parent, out_dir.parent] + ([out_dir.parent.parent] if out_dir.parent and out_dir.parent.parent else [])
    for candidate_dir in search_dirs:
        p = candidate_dir / "collateral_drop_log.json"
        if p.is_file():
            return p
    return None


def _sync_drop_log_to_out_dir(drop_log_path: Path, out_dir: Path) -> None:
    target_local_log = out_dir / "collateral_drop_log.json"
    if drop_log_path.resolve() != target_local_log.resolve():
        try:
            shutil.copy2(drop_log_path, target_local_log)
        except Exception as exc:
            logger.debug("Collateral drop log sync skipped for %s: %s", target_local_log, exc)


def _load_drop_log(out_dir: Path, target_ref_dir: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    import json as _json

    drop_log_path = _find_drop_log_path(out_dir, target_ref_dir)
    if not drop_log_path:
        return {}, {}
    _sync_drop_log_to_out_dir(drop_log_path, out_dir)
    try:
        entries = _json.loads(drop_log_path.read_text(encoding="utf-8"))
        if isinstance(entries, dict):
            entries = entries.get("drops") or entries.get("entries") or []
    except Exception:
        return {}, {}

    valid = [e for e in entries if isinstance(e, dict) and str(e.get("filename") or "").lower() not in IGNORED_FILENAMES]
    drop_log_by_sha = {e["sha256"]: e for e in valid if e.get("sha256")}
    drop_log_by_filename = {str(e["filename"]): e for e in valid if e.get("filename")}
    return drop_log_by_sha, drop_log_by_filename


def _stage_candidate_attachment(
    p: Path,
    target_ref_dir: Path,
    drop_log_by_sha: Dict[str, Any],
    drop_log_by_filename: Dict[str, Any],
    idx: int,
) -> AttachmentRecord:
    filename = p.name
    dest_path = (target_ref_dir / filename).resolve()
    if p.resolve() != dest_path:
        shutil.copy2(p, dest_path)

    sha = compute_sha256(p)
    att_type = classify_attachment_type(filename)
    mime, _ = mimetypes.guess_type(filename)
    clean_id = f"att-{idx:02d}-{p.stem[:30]}"

    drop_entry = drop_log_by_sha.get(sha) or drop_log_by_filename.get(filename)
    from .review_provenance import is_authenticated_drop
    if drop_entry and is_authenticated_drop(drop_entry):
        attribution_mode = drop_entry.get("attribution_mode", "COLLATERAL_REVIEWED")
        dropped_at_elapsed = drop_entry.get("dropped_at_elapsed")
    else:
        attribution_mode = "BACKGROUND"
        dropped_at_elapsed = drop_entry.get("dropped_at_elapsed") if drop_entry else None

    return AttachmentRecord(
        attachment_id=clean_id,
        original_path=p.resolve(),
        staged_path=dest_path,
        attachment_type=att_type,
        filename=filename,
        file_size_bytes=dest_path.stat().st_size,
        sha256=sha,
        relative_staged_path=f"referenced_artifacts/{filename}",
        mime_type=mime,
        attribution_mode=attribution_mode,
        dropped_at_elapsed=dropped_at_elapsed,
    )


def _append_reviewed_records_to_drops(records: List[AttachmentRecord], existing_drops: List[Dict[str, Any]]) -> bool:
    from .review_provenance import is_authenticated_drop

    existing_shas = {d.get("sha256") for d in existing_drops if d.get("sha256")}
    added = False
    for rec in records:
        if rec.attribution_mode == "COLLATERAL_REVIEWED" and rec.sha256 not in existing_shas and is_authenticated_drop(rec.to_dict()):
            existing_drops.append({
                "filename": rec.filename,
                "path": rec.relative_staged_path,
                "dropped_at_elapsed": rec.dropped_at_elapsed,
                "sha256": rec.sha256,
                "attribution_mode": "COLLATERAL_REVIEWED",
            })
            existing_shas.add(rec.sha256)
            added = True
    return added


def _sync_collateral_drop_log(records: List[AttachmentRecord], out_dir: Path) -> None:
    import json as _json

    drop_log_path = out_dir / "collateral_drop_log.json"
    if not records or not drop_log_path.is_file():
        return
    try:
        raw_drops = _json.loads(drop_log_path.read_text(encoding="utf-8"))
        if isinstance(raw_drops, dict):
            raw_drops = raw_drops.get("drops") or raw_drops.get("entries") or []
    except Exception:
        raw_drops = []

    existing_drops = [d for d in raw_drops if isinstance(d, dict) and str(d.get("filename") or "").lower() not in IGNORED_FILENAMES]
    changed = len(existing_drops) != len(raw_drops) or _append_reviewed_records_to_drops(records, existing_drops)
    if changed:
        try:
            drop_log_path.write_text(_json.dumps(existing_drops, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to write collateral_drop_log.json: %s", e)


def stage_attachments_into_output(
    candidate_paths: Iterable[Path],
    meeting_output_dir: Union[str, Path],
) -> List[AttachmentRecord]:
    """
    Stages discovered candidate files into the meeting output directory's
    governed `referenced_artifacts/` subfolder, ensuring directory hierarchy
    governance compliance.

    Also reads `collateral_drop_log.json` from the transcript directory to stamp
    attribution_mode="COLLATERAL_REVIEWED" and dropped_at_elapsed onto matching
    records by SHA-256.
    """
    out_dir = Path(meeting_output_dir).resolve()
    target_ref_dir = out_dir / "referenced_artifacts"
    target_ref_dir.mkdir(parents=True, exist_ok=True)

    drop_log_by_sha, drop_log_by_filename = _load_drop_log(out_dir, target_ref_dir)

    records: List[AttachmentRecord] = []
    seen_shas: Set[str] = set()

    for p in sorted(candidate_paths):
        if not p.is_file():
            continue
        sha = compute_sha256(p)
        if sha in seen_shas:
            continue
        seen_shas.add(sha)
        records.append(
            _stage_candidate_attachment(
                p, target_ref_dir, drop_log_by_sha, drop_log_by_filename, len(records) + 1
            )
        )

    _sync_collateral_drop_log(records, out_dir)
    return records

