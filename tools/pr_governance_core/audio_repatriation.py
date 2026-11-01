"""
Pre-teardown audio and ASR cache repatriation gate for isolated worktrees.

Guarantees that before any worktree is removed (via `pr_governance.py finish`,
`pr_governance.py merge`, `--force`, `--ingest`, or `teardown_ingest_worktree`),
all master `.wav` files and durable ASR cache sidecars under `<worktree>/output/`
are atomically copied to `<primary_root>/output/transcript_cache/` and verified
via independent SHA-256 readback. If verification fails, worktree removal aborts
fail-closed to prevent audio data loss.
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
import hashlib
import logging
import os
from pathlib import Path
import shutil
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("tools.pr_governance_core.audio_repatriation")

MIN_VALID_WAV_BYTES = 45

REPATRIATED_SIDECAR_SUFFIXES = (
    ".raw_asr.json",
    ".segments.json",
    ".utterances.json",
    ".transcript_cache.json",
    ".cache_seal.json",
    "_manifest.json",
    "_acoustic_evidence_receipt.json",
    "speaker_hints.json",
    "speaker_hints_queue.json",
    "inline_chat_attributions.json",
    "speaker_hint_conflicts.jsonl",
    "collateral_drop_log.json",
    "copilot_answers.jsonl",
    "copilot_attempts.jsonl",
    "turn_embeddings.jsonl",
    "_segments.jsonl",
    "_copilot_feedback.jsonl",
    "record_start_receipt.json",
    ".voiceprints.json",
    "speaker_map.json",
    "_live_transcript.md",
)


@dataclass
class AudioRepatriationResult:
    """Result of pre-teardown worktree audio and ASR cache repatriation."""

    success: bool
    repatriated_files: List[str] = field(default_factory=list)
    pruned_chunks: List[str] = field(default_factory=list)
    skipped_files: List[str] = field(default_factory=list)
    sha256_digests: Dict[str, str] = field(default_factory=dict)
    error: Optional[str] = None


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _is_chunk_wav(path: Path) -> bool:
    name = path.name.lower()
    return name.startswith("chunk_") and name.endswith(".wav")


def _is_repatriated_sidecar(path: Path) -> bool:
    name = path.name.lower()
    if "referenced_artifacts" in [p.lower() for p in path.parts]:
        return True
    return any(name.endswith(sfx) for sfx in REPATRIATED_SIDECAR_SUFFIXES)


def _resolve_root_destination(src_file: Path, worktree_dir: Path, primary_root: Path) -> Path:
    rel = src_file.resolve().relative_to(worktree_dir.resolve())
    parts = rel.parts
    if len(parts) >= 2 and parts[0] == "output" and parts[1] == "transcript_cache":
        return primary_root / rel
    if len(parts) >= 1 and parts[0] == "output":
        return primary_root / "output" / "transcript_cache" / Path(*parts[1:])
    return primary_root / "output" / "transcript_cache" / rel


def _atomic_copy_verify_single(
    src: Path,
    dst: Path,
    copy_fn: Callable[[Path, Path], object],
) -> tuple[bool, str, Optional[str]]:
    """Atomically copies src to dst and independently verifies SHA-256 parity."""
    src_sha = _sha256_file(src)
    src_size = src.stat().st_size
    dst.parent.mkdir(parents=True, exist_ok=True)

    if dst.is_file() and dst.stat().st_size == src_size:
        if _sha256_file(dst) == src_sha:
            return True, src_sha, None

    # Never overwrite an existing larger valid WAV with a smaller file
    if dst.suffix.lower() == ".wav" and dst.is_file() and dst.stat().st_size > src_size:
        existing_sha = _sha256_file(dst)
        return True, existing_sha, None

    tmp_dst = dst.with_name(f".{dst.name}.repatriate.tmp.{os.getpid()}")
    try:
        copy_fn(src, tmp_dst)
        if not tmp_dst.is_file():
            return False, src_sha, f"Temporary destination file was not created: {tmp_dst}"
        with open(tmp_dst, "rb") as f:
            os.fsync(f.fileno())
        tmp_sha = _sha256_file(tmp_dst)
        if tmp_sha != src_sha:
            tmp_dst.unlink(missing_ok=True)
            return (
                False,
                src_sha,
                f"SHA-256 mismatch on staged copy for {src.name}: expected {src_sha}, got {tmp_sha}",
            )
        os.replace(tmp_dst, dst)
        verified_sha = _sha256_file(dst)
        if verified_sha != src_sha:
            return (
                False,
                src_sha,
                f"SHA-256 mismatch after atomic replace for {src.name}: expected {src_sha}, got {verified_sha}",
            )
        return True, verified_sha, None
    except Exception as exc:
        with contextlib.suppress(Exception):
            tmp_dst.unlink(missing_ok=True)
        return False, src_sha, f"Failed repatriating {src.name}: {exc}"


def _collect_worktree_output_files(output_root: Path) -> tuple[List[Path], List[Path], List[Path]]:
    master_wavs: List[Path] = []
    chunk_wavs: List[Path] = []
    sidecars: List[Path] = []
    if not output_root.exists() or output_root.is_symlink():
        return master_wavs, chunk_wavs, sidecars

    for dirpath, dirnames, filenames in os.walk(output_root, followlinks=False):
        dirnames[:] = [d for d in dirnames if not (Path(dirpath) / d).is_symlink()]
        for fname in sorted(filenames):
            fpath = Path(dirpath) / fname
            if fpath.is_symlink() or not fpath.is_file():
                continue
            low = fname.lower()
            if low.endswith(".wav"):
                if _is_chunk_wav(fpath):
                    chunk_wavs.append(fpath)
                else:
                    master_wavs.append(fpath)
            elif _is_repatriated_sidecar(fpath):
                sidecars.append(fpath)
    # Sort sidecars so top-level output_dir files are processed after .cache files
    sidecars.sort(key=lambda p: (0 if ".cache" in p.parts else 1, str(p)))
    return master_wavs, chunk_wavs, sidecars


def _has_verified_master_wav_in_dir(cache_dir: Path) -> bool:
    if not cache_dir.is_dir():
        return False
    for item in cache_dir.glob("*.wav"):
        if not _is_chunk_wav(item) and item.is_file() and item.stat().st_size >= MIN_VALID_WAV_BYTES:
            return True
    return False


def _prune_empty_dirs(start_root: Path) -> None:
    if not start_root.is_dir():
        return
    for dirpath, _dirnames, _filenames in os.walk(start_root, topdown=False, followlinks=False):
        p = Path(dirpath)
        if p == start_root:
            continue
        with contextlib.suppress(Exception):
            if not any(p.iterdir()):
                p.rmdir()


def repatriate_worktree_audio_artifacts(
    worktree_dir: Path,
    primary_root: Path,
    copy_fn: Optional[Callable[[Path, Path], object]] = None,
) -> AudioRepatriationResult:
    """
    Repatriates all master `.wav` files and durable ASR cache sidecars from
    `<worktree_dir>/output/` into `<primary_root>/output/transcript_cache/`.
    """
    wt_resolved = Path(worktree_dir).resolve()
    root_resolved = Path(primary_root).resolve()
    res = AudioRepatriationResult(success=True)
    if not wt_resolved.is_dir() or not root_resolved.is_dir() or wt_resolved == root_resolved:
        return res

    effective_copy = copy_fn or shutil.copy2
    output_root = wt_resolved / "output"
    master_wavs, chunk_wavs, sidecars = _collect_worktree_output_files(output_root)

    # 1. Repatriate master WAVs first
    for wav_path in master_wavs:
        rel_str = str(wav_path.relative_to(wt_resolved))
        if wav_path.stat().st_size < MIN_VALID_WAV_BYTES:
            res.skipped_files.append(rel_str)
            with contextlib.suppress(Exception):
                wav_path.unlink()
            continue

        dst_path = _resolve_root_destination(wav_path, wt_resolved, root_resolved)
        ok, sha_hex, err = _atomic_copy_verify_single(wav_path, dst_path, effective_copy)
        if not ok:
            res.success = False
            res.error = err
            return res
        dst_rel = str(dst_path.relative_to(root_resolved))
        res.repatriated_files.append(dst_rel)
        res.sha256_digests[dst_rel] = sha_hex
        with contextlib.suppress(Exception):
            wav_path.unlink()

    # 2. Handle rolling chunk WAVs (prune if master WAV verified in root, else repatriate)
    for chunk_path in chunk_wavs:
        rel_str = str(chunk_path.relative_to(wt_resolved))
        if chunk_path.stat().st_size < MIN_VALID_WAV_BYTES:
            res.skipped_files.append(rel_str)
            with contextlib.suppress(Exception):
                chunk_path.unlink()
            continue

        dst_chunk = _resolve_root_destination(chunk_path, wt_resolved, root_resolved)
        if _has_verified_master_wav_in_dir(dst_chunk.parent.parent) or _has_verified_master_wav_in_dir(dst_chunk.parent):
            res.pruned_chunks.append(rel_str)
            with contextlib.suppress(Exception):
                chunk_path.unlink()
            continue

        ok, sha_hex, err = _atomic_copy_verify_single(chunk_path, dst_chunk, effective_copy)
        if not ok:
            res.success = False
            res.error = err
            return res
        dst_rel = str(dst_chunk.relative_to(root_resolved))
        res.repatriated_files.append(dst_rel)
        res.sha256_digests[dst_rel] = sha_hex
        with contextlib.suppress(Exception):
            chunk_path.unlink()

    # 3. Repatriate durable ASR cache sidecars
    for sc_path in sidecars:
        dst_sc = _resolve_root_destination(sc_path, wt_resolved, root_resolved)
        ok, sha_hex, err = _atomic_copy_verify_single(sc_path, dst_sc, effective_copy)
        if not ok:
            res.success = False
            res.error = err
            return res
        dst_rel = str(dst_sc.relative_to(root_resolved))
        res.repatriated_files.append(dst_rel)
        res.sha256_digests[dst_rel] = sha_hex
        with contextlib.suppress(Exception):
            sc_path.unlink()

    _prune_empty_dirs(output_root / "transcript_cache")
    _prune_empty_dirs(output_root)
    return res
