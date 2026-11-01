"""
dual_pipeline/remediation_core/stage_sealer.py

Stage-isolated seal persistence and candidate artifact seeding helpers.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def validate_artifact_content_for_sealing(artifact_key: str, content: str) -> Tuple[bool, Optional[str]]:
    """Validates candidate artifact content before allowing stage checkpoint sealing."""
    if not isinstance(content, str) or not content.strip():
        return False, "EMPTY_CONTENT: Cannot seal empty or whitespace content"
    if artifact_key == "output_3":
        try:
            parsed = json.loads(content)
            if not isinstance(parsed, (dict, list)):
                return False, "INVALID_JSON_STRUCTURE: Output 3 must be a JSON object or array"
        except Exception as e:
            return False, f"MALFORMED_JSON: Output 3 content is not valid JSON ({e})"
    elif artifact_key in ("output_1", "output_2"):
        if len(content.strip()) < 20:
            return False, f"TRUNCATED_CONTENT: {artifact_key} content is suspiciously truncated"
    return True, None


def persist_stage_checkpoint_seal(
    checkpoint_dir: Path | str,
    artifact_key: str,
    content: str,
    force: bool = False,
) -> None:
    """Persists artifact content and records isolated stage seal in stage_seals.json."""
    ok, err = validate_artifact_content_for_sealing(artifact_key, content)
    if not ok:
        raise ValueError(f"CANNOT_SEAL_INVALID_ARTIFACT: {err}")

    ckpt_p = Path(checkpoint_dir)
    ckpt_p.mkdir(parents=True, exist_ok=True)
    ext = ".json" if artifact_key == "output_3" else ".md"
    ckpt_file = ckpt_p / f"{artifact_key}.ckpt{ext}"

    content_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    seal_file = ckpt_p / "stage_seals.json"
    seals = {}
    if seal_file.is_file():
        try:
            seals = json.loads(seal_file.read_text(encoding="utf-8"))
            if not force and seals.get(f"{artifact_key}_seal") and seals.get(f"{artifact_key}_pass"):
                prev_sha = seals.get(f"{artifact_key}_sha256")
                if prev_sha and prev_sha != content_sha:
                    logger.info(
                        "Updating sealed artifact '%s' in %s with validated content.",
                        artifact_key, ckpt_p
                    )
        except Exception:
            seals = {}

    if ckpt_p.name.startswith("attempt_") or not (ckpt_p / "attempts").is_dir():
        ckpt_file.write_text(content, encoding="utf-8")
    seals[f"{artifact_key}_seal"] = True
    seals[f"{artifact_key}_pass"] = True
    seals[f"{artifact_key}_sha256"] = content_sha
    seal_file.write_text(json.dumps(seals, indent=2), encoding="utf-8")


def verify_stage_checkpoint_seal(
    checkpoint_dir: Path | str,
    artifact_key: str,
) -> Tuple[bool, Optional[str]]:
    """Verifies that an artifact checkpoint seal is intact, valid, and matches its stored SHA-256."""
    ckpt_p = Path(checkpoint_dir)
    seal_file = ckpt_p / "stage_seals.json"
    if not seal_file.is_file():
        return False, "NO_SEAL_FILE"
    try:
        seals = json.loads(seal_file.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"CORRUPT_SEAL_FILE: {exc}"
    if not seals.get(f"{artifact_key}_seal"):
        return False, "NOT_SEALED"
    if not seals.get(f"{artifact_key}_pass"):
        return False, "SEAL_NOT_PASSED"
    expected_sha = seals.get(f"{artifact_key}_sha256")
    if not expected_sha:
        return False, "MISSING_SEAL_DIGEST"
    ext = ".json" if artifact_key == "output_3" else ".md"
    ckpt_file = ckpt_p / f"{artifact_key}.ckpt{ext}"
    if not ckpt_file.is_file():
        return False, "CHECKPOINT_FILE_MISSING"
    raw_bytes = ckpt_file.read_bytes()
    actual_sha = hashlib.sha256(raw_bytes).hexdigest()
    if actual_sha != expected_sha:
        return False, f"SEAL_DIGEST_MISMATCH: expected {expected_sha[:8]}, got {actual_sha[:8]}"
    content_str = raw_bytes.decode("utf-8", errors="replace")
    ok, err = validate_artifact_content_for_sealing(artifact_key, content_str)
    if not ok:
        return False, f"INVALID_SEALED_CONTENT: {err}"
    return True, None


def build_slug_candidates(meeting_name: Optional[str]) -> List[str]:
    """Build candidate slug variations for matching artifact filenames."""
    if not meeting_name:
        return []
    slug_candidates: List[str] = []
    slug = re.sub(r"[^\w\-_]", "_", meeting_name)
    slug_candidates.append(slug.lower())
    try:
        from dual_pipeline.meeting_paths import sanitize_filename_slug, standardize_meeting_slug

        std = standardize_meeting_slug(meeting_name, "")
        if std:
            slug_candidates.append(std.lower())
        san = sanitize_filename_slug(meeting_name)
        if san:
            slug_candidates.append(san.lower())
    except Exception:
        pass
    return slug_candidates


def _is_cache_invalidated_by_input_or_attachments(
    output_dir: Path,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """Checks whether prior artifacts in output_dir should be invalidated due to changed inputs or attachments."""
    if not metadata:
        return False
    prior_meta_file = output_dir / "metadata.json"
    if not prior_meta_file.is_file():
        return False
    try:
        prior_meta = json.loads(prior_meta_file.read_text(encoding="utf-8"))
    except Exception:
        return False

    curr_input_hash = metadata.get("source_file_hash") or metadata.get("input_hash")
    prior_input_hash = prior_meta.get("source_file_hash") or prior_meta.get("input_hash")
    if curr_input_hash and prior_input_hash and curr_input_hash != prior_input_hash:
        logger.info("Invalidating prior artifacts in %s: input hash changed", output_dir)
        return True

    curr_att_count = len(metadata.get("attachments") or [])
    prior_att_count = len(prior_meta.get("attachments") or [])
    if curr_att_count != prior_att_count:
        logger.info(
            "Invalidating prior artifacts in %s: attachment count changed (%d != %d)",
            output_dir, curr_att_count, prior_att_count,
        )
        return True
    return False


def seed_single_artifact_from_dir(
    output_dir: Path,
    slug_candidates: List[str],
    art_key: str,
    pref: str,
    suff: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Tuple[str, str]]:
    """Inspects output_dir for a prior passing artifact and returns (art_key, content) if found."""
    if metadata and _is_cache_invalidated_by_input_or_attachments(output_dir, metadata):
        return None
    from .artifact_validator import _check_prior_decision

    ok, _, _ = _check_prior_decision(output_dir, art_key)
    if not ok:
        return None
    for fname in output_dir.iterdir():
        if "live_transcript" in fname.name.lower():
            continue
        if slug_candidates and not any(cand in fname.name.lower() for cand in slug_candidates):
            continue
        if fname.name.startswith(pref) and fname.name.endswith(suff):
            try:
                content = fname.read_text(encoding="utf-8")
                return (art_key, content)
            except Exception as e:
                logger.warning(f"Failed to read {fname.name}: {e}")
                return None


def _heal_frozen_artifact(
    state_machine: Any, p: Path, art_key: str, content: str, meta: Dict[str, Any]
) -> str:
    try:
        from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
        healed = content
        if art_key == "output_1":
            healed, _ = DeterministicHealer.heal_output_1(content, metadata=meta)
        elif art_key == "output_2":
            out_1 = state_machine.get_frozen_content("output_1") if hasattr(state_machine, "get_frozen_content") else None
            healed, _ = DeterministicHealer.heal_output_2(content, metadata=meta, output_1=out_1)
        if healed != content:
            persist_stage_checkpoint_seal(p, art_key, healed, force=True)
        return healed
    except Exception:
        return content


def seed_selective_regeneration(
    state_machine: Any,
    output_dir: str | Path,
    regenerate_spec: Optional[str],
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Seeds and freezes artifacts excluded from the --regenerate deliverable list.
    Validates each frozen candidate through validate_frozen_artifact_gate.
    Returns a dictionary of {artifact_key: FrozenArtifactValidationResult}.
    """
    from .artifact_validator import (
        ALL_OUTPUTS,
        parse_regenerate_spec,
        validate_frozen_artifact_gate,
    )

    meta = metadata or {}
    regen_set = parse_regenerate_spec(regenerate_spec)
    frozen_to_seed = set(ALL_OUTPUTS - regen_set)
    if meta.get("skip_output_1") or meta.get("notes_mode") or meta.get("is_notes"):
        frozen_to_seed.discard("output_1")

    p = Path(output_dir)
    results = {}

    for art_key in sorted(list(frozen_to_seed)):
        val_res = validate_frozen_artifact_gate(p, art_key, meta)
        results[art_key] = val_res
        if not val_res.valid:
            raise ValueError(
                f"Frozen artifact validation failed for '{art_key}': {val_res.reason}"
            )
        if val_res.content:
            healed_content = _heal_frozen_artifact(state_machine, p, art_key, val_res.content, meta)
            state_machine.freeze_artifact(art_key, healed_content)
            state_machine.permanently_frozen.add(art_key)
            state_machine.passing_stages.add(art_key)
            logger.info(
                f"Selective regeneration: Froze '{art_key}' from "
                f"{val_res.file_path.name if val_res.file_path else 'prior run'}."
            )

    return results


