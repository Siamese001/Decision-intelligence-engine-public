"""
dual_pipeline/canonical_pipeline/attachments.py

Helpers for staging deliverable collateral and resuming passing synthesis attempts.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
import shutil
from typing import Any, Dict, Optional, Tuple

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality

logger = logging.getLogger("CanonicalAgenticPipeline.attachments")


def is_deliberate_reset(metadata: Dict[str, Any]) -> bool:
    """Returns True only when a full reset is deliberately requested, not during recovery or selective regeneration."""
    if metadata.get("regenerate") or metadata.get("recovery") or metadata.get("resume") or metadata.get("freeze_output_1"):
        return False
    if metadata.get("force_rerun"):
        return True
    return False


def _load_passing_decision(att_dir: Path) -> Optional[Dict[str, Any]]:
    dec_file = att_dir / "final_release_decision.json"
    if not dec_file.is_file():
        return None
    try:
        dec = json.loads(dec_file.read_text(encoding="utf-8"))
        if dec.get("authoritative_write_authorized") and dec.get("status") in (
            "PASS",
            "APPROVED",
            "PASS_ADVISORY",
            "PASS_AUTHORIZED_EXCEPTION",
        ):
            return dec
    except Exception as exc:
        logger.debug("Failed to parse final_release_decision.json in %s: %s", att_dir, exc)
    return None


def _has_required_deliverable_checkpoints(att_dir: Path, modality: Any) -> bool:
    f3_ok = (att_dir / "output_3.ckpt.json").is_file() or bool(list(att_dir.glob("03_*.json")))
    if not f3_ok:
        return False
    if modality == CanonicalModality.DOCUMENT:
        return True
    if modality == CanonicalModality.MEETING_NOTES:
        f2_ok = (att_dir / "output_2.ckpt.md").is_file() or bool(list(att_dir.glob("02_*.md")))
        return f2_ok
    f1_cands = [p for p in att_dir.glob("01_*.md") if "live_transcript" not in p.name.lower()]
    f1_ok = (att_dir / "output_1.ckpt.md").is_file() or bool(f1_cands)
    f2_ok = (att_dir / "output_2.ckpt.md").is_file() or bool(list(att_dir.glob("02_*.md")))
    return f1_ok and f2_ok


def _build_passing_resumption(
    att_dir: Path, dec: Dict[str, Any], metadata: Dict[str, Any]
) -> Tuple[int, Any, Any]:
    from dual_pipeline.canonical_pipeline.strategies.qa import QAEvaluationResult
    from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult

    f1_cand = [p for p in sorted(att_dir.glob("01_*.md")) if "live_transcript" not in p.name.lower()]
    f1 = f1_cand[0] if f1_cand else att_dir / "output_1.ckpt.md"
    f2_cand = sorted(att_dir.glob("02_*.md"))
    f2 = f2_cand[0] if f2_cand else att_dir / "output_2.ckpt.md"
    f3_cand = sorted(att_dir.glob("03_*.json"))
    f3 = f3_cand[0] if f3_cand else att_dir / "output_3.ckpt.json"
    attempt_num = int(att_dir.name.split("_")[1]) if att_dir.name.split("_")[1].isdigit() else 1

    s_res = SynthesisResult(
        output_1=f1.read_text(encoding="utf-8") if f1.is_file() else None,
        output_2=f2.read_text(encoding="utf-8") if f2.is_file() else None,
        output_3=f3.read_text(encoding="utf-8"),
        attempt=attempt_num,
        metadata=metadata,
    )
    qa_path = att_dir / "qa_gates_report.json"
    jdg_path = att_dir / "llm_judge_evaluation.json"
    qa_rep = json.loads(qa_path.read_text(encoding="utf-8")) if qa_path.is_file() else {}
    jdg_rep = json.loads(jdg_path.read_text(encoding="utf-8")) if jdg_path.is_file() else {}
    e_res = QAEvaluationResult(
        passed=True,
        qa_report=qa_rep,
        judge_report=jdg_rep,
        final_decision=dec,
        authoritative_write_authorized=True,
    )
    return s_res.attempt, s_res, e_res


def _discover_sorted_attempt_dirs(out_p: Path) -> List[Path]:
    attempts_root = out_p / "attempts"
    if not attempts_root.is_dir():
        nested = sorted(list(out_p.glob("**/attempts")), key=lambda p: len(p.parts))
        if not nested:
            return []
        attempts_root = nested[0]
    return sorted(
        [d for d in attempts_root.iterdir() if d.is_dir() and d.name.startswith("attempt_")],
        key=lambda d: int(d.name.split("_")[1]) if d.name.split("_")[1].isdigit() else 0,
        reverse=True,
    )


def _matches_attempt_bindings(
    att_dir: Path, dec: Dict[str, Any], metadata: Dict[str, Any]
) -> bool:
    expected_input_hash = metadata.get("input_hash") or metadata.get("source_hash") or metadata.get("audio_sha256")
    expected_binding = metadata.get("binding_sha256")
    expected_collat = metadata.get("collateral_digest")
    att_meta_f = att_dir / "metadata.json"
    att_meta = json.loads(att_meta_f.read_text(encoding="utf-8")) if att_meta_f.is_file() else {}
    att_src = att_meta.get("input_hash") or att_meta.get("source_hash") or att_meta.get("audio_sha256") or dec.get("input_hash") or dec.get("source_hash")
    if expected_input_hash and (not att_src or att_src != expected_input_hash):
        logger.info("Rejecting attempt %s: input_hash mismatch", att_dir.name)
        return False
    att_b = att_meta.get("binding_sha256") or dec.get("binding_sha256")
    if expected_binding and (not att_b or att_b != expected_binding):
        logger.info("Rejecting attempt %s: binding_sha256 mismatch", att_dir.name)
        return False
    att_c = att_meta.get("collateral_digest") or dec.get("collateral_digest")
    if expected_collat and (not att_c or att_c != expected_collat):
        logger.info("Rejecting attempt %s: collateral_digest mismatch", att_dir.name)
        return False
    return True


def try_resume_passing_attempt(
    out_p: Path, metadata: Dict[str, Any], modality: Any
) -> Optional[Tuple[int, Any, Any]]:
    """Checks if a prior attempt in out_p / attempts passed and can be resumed."""
    if (
        is_deliberate_reset(metadata)
        or metadata.get("speaker_binding_updated_on_resume")
        or (metadata.get("regenerate") and not metadata.get("resume"))
    ):
        return None
    for att_dir in _discover_sorted_attempt_dirs(out_p):
        dec = _load_passing_decision(att_dir)
        if not dec or not _has_required_deliverable_checkpoints(att_dir, modality):
            continue
        if not _matches_attempt_bindings(att_dir, dec, metadata):
            continue
        try:
            logger.info("Resuming from verified passing %s in %s", att_dir.name, out_p)
            return _build_passing_resumption(att_dir, dec, metadata)
        except Exception as resume_err:
            logger.warning("Could not resume passing attempt from %s: %s", att_dir, resume_err)
    return None


def _copy_if_needed(src: Path, dest: Path) -> None:
    if src.is_file() and (not dest.exists() or dest.resolve() != src.resolve()):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def _resolve_attachment_file(att: Any) -> Tuple[Optional[Path], Optional[str]]:
    if isinstance(att, dict):
        src_cand = att.get("staged_path") or att.get("original_path")
        p = Path(src_cand) if (src_cand and Path(src_cand).is_file()) else None
        fname = att.get("filename") or (p.name if p else None)
        return p, fname
    if isinstance(att, (str, Path)):
        p = Path(att)
        return (p, p.name) if p.is_file() else (None, None)
    return None, None


def _copy_from_target_dir(target_dir: Optional[Path], ref_dir: Path) -> None:
    if target_dir and target_dir.is_dir() and (target_dir / "referenced_artifacts").is_dir():
        for existing in (target_dir / "referenced_artifacts").iterdir():
            _copy_if_needed(existing, ref_dir / existing.name)


def stage_deliverable_attachments(
    out_p: Path, meta: Dict[str, Any], target_dir: Optional[Path] = None
) -> None:
    """Stage meeting attachments and collateral files into out_p / 'referenced_artifacts'."""
    ref_dir = out_p / "referenced_artifacts"
    attachments = meta.get("attachments") or meta.get("attachment")
    if attachments:
        att_items = attachments if isinstance(attachments, list) else [attachments]
        for att in att_items:
            src_file, att_filename = _resolve_attachment_file(att)
            if src_file and att_filename:
                _copy_if_needed(src_file, ref_dir / att_filename)
    _copy_from_target_dir(target_dir, ref_dir)
