"""
dual_pipeline/transcript_pipeline_core/cache_seal.py

Cryptographic seal creation and verification for audio transcription sidecars.
Guarantees immutability and parity across:
  - <stem>.transcript_cache.json
  - <stem>.segments.json
  - <stem>.utterances.json
Fails closed with CacheIntegrityViolationError upon unauthorized edits or turn-count divergence.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from dual_pipeline.exceptions import CacheIntegrityViolationError

logger = logging.getLogger(__name__)


def _compute_sha256(path: Union[str, Path]) -> str:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"File not found for hash calculation: {path}")
    return hashlib.sha256(p.read_bytes()).hexdigest()


def find_associated_sidecars(
    target_file: Union[str, Path],
) -> Tuple[Path, Optional[Path], Optional[Path], Optional[Path]]:
    """
    Given a path to .segments.json, .transcript_cache.json, or .utterances.json,
    locates all companion sidecars and the cache seal file in the same directory.
    Returns (structured_file, cache_file, utterance_file, seal_file).
    """
    p = Path(target_file)
    c_dir = p.parent
    stem = p.name

    for sfx in (".segments.json", ".transcript_cache.json", ".utterances.json", ".cache_seal.json"):
        if stem.endswith(sfx):
            stem = stem[: -len(sfx)]
            break

    structured_file = c_dir / f"{stem}.segments.json"
    cache_file = c_dir / f"{stem}.transcript_cache.json"
    utterance_file = c_dir / f"{stem}.utterances.json"
    seal_file = c_dir / f"{stem}.cache_seal.json"

    c_file = cache_file if cache_file.is_file() else None
    u_file = utterance_file if utterance_file.is_file() else None
    s_file = seal_file if seal_file.is_file() else None

    return structured_file, c_file, u_file, s_file


def _resolve_optional_sidecar_hash(s_path: Path, opt_path: Optional[Union[str, Path]], suffix: str) -> Tuple[Path, str]:
    p = Path(opt_path) if opt_path else s_path.with_name(s_path.name.replace(".segments.json", suffix))
    return p, (_compute_sha256(p) if p.is_file() else "")


def create_cache_seal(
    structured_file: Union[str, Path],
    cache_file: Optional[Union[str, Path]] = None,
    utterance_file: Optional[Union[str, Path]] = None,
    audio_sha256: str = "",
    cache_key: str = "",
    voiceprints_file: Optional[Union[str, Path]] = None,
    cluster_map_file: Optional[Union[str, Path]] = None,
    cache_tier: str = "AUTHORITATIVE_MASTER",
) -> Path:
    """
    Creates and writes <stem>.cache_seal.json capturing cryptographic digests and counts.
    """
    s_path = Path(structured_file)
    if not s_path.is_file():
        raise FileNotFoundError(f"Structured segments file not found: {structured_file}")

    s_data = json.loads(s_path.read_text(encoding="utf-8"))
    turn_count = len(s_data) if isinstance(s_data, list) else len(s_data.get("segments", []))

    c_path, c_sha = _resolve_optional_sidecar_hash(s_path, cache_file, ".transcript_cache.json")
    u_path, u_sha = _resolve_optional_sidecar_hash(s_path, utterance_file, ".utterances.json")
    u_count = 0
    if u_path.is_file():
        u_data = json.loads(u_path.read_text(encoding="utf-8"))
        u_count = len(u_data) if isinstance(u_data, list) else len(u_data.get("utterances", []))

    vp_path, vp_sha = _resolve_optional_sidecar_hash(s_path, voiceprints_file, ".voiceprints.json")
    cmap_path, cmap_sha = _resolve_optional_sidecar_hash(s_path, cluster_map_file, ".cluster_map.json")

    seal_file = s_path.with_name(s_path.name.replace(".segments.json", ".cache_seal.json"))
    seal_payload: Dict[str, Any] = {
        "schema_version": "1.2.0",
        "sealed": True,
        "cache_tier": cache_tier,
        "cache_key": cache_key,
        "audio_sha256": audio_sha256,
        "segments_file": s_path.name,
        "segments_sha256": _compute_sha256(s_path),
        "turn_count": turn_count,
        "transcript_cache_file": c_path.name if c_path.is_file() else "",
        "transcript_cache_sha256": c_sha,
        "utterances_file": u_path.name if u_path.is_file() else "",
        "utterances_sha256": u_sha,
        "utterance_count": u_count,
    }
    if vp_path.is_file():
        seal_payload["voiceprints_file"] = vp_path.name
        seal_payload["voiceprints_sha256"] = vp_sha
    if cmap_path.is_file():
        seal_payload["cluster_map_file"] = cmap_path.name
        seal_payload["cluster_map_sha256"] = cmap_sha

    tmp_file = seal_file.with_suffix(".cache_seal.json.tmp")
    tmp_file.write_text(json.dumps(seal_payload, indent=2), encoding="utf-8")
    tmp_file.replace(seal_file)
    logger.info("Emitted cryptographic transcript cache seal: %s", seal_file)
    return seal_file


def verify_cache_seal(
    structured_file: Union[str, Path],
    cache_file: Optional[Union[str, Path]] = None,
    utterance_file: Optional[Union[str, Path]] = None,
    seal_file: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """
    Verifies cryptographic seal and parity between structured turns, transcript cache, and raw utterances.
    Fails closed with CacheIntegrityViolationError upon any tampering or turn divergence.
    """
    s_path = Path(structured_file)
    if not s_path.is_file():
        raise FileNotFoundError(f"Structured segments file not found: {structured_file}")

    s_file, c_file, u_file, detected_seal = find_associated_sidecars(s_path)
    active_seal = Path(seal_file) if seal_file and Path(seal_file).is_file() else detected_seal
    active_cache = Path(cache_file) if cache_file and Path(cache_file).is_file() else c_file
    active_utt = Path(utterance_file) if utterance_file and Path(utterance_file).is_file() else u_file

    structured_turns = json.loads(s_path.read_text(encoding="utf-8"))
    if not isinstance(structured_turns, list):
        raise CacheIntegrityViolationError(f"Structured segments in {s_path} must be a JSON array.")

    actual_seg_sha = _compute_sha256(s_path)

    # 1. Verification against cryptographic seal if present
    if active_seal and active_seal.is_file():
        try:
            seal_data = json.loads(active_seal.read_text(encoding="utf-8"))
        except json.JSONDecodeError as jde:
            raise CacheIntegrityViolationError(f"Corrupted cache seal file {active_seal}: {jde}") from jde

        exp_seg_sha = seal_data.get("segments_sha256")
        if exp_seg_sha and actual_seg_sha != exp_seg_sha:
            raise CacheIntegrityViolationError(
                f"Cryptographic segments cache seal mismatch for {s_path.name}: "
                f"expected {exp_seg_sha}, got {actual_seg_sha}. Manual tampering detected."
            )

        if active_cache and active_cache.is_file() and seal_data.get("transcript_cache_sha256"):
            actual_c_sha = _compute_sha256(active_cache)
            if actual_c_sha != seal_data["transcript_cache_sha256"]:
                raise CacheIntegrityViolationError(
                    f"Cryptographic transcript cache seal mismatch for {active_cache.name}: "
                    f"expected {seal_data['transcript_cache_sha256']}, got {actual_c_sha}."
                )

        if active_utt and active_utt.is_file() and seal_data.get("utterances_sha256"):
            actual_u_sha = _compute_sha256(active_utt)
            if actual_u_sha != seal_data["utterances_sha256"]:
                raise CacheIntegrityViolationError(
                    f"Cryptographic utterances cache seal mismatch for {active_utt.name}: "
                    f"expected {seal_data['utterances_sha256']}, got {actual_u_sha}."
                )

        if seal_data.get("voiceprints_sha256"):
            vp_name = seal_data.get("voiceprints_file") or s_path.name.replace(".segments.json", ".voiceprints.json")
            vp_path = s_path.parent / vp_name
            if not vp_path.is_file():
                raise CacheIntegrityViolationError(
                    f"Cryptographic voiceprints sidecar missing: {vp_name} declared in seal but not found on disk."
                )
            actual_vp_sha = _compute_sha256(vp_path)
            if actual_vp_sha != seal_data["voiceprints_sha256"]:
                raise CacheIntegrityViolationError(
                    f"Cryptographic voiceprints cache seal mismatch for {vp_path.name}: "
                    f"expected {seal_data['voiceprints_sha256']}, got {actual_vp_sha}. Sidecar tampering detected."
                )

        if seal_data.get("cluster_map_sha256"):
            cmap_name = seal_data.get("cluster_map_file") or s_path.name.replace(".segments.json", ".cluster_map.json")
            cmap_path = s_path.parent / cmap_name
            if not cmap_path.is_file():
                raise CacheIntegrityViolationError(
                    f"Cryptographic cluster map sidecar missing: {cmap_name} declared in seal but not found on disk."
                )
            actual_cmap_sha = _compute_sha256(cmap_path)
            if actual_cmap_sha != seal_data["cluster_map_sha256"]:
                raise CacheIntegrityViolationError(
                    f"Cryptographic cluster map cache seal mismatch for {cmap_path.name}: "
                    f"expected {seal_data['cluster_map_sha256']}, got {actual_cmap_sha}. Sidecar tampering detected."
                )

        if seal_data.get("turn_count") is not None and len(structured_turns) != seal_data["turn_count"]:
            raise CacheIntegrityViolationError(
                f"Transcript cache turn count mismatch: {s_path.name} has {len(structured_turns)} turns, "
                f"expected {seal_data['turn_count']} from cryptographic seal."
            )

        seal_data["sealed"] = True
        return seal_data

    # 2. Structural parity verification when seal is absent but utterances exist
    if active_utt and active_utt.is_file():
        try:
            raw_utterances = json.loads(active_utt.read_text(encoding="utf-8"))
        except json.JSONDecodeError as jde:
            raise CacheIntegrityViolationError(f"Corrupted raw utterances file {active_utt}: {jde}") from jde

        if isinstance(raw_utterances, list):
            if len(structured_turns) != len(raw_utterances):
                raise CacheIntegrityViolationError(
                    f"Transcript cache turn-count divergence detected: structured turns ({len(structured_turns)}) "
                    f"diverges from raw acoustic utterances ({len(raw_utterances)}) in {s_path.name} without a cryptographic seal."
                )

            # Check for unauthenticated speaker relabeling of Unknown utterances
            for idx, (turn, utt) in enumerate(zip(structured_turns, raw_utterances)):
                raw_spk = str(utt.get("speaker") or utt.get("raw_speaker") or "").strip().lower()
                turn_spk = str(turn.get("speaker") or "").strip().lower()
                if raw_spk in ("unknown", "speaker unknown") and turn_spk not in ("unknown", "speaker unknown", "speaker"):
                    raise CacheIntegrityViolationError(
                        f"Unauthorized speaker attribution detected at turn {idx}: "
                        f"raw acoustic utterance was '{utt.get('speaker')}' but cached turn is '{turn.get('speaker')}' "
                        f"without a verified cryptographic seal."
                    )

    return {
        "segments_file": s_path.name,
        "segments_sha256": actual_seg_sha,
        "turn_count": len(structured_turns),
        "sealed": False,
    }