def _write_artifact_file(file_path_str: str, content: str) -> None:
    if "/" in file_path_str and Path(file_path_str).is_file():
        try:
            Path(file_path_str).write_text(content, encoding="utf-8")
        except Exception:
            pass


def sync_candidate_to_artifacts_and_disk(
    key: str,
    pat: str,
    content: Optional[str],
    artifacts: Optional[Dict[str, str]],
    ckpt_dir: Optional[Path | str],
) -> None:
    """Synchronizes candidate content to artifacts map, files on disk, and stage checkpoint seal."""
    if not content:
        return
    if artifacts is not None:
        artifacts[key] = content
        for ak in list(artifacts.keys()):
            if pat in ak:
                artifacts[ak] = content
                _write_artifact_file(ak, content)
    if ckpt_dir is not None:
        try:
            persist_stage_checkpoint_seal(ckpt_dir, key, content, force=True)
        except Exception:
            pass


def apply_terminal_rollback(state_machine: Any, artifacts: Optional[Dict[str, str]]) -> None:
    """Applies terminal attempt rollback for outputs 1-3 to the best candidate content."""
    for key, pat in (("output_1", "01_"), ("output_2", "02_"), ("output_3", "03_")):
        best = state_machine.get_best_candidate_content(key)
        if best:
            logger.info("Terminal attempt %s: rolling back %s to best candidate.", state_machine.current_attempt, key)
            sync_candidate_to_artifacts_and_disk(key, pat, best, artifacts, state_machine.checkpoint_dir)


