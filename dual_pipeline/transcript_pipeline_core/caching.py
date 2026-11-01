"""
caching.py — Audio transcription caching and pre-execution idempotency utilities.

Guarantees:
1. Pre-execution SHA-256 fingerprinting of input files (audio and text).
2. Transcript caching in output/transcript_cache/<audio_sha256>.txt to prevent redundant ASR.
3. Fast-path zero-token idempotency gate to skip unchanged inputs without token spend.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Union

logger = logging.getLogger(__name__)

DEFAULT_TRANSCRIPT_CACHE_DIR = Path("output/transcript_cache")


def compute_source_file_hash(input_path: Union[str, Path]) -> Optional[str]:
    """Compute full 64-character SHA-256 digest of input file bytes using chunked streaming."""
    try:
        p = Path(input_path).expanduser().resolve()
        if p.is_file() and p.stat().st_size > 0:
            hasher = hashlib.sha256()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    hasher.update(chunk)
            return hasher.hexdigest()
    except Exception as e:
        logger.debug("Could not compute source file hash for '%s': %s", input_path, e)
    return None


def get_cached_audio_transcript(
    audio_path: Union[str, Path],
    audio_sha: Optional[str] = None,
    cache_dir: Optional[Union[str, Path]] = None,
) -> Optional[str]:
    """Retrieve previously cached transcript for an audio file if available."""
    if audio_sha:
        sha = audio_sha
    elif isinstance(audio_path, str) and len(audio_path) == 64 and all(c in "0123456789abcdefABCDEF" for c in audio_path) and not Path(audio_path).is_file():
        sha = audio_path.lower()
    else:
        sha = compute_source_file_hash(audio_path)
    if not sha:
        return None

    c_dir = Path(cache_dir) if cache_dir else DEFAULT_TRANSCRIPT_CACHE_DIR
    cache_file = c_dir / f"{sha}.txt"
    if cache_file.is_file():
        try:
            content = cache_file.read_text(encoding="utf-8").strip()
            if len(content) > 20:
                logger.info(
                    f"Found cached audio transcript in {cache_file} ({len(content)} chars). "
                    f"Bypassing audio re-transcription."
                )
                return content
        except Exception as e:
            logger.warning(f"Failed reading cached audio transcript from {cache_file}: {e}")
    return None


def cache_audio_transcript(
    audio_path: Union[str, Path],
    transcript: str,
    audio_sha: Optional[str] = None,
    cache_dir: Optional[Union[str, Path]] = None,
) -> Optional[Path]:
    """Store raw audio transcript into cache directory keyed by audio SHA-256."""
    if not transcript or len(transcript.strip()) < 20:
        return None

    if audio_sha:
        sha = audio_sha
    elif isinstance(audio_path, str) and len(audio_path) == 64 and all(c in "0123456789abcdefABCDEF" for c in audio_path) and not Path(audio_path).is_file():
        sha = audio_path.lower()
    else:
        sha = compute_source_file_hash(audio_path)
    if not sha:
        return None

    c_dir = Path(cache_dir) if cache_dir else DEFAULT_TRANSCRIPT_CACHE_DIR
    try:
        c_dir.mkdir(parents=True, exist_ok=True)
        cache_file = c_dir / f"{sha}.txt"
        tmp_file = c_dir / f"{sha}.txt.tmp_{os.getpid()}_{uuid.uuid4().hex[:8]}"
        tmp_file.write_text(transcript.strip(), encoding="utf-8")
        tmp_file.replace(cache_file)
        logger.info(f"Cached audio transcript to {cache_file} ({len(transcript)} chars).")
        return cache_file
    except Exception as e:
        logger.warning(f"Failed writing audio transcript cache for {audio_path}: {e}")
        return None


def _is_notes_only_mode(metadata: Mapping[str, Any]) -> bool:
    if metadata.get("notes_mode") or metadata.get("is_notes"):
        return True
    if metadata.get("source_type") in ("copilot_notes", "meeting_notes", "notes"):
        return True
    return bool(metadata.get("outputs") == ["02", "03"] and not metadata.get("freeze_output_1"))


def _collect_prior_decision_hashes(out_p: Path, decision_data: Mapping[str, Any]) -> set[str]:
    prior_hashes = {
        decision_data.get("input_hash"),
        decision_data.get("source_file_hash"),
        decision_data.get("output_01_sha256"),
    }
    rcpt_file = out_p / "pipeline_execution_receipt.json"
    if rcpt_file.is_file():
        try:
            rcpt = json.loads(rcpt_file.read_text(encoding="utf-8"))
            prior_hashes.add(rcpt.get("source_evidence", {}).get("raw_source_sha256"))
            prior_hashes.add(rcpt.get("stages", {}).get("U0", {}).get("correlation", {}).get("input_digest"))
        except Exception:
            pass
    for p01 in out_p.glob("01_*_hitl_resolved_transcript.md"):
        if p01.is_file():
            prior_hashes.add(compute_source_file_hash(p01))
    return {h for h in prior_hashes if isinstance(h, str) and h}


def _should_skip_idempotency_check(metadata: Mapping[str, Any]) -> bool:
    if metadata.get("augmentation_mode", False) or metadata.get("force_rerun", False):
        return True
    return bool(metadata.get("regenerate") and not metadata.get("auto_regenerate_track1"))


def _build_expected_idempotent_paths(
    out_p: Path,
    metadata: Dict[str, Any],
) -> Optional[Dict[str, str]]:
    """Build and verify existence of expected artifact paths for an idempotent run."""
    from dual_pipeline.meeting_paths import (
        normalize_meeting_date,
        normalize_time_str,
        sanitize_filename_slug,
    )

    raw_name = metadata.get("meeting_name") or metadata.get("title") or "Meeting"
    name = metadata.get("standardized_slug") or sanitize_filename_slug(raw_name)
    date = metadata.get("date_filename")
    if not date or not re.match(r"^\d{2}-\d{2}-\d{4}$", str(date)):
        date = normalize_meeting_date(metadata.get("date"))[0]
    t_str = metadata.get("time_filename")
    if not t_str and out_p.exists():
        cand = list(out_p.glob(f"03_{name}_{date}_*_canonical_signal_projection.json"))
        if not cand:
            cand = list(out_p.glob(f"**/03_{name}_{date}_*_canonical_signal_projection.json"))
        if not cand:
            cand = list(out_p.glob("03_*_canonical_signal_projection.json"))
        if not cand:
            cand = list(out_p.glob("**/03_*_canonical_signal_projection.json"))
        if cand:
            m = re.match(rf"^03_{re.escape(name)}_{re.escape(date)}_(.+?)_canonical_signal_projection\.json$", cand[0].name)
            if m:
                t_str = m.group(1)
                out_p = cand[0].parent
            else:
                m2 = re.match(r"^03_(.+?)_canonical_signal_projection\.json$", cand[0].name)
                if m2:
                    out_p = cand[0].parent
                    t_str = m2.group(1).split("_")[-1]
    if not t_str:
        t_str = normalize_time_str(metadata.get("time"))
    metadata["time_filename"] = t_str
    base = f"{name}_{date}_{t_str}"

    is_notes_mode = _is_notes_only_mode(metadata)
    is_out3 = bool(metadata.get("output3_only") or metadata.get("outputs") == ["03"] or metadata.get("is_document"))

    expected_paths = {
        "01_transcript": "" if is_notes_mode or is_out3 else str(out_p / f"01_{base}_hitl_resolved_transcript.md"),
        "02_recap": "" if is_out3 else str(out_p / f"02_{base}_executive_recap.md"),
        "03_graph": str(out_p / f"03_{base}_canonical_signal_projection.json"),
        "source_notes": str(out_p / f"{base}_source.txt") if is_notes_mode else "",
        "qa_gates_report": str(out_p / "qa_gates_report.json"),
        "llm_judge_evaluation": str(out_p / "llm_judge_evaluation.json"),
        "final_release_decision": str(out_p / "final_release_decision.json"),
    }

    # Check that core artifacts actually exist on disk
    req_files = [expected_paths["03_graph"], expected_paths["final_release_decision"]]
    if not is_out3:
        req_files.append(expected_paths["02_recap"])
    if not is_notes_mode and not is_out3:
        req_files.append(expected_paths["01_transcript"])
    elif expected_paths["source_notes"]:
        req_files.append(expected_paths["source_notes"])

    if all(Path(p).is_file() for p in req_files):
        return expected_paths
    return None


def compute_prompt_template_hash(prompt_path: Optional[Union[str, Path]] = None) -> str:
    """Compute SHA-256 digest of RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md or specified prompt path."""
    try:
        if prompt_path:
            p = Path(prompt_path).resolve()
            if p.is_file():
                return hashlib.sha256(p.read_bytes()).hexdigest()
        default_rel = Path("config/prompts/synthesis/RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md")
        cur = Path.cwd().resolve()
        for root in [cur] + list(cur.parents):
            cand = root / default_rel
            if cand.is_file():
                return hashlib.sha256(cand.read_bytes()).hexdigest()
    except Exception as e:
        logger.debug("Failed computing prompt template hash: %s", e)
    return "default_prompt_v5_7"


def _is_idempotency_cache_valid(metadata: Mapping[str, Any], decision_data: Mapping[str, Any]) -> bool:
    """Validates multi-factor dependencies (Wave 5 Alignment Invariant)."""
    if "attendees" in metadata or "attendee_roster_hash" in metadata:
        from dual_pipeline.l0.runtime_gate_preservation import compute_roster_hash
        current_roster_hash = metadata.get("attendee_roster_hash") or compute_roster_hash(metadata.get("attendees"))
        prior_roster_hash = decision_data.get("attendee_roster_hash")
        if prior_roster_hash and prior_roster_hash != current_roster_hash:
            logger.warning("Idempotency cache invalidated: Attendee roster changed.")
            return False

    if "context_hash" in metadata or "context" in metadata:
        from dual_pipeline.l0.runtime_gate_preservation import compute_context_hash
        current_ctx_hash = metadata.get("context_hash") or compute_context_hash(metadata.get("context"))
        prior_ctx_hash = decision_data.get("context_hash")
        if prior_ctx_hash and prior_ctx_hash != current_ctx_hash:
            logger.warning("Idempotency cache invalidated: Execution context changed.")
            return False

    for key, log_name in (
        ("policy_snapshot_digest", "Policy snapshot"),
        ("evaluator_version", "Evaluator version"),
        ("output_schema_version", "Output schema version"),
    ):
        if key in metadata:
            prior_val = decision_data.get(key)
            if prior_val and prior_val != metadata.get(key):
                logger.warning(f"Idempotency cache invalidated: {log_name} changed.")
                return False

    if metadata.get("strict_binding", False):
        required_bindings = ["policy_snapshot_digest", "prompt_template_hash", "attendee_roster_hash"]
        if any(not decision_data.get(k) for k in required_bindings):
            logger.warning("Idempotency cache invalidated: Legacy unbound result missing cryptographic bindings.")
            return False

    return True


def check_idempotent_meeting_run(
    output_dir: Union[str, Path],
    metadata: Dict[str, Any],
    source_file_hash: Optional[str] = None,
    content_hash: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    """Check if output_dir already contains a PASS final release matching input hashes.

    Returns the dictionary of expected output paths if an idempotent match is found,
    or None if execution should proceed.
    """
    if _should_skip_idempotency_check(metadata):
        return None

    out_p = Path(output_dir)
    dec_file = out_p / "final_release_decision.json"
    if not dec_file.is_file():
        nested_decs = sorted(list(out_p.glob("**/final_release_decision.json")), key=lambda p: len(p.parts))
        if not nested_decs:
            return None
        dec_file = nested_decs[0]
        out_p = dec_file.parent

    try:
        decision_data = json.loads(dec_file.read_text(encoding="utf-8"))
        if decision_data.get("status") != "PASS":
            return None

        prior_prompt_hash = decision_data.get("prompt_template_hash")
        expected_prompt_hash = compute_prompt_template_hash(metadata.get("prompt_path"))
        if prior_prompt_hash and prior_prompt_hash != expected_prompt_hash:
            logger.warning(
                "Idempotency cache invalidated: Prompt template version changed "
                "(prior=%s, current=%s). Proceeding with fresh synthesis.",
                prior_prompt_hash[:8], expected_prompt_hash[:8],
            )
            return None

        prior_hashes = _collect_prior_decision_hashes(out_p, decision_data)
        current_hashes = {source_file_hash, content_hash} - {None, ""}
        matching_hashes = prior_hashes & current_hashes
        if not matching_hashes or not _is_idempotency_cache_valid(metadata, decision_data):
            return None

        expected_paths = _build_expected_idempotent_paths(out_p, metadata)
        if expected_paths:
            matched_hash = matching_hashes.pop()
            logger.warning(
                f"Idempotent run detected: Input hash {matched_hash} matches prior "
                f"successful run in {output_dir}. Skipping execution to prevent token waste."
            )
            return expected_paths
    except Exception as e:
        logger.debug(f"Idempotency check failed in {output_dir}: {e}")

    return None
