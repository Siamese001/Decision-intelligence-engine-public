"""
Container and transcript cache validation for output hierarchy governance.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List

from .constants import (
    CONTROLLED_SUPPORTING_DIRS,
    CONTROLLED_SUPPORTING_FILES,
    LEGACY_UNSCOPED_MULTI_MEETING_DATES,
    PROHIBITED_CACHE_RECEIPTS,
    PROHIBITED_EXTENSIONS,
)
from .helpers import _extract_date_token, _extract_stem, _validate_project_affinity


def validate_transcript_cache(cache_dir: Path) -> List[str]:
    """Ensures output/transcript_cache/ is never used for final deliverables or receipts."""
    violations: List[str] = []
    if not cache_dir.is_dir():
        return violations
    for root, _, files in os.walk(cache_dir):
        for f in sorted(files):
            if f.startswith("."):
                continue
            is_deliv = any(
                f.endswith(s)
                for s in (
                    "_hitl_resolved_transcript.md",
                    "_executive_recap.md",
                    "_canonical_signal_projection.json",
                )
            ) or (f[:3] in ("01_", "02_", "03_") and f.endswith((".md", ".json")))
            is_receipt = f in PROHIBITED_CACHE_RECEIPTS or ("intake_package" in f and f.endswith(".json"))
            if is_deliv or is_receipt:
                kind = "deliverable" if is_deliv else "governance receipt"
                violations.append(
                    f"[TRANSCRIPT_CACHE_FINAL_OUTPUT_POLLUTION] Prohibited final {kind} '{f}' "
                    f"in transcript_cache at '{Path(root) / f}'."
                )
    return violations


def _classify_container_item(
    item: Path,
    proj_name: str,
    meeting_date: str,
    container_label: str,
    art01_files: List[str],
    art02_files: List[str],
    art03_files: List[str],
    receipts_found: Dict[str, bool],
) -> List[str]:
    """Classifies an individual file or subdirectory within a meeting container."""
    violations: List[str] = []
    if item.name == ".DS_Store":
        return violations
    if item.name.startswith("."):
        violations.append(f"[PROHIBITED_HIDDEN_RESIDUE] Prohibited hidden item '{item.name}' in '{container_label}'.")
        return violations
    if item.is_symlink():
        violations.append(f"[MEETING_SYMLINK] Disallowed symlink in '{container_label}': {item.name}")
        return violations
    if item.is_dir():
        if item.name.startswith("archive_"):
            violations.append(f"[IN_PLACE_ARCHIVE] Prohibited in-place archive '{item.name}' in '{container_label}'.")
        elif item.name == "chunks":
            violations.append(f"[PROHIBITED_CHUNKS_DIR] Prohibited chunks directory in '{container_label}'.")
        elif item.name not in CONTROLLED_SUPPORTING_DIRS and f"{proj_name}/{meeting_date}" not in LEGACY_UNSCOPED_MULTI_MEETING_DATES:
            violations.append(f"[UNEXPECTED_SUBDIRECTORY] Unexpected subdirectory '{item.name}' in '{container_label}'.")
        return violations

    name, suffix = item.name, item.suffix.lower()
    if suffix in PROHIBITED_EXTENSIONS and name not in CONTROLLED_SUPPORTING_FILES:
        violations.append(f"[PROHIBITED_FILE_RESIDUE] Prohibited residue '{name}' ({suffix}) in '{container_label}'.")
        return violations
    if name.endswith("_cache.json") or name == "accurate_transcript_cache.json":
        violations.append(f"[CACHE_RESIDUE] Prohibited scratch cache '{name}' in '{container_label}'.")
        return violations
    if any(name.endswith(s) for s in ("_QA_gate_report.md", "_judge_evaluation.md", "_release_decision.md")):
        violations.append(f"[NONCANONICAL_MD_REPORT] Prohibited Markdown governance report '{name}' in '{container_label}'.")
        return violations

    if name.endswith("_live_transcript.md"):
        try:
            first_line = ""
            with item.open(encoding="utf-8") as f:
                first_line = f.readline().strip()
            if not first_line.startswith("# Live Meeting Transcript:"):
                violations.append(
                    f"[INVALID_LIVE_TRANSCRIPT_HEADER] Live transcript '{name}' in '{container_label}' "
                    f"must begin with '# Live Meeting Transcript:'. Found: '{first_line[:60]}'"
                )
            elif " | " in first_line:
                header_date = first_line.split(" | ", 1)[1].strip()
                expected_date = meeting_date.replace("-", "/")
                if header_date != expected_date:
                    violations.append(
                        f"[LIVE_TRANSCRIPT_DATE_MISMATCH] Live transcript '{name}' in '{container_label}' "
                        f"header date '{header_date}' does not match container date '{expected_date}'."
                    )
        except Exception as ex:
            violations.append(f"[CORRUPT_LIVE_TRANSCRIPT] Failed to read '{name}' in '{container_label}': {ex}")
    elif name.endswith(("_speaker_map.json", "_internal_advisory_enclave.json", ".enclave.json")):
        pass
    elif name.startswith("01_") and (name.endswith("_hitl_resolved_transcript.md") or name.endswith("_Transcript.md") or name.endswith("_transcript.md")):
        art01_files.append(name)
    elif name.startswith("02_") and (name.endswith("_executive_recap.md") or name.endswith("_Recap.md") or name.endswith("_recap.md") or name.endswith(".md")):
        art02_files.append(name)
    elif name.startswith("03_") and (name.endswith("_canonical_signal_projection.json") or name.endswith("_Signals.json") or name.endswith("_signals.json") or name.endswith(".json")):
        art03_files.append(name)
    elif name in ("qa_gates_report.json", "llm_judge_evaluation.json", "final_release_decision.json"):
        receipt_map = {"qa_gates_report.json": "qa", "llm_judge_evaluation.json": "judge", "final_release_decision.json": "release"}
        try:
            content = item.read_text(encoding="utf-8").strip()
            if len(content) <= 10 or content in ("{}", "null", "[]"):
                violations.append(f"[EMPTY_RECEIPT_FILE] Required receipt '{name}' in '{container_label}' is an empty or trivial placeholder.")
            else:
                receipts_found[receipt_map[name]] = True
        except Exception as ex:
            violations.append(f"[CORRUPT_RECEIPT_FILE] Failed to read receipt '{name}' in '{container_label}': {ex}")
    elif ("intake_package" in name and name.endswith(".json") and name != "intake_package_result.json") or name.endswith("_intake_package.json"):
        try:
            content = item.read_text(encoding="utf-8").strip()
            if len(content) <= 10 or content in ("{}", "null", "[]"):
                violations.append(f"[EMPTY_RECEIPT_FILE] Intake receipt '{name}' in '{container_label}' is an empty or trivial placeholder.")
            else:
                receipts_found["intake"] = True
        except Exception as ex:
            violations.append(f"[CORRUPT_RECEIPT_FILE] Failed to read intake receipt '{name}' in '{container_label}': {ex}")
    elif name in CONTROLLED_SUPPORTING_FILES or name.endswith(("_source.txt", "_source.md", ".intake.json", ".intake.md", "_speaker_map.json", "_ingestion_plan.json")):
        pass
    else:
        violations.append(f"[UNKNOWN_FILE] Unrecognized file '{name}' in '{container_label}'.")

    return violations


def _detect_modes(container: Path) -> tuple[bool, bool]:
    """Detects (is_doc_mode, is_notes_mode) from container metadata or source documents."""
    is_doc_mode, is_notes_mode = False, False
    meta_p = container / "metadata.json"
    if meta_p.exists():
        try:
            mdata = json.loads(meta_p.read_text(encoding="utf-8"))
            is_doc_mode = bool(mdata.get("is_document") or mdata.get("output3_only") or mdata.get("source_type") == "document")
            is_notes_mode = bool(
                mdata.get("notes_mode")
                or mdata.get("is_notes")
                or mdata.get("source_type") in ("copilot_notes", "meeting_notes", "notes")
                or mdata.get("outputs") == ["02", "03"]
            )
        except Exception:
            pass
    if not is_doc_mode and not is_notes_mode:
        is_doc_mode = any((container / fn).exists() for fn in ("source_document.txt", "source_document.md")) or any(
            (container / fn).exists() and '"mode": "document"' in (container / fn).read_text(encoding="utf-8", errors="ignore")
            for fn in ("final_release_decision.json", "qa_gates_report.json", "llm_judge_evaluation.json")
        )
    return is_doc_mode, is_notes_mode


def _validate_meeting_container(
    container: Path,
    proj_name: str,
    meeting_date: str,
    container_label: str,
    allow_active_recording: bool = False,
) -> List[str]:
    """Validates an isolated meeting directory container."""
    violations: List[str] = []

    # Active recording check
    live_transcripts = list(container.glob("*_live_transcript.md"))
    if live_transcripts:
        is_active = any(
            any(k in lt.read_text(encoding="utf-8", errors="ignore") for k in ("RECORDING_IN_PROGRESS", "Session paused", "Session completed"))
            for lt in live_transcripts
        )
        if is_active and not list(container.glob("01_*_hitl_resolved_transcript.md")):
            if allow_active_recording:
                return violations
            return [f"[ACTIVE_RECORDING_INCOMPLETE] Meeting container '{container_label}' has active recording in progress."]

    art01_files: List[str] = []
    art02_files: List[str] = []
    art03_files: List[str] = []
    receipts_found = {"qa": False, "judge": False, "release": False, "intake": False}

    for item in sorted(container.iterdir()):
        violations.extend(
            _classify_container_item(
                item, proj_name, meeting_date, container_label,
                art01_files, art02_files, art03_files, receipts_found,
            )
        )

    stems_by_kind: Dict[str, Dict[str, str]] = {"01": {}, "02": {}, "03": {}}
    for kind, f_list in (("01", art01_files), ("02", art02_files), ("03", art03_files)):
        for f in f_list:
            stem = _extract_stem(f)
            if stem in stems_by_kind[kind]:
                violations.append(
                    f"[DUPLICATE_DELIVERABLE] Multiple Deliverable {kind} versions for meeting '{stem}' "
                    f"in '{container_label}': {[stems_by_kind[kind][stem], f]}."
                )
            stems_by_kind[kind][stem] = f

    stems_01, stems_02, stems_03 = stems_by_kind["01"], stems_by_kind["02"], stems_by_kind["03"]
    is_doc_mode, is_notes_mode = _detect_modes(container)

    if is_doc_mode:
        for art_k, art_list in (("01", art01_files), ("02", art02_files)):
            if art_list:
                violations.append(f"[PROHIBITED_DOCUMENT_DELIVERABLE] Deliverable {art_k} prohibited in document container '{container_label}': {art_list}.")
        all_stems = set(stems_03.keys())
        kinds_to_check = [("03", stems_03, "03")]
    elif is_notes_mode:
        if not art02_files:
            violations.append(f"[MISSING_DELIVERABLE] Missing Deliverable 02 in '{container_label}'.")
        all_stems = set(stems_02.keys()) | set(stems_03.keys())
        kinds_to_check = [("02", stems_02, "02"), ("03", stems_03, "03")]
    else:
        if not art01_files:
            violations.append(f"[MISSING_DELIVERABLE] Missing Deliverable 01 in '{container_label}'.")
        if not art02_files:
            violations.append(f"[MISSING_DELIVERABLE] Missing Deliverable 02 in '{container_label}'.")
        all_stems = set(stems_01.keys()) | set(stems_02.keys()) | set(stems_03.keys())
        kinds_to_check = [("01", stems_01, "01"), ("02", stems_02, "02"), ("03", stems_03, "03")]

    if not art03_files:
        violations.append(f"[MISSING_DELIVERABLE] Missing Deliverable 03 in '{container_label}'.")

    norm_mdate = _extract_date_token(meeting_date) or meeting_date
    for stem in sorted(all_stems):
        stem_date = _extract_date_token(stem)
        if meeting_date not in stem and norm_mdate not in stem and (stem_date is None or stem_date != norm_mdate):
            violations.append(f"[NONCONFORMING_FILENAME_DATE] Deliverable stem '{stem}' does not embed meeting date '{meeting_date}'.")
        for _kind_id, s_map, d_label in kinds_to_check:
            if stem not in s_map:
                violations.append(f"[MISSING_DELIVERABLE] Missing Deliverable {d_label} for session '{stem}' in '{container_label}'.")
        aff_err = _validate_project_affinity(stem, proj_name, container_label)
        if aff_err:
            violations.append(aff_err)

    for r_key, r_name in (("qa", "qa_gates_report.json"), ("judge", "llm_judge_evaluation.json"), ("release", "final_release_decision.json")):
        if not receipts_found[r_key]:
            violations.append(f"[MISSING_RECEIPT] Missing {r_name} in '{container_label}'.")

    if not receipts_found["intake"]:
        rel_p, is_auth = container / "final_release_decision.json", True
        if rel_p.exists():
            try:
                rel_data = json.loads(rel_p.read_text(encoding="utf-8"))
                is_auth = bool(rel_data.get("authoritative_write_authorized", False)) and rel_data.get("status") == "PASS"
            except Exception:
                is_auth = False
        if is_auth:
            violations.append(f"[MISSING_RECEIPT] Missing intake_package.json in '{container_label}'.")

    if is_doc_mode and art03_files:
        for f03 in art03_files:
            p03 = container / f03
            try:
                d03 = json.loads(p03.read_text(encoding="utf-8"))
                gm = d03.get("graph_mutations") or {} if isinstance(d03, dict) else {}
                mut_total = sum(len(gm.get(k) or []) for k in ("entity_upserts", "relationship_signals", "decision_updates", "proposal_updates", "risk_updates", "strategic_intent_signals", "metrics")) if isinstance(gm, dict) else 0
                if mut_total == 0:
                    violations.append(f"[VACUOUS_DOCUMENT_PROJECTION] Deliverable 03 '{f03}' in '{container_label}' has 0 graph mutations.")
            except Exception as ex:
                violations.append(f"[CORRUPT_DELIVERABLE_03] Failed to parse '{f03}' in '{container_label}': {ex}")

    return violations
