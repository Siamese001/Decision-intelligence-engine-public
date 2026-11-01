"""
dual_pipeline/canonical_pipeline/evidence_capture.py

Immutable per-run evidence capture and artifact snapshotting.
Guarantees that:
1. Stage deliverables and artifacts (01, 02, 03, intake packages) are immutably
   snapshotted into the run's unique execution directory BEFORE shared presentation
   directories (out_p) can be overwritten by subsequent retries.
2. Source evidence (input paths, hashes, attachments, classifications) and operator
   answers (HITL approvals, entry digests, ledger references) are durably recorded in
   the execution receipt.
3. Two retries cannot overwrite each other's verifiable evidence.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from dual_pipeline.hitl_governance_core.approval_lookup import (
    resolve_hitl_approval_reference,
)

logger = logging.getLogger("EvidenceCapture")


def _compute_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _persist_staging_artifacts(s_dir: Path, artifacts_dest: Path) -> Dict[str, Dict[str, Any]]:
    persisted_artifacts: Dict[str, Dict[str, Any]] = {}
    if s_dir.is_dir():
        for file_p in sorted(s_dir.iterdir()):
            if file_p.is_file():
                fn = file_p.name
                file_hash = _compute_file_sha256(file_p)
                dest_file = artifacts_dest / fn
                shutil.copy2(file_p, dest_file)
                persisted_artifacts[fn] = {
                    "filename": fn,
                    "sha256": file_hash,
                    "size_bytes": file_p.stat().st_size,
                    "persisted_path": str(dest_file),
                }
    return persisted_artifacts


def _build_attachment_record(att: Any, default_class: str) -> Dict[str, Any]:
    if isinstance(att, dict):
        att_p = Path(att.get("path", "")).resolve() if att.get("path") else None
        att_hash = att.get("sha256") or (_compute_file_sha256(att_p) if att_p and att_p.is_file() else "")
        return {
            "filename": att.get("filename") or (att_p.name if att_p else ""),
            "path": str(att_p) if att_p else "",
            "sha256": att_hash,
            "classification": att.get("classification", "CONFIDENTIAL"),
        }
    att_p = Path(att).resolve()
    att_hash = _compute_file_sha256(att_p) if att_p.is_file() else ""
    return {
        "filename": att_p.name,
        "path": str(att_p),
        "sha256": att_hash,
        "classification": default_class,
    }


def _build_source_evidence(tracker: Any, meta: Dict[str, Any], modality: Optional[Any]) -> Dict[str, Any]:
    attachments_record: List[Dict[str, Any]] = []
    raw_attachments = meta.get("attachments") or []
    def_class = meta.get("source_classification", "CONFIDENTIAL")
    if isinstance(raw_attachments, (list, tuple)):
        for att in raw_attachments:
            attachments_record.append(_build_attachment_record(att, def_class))
    return {
        "input_path": str(getattr(tracker, "input_path", "")),
        "input_hash": str(getattr(tracker, "input_hash", "")),
        "source_modality": str(modality or meta.get("modality", "unknown")),
        "raw_source_sha256": str(meta.get("raw_source_sha256") or ""),
        "source_classification": def_class,
        "attachments": attachments_record,
    }


def _build_operator_answers(meta: Dict[str, Any]) -> List[Dict[str, Any]]:
    approval_candidates: List[Any] = []
    for k in ("hitl_approval_reference", "attendee_approval", "approval_event_id", "auth_event_id"):
        if meta.get(k):
            approval_candidates.append(meta[k])
    if meta.get("hitl_approval_references"):
        approval_candidates.extend(meta["hitl_approval_references"])

    expected_subject = meta.get("subject_sha256")
    operator_answers: List[Dict[str, Any]] = []
    for cand in approval_candidates:
        res = resolve_hitl_approval_reference(
            ref=cand,
            expected_subject_sha256=expected_subject,
            default_ledger_path=meta.get("ledger_path"),
        )
        operator_answers.append(res.to_dict())
    return operator_answers


def snapshot_run_artifacts(
    tracker: Any,
    staging_dir: Union[str, Path],
    evidence_dir: Union[str, Path],
    meta: Dict[str, Any],
    modality: Optional[Any] = None,
) -> Dict[str, Any]:
    """Snapshots artifacts from staging to immutable per-run execution directory.

    Computes SHA-256 for all artifacts, records source/answer provenance, and
    attaches evidence records to tracker metadata before promotion to shared output.
    """
    s_dir = Path(staging_dir).resolve()
    e_dir = Path(evidence_dir).resolve()
    artifacts_dest = e_dir / "artifacts"
    artifacts_dest.mkdir(parents=True, exist_ok=True)

    persisted = _persist_staging_artifacts(s_dir, artifacts_dest)
    tracker.metadata["persisted_artifacts"] = persisted
    tracker.metadata["persisted_artifacts_dir"] = str(artifacts_dest)
    tracker.metadata["source_evidence"] = _build_source_evidence(tracker, meta, modality)
    tracker.metadata["operator_answers"] = _build_operator_answers(meta)
    return persisted


def verify_run_evidence_integrity(receipt_path: Union[str, Path]) -> Tuple[bool, List[str]]:
    """Verifies that per-run evidence and persisted artifacts match receipt digests."""
    rc_p = Path(receipt_path).resolve()
    if not rc_p.is_file():
        return False, [f"Receipt file not found: {rc_p}"]

    errors: List[str] = []
    try:
        data = json.loads(rc_p.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, [f"Failed to parse receipt JSON: {exc}"]

    persisted_dir_str = data.get("persisted_artifacts_dir") or data.get("metadata", {}).get("persisted_artifacts_dir")
    artifacts = data.get("artifacts") or data.get("metadata", {}).get("persisted_artifacts") or {}

    if not artifacts:
        # Check if run had no artifacts (e.g. early failure)
        return True, []

    if not persisted_dir_str or not Path(persisted_dir_str).is_dir():
        errors.append(f"Persisted artifacts directory missing or not a directory: {persisted_dir_str}")
        return False, errors

    persisted_dir = Path(persisted_dir_str)
    for fn, art_info in artifacts.items():
        art_path = persisted_dir / fn
        if not art_path.is_file():
            errors.append(f"Persisted artifact '{fn}' missing at {art_path}")
            continue
        actual_hash = _compute_file_sha256(art_path)
        expected_hash = art_info.get("sha256")
        if expected_hash and actual_hash != expected_hash:
            errors.append(f"Artifact '{fn}' digest mismatch: actual '{actual_hash}' != expected '{expected_hash}'")

    return len(errors) == 0, errors
